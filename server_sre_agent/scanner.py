"""Docker event listener + auto-restart."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from server_sre_agent.config import AgentConfig
from server_sre_agent.docker_client import get_client

logger = logging.getLogger(__name__)

TRIGGER_EVENTS = {"die", "oom"}
HEALTH_PREFIX = "health_status: unhealthy"
RECONNECT_DELAY = 5
MAX_STATE_AGE = 86400


@dataclass
class ContainerState:
    restart_timestamps: deque = field(default_factory=deque)
    consecutive_fails: int = 0
    stopped: bool = False


class Scanner:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = get_client()
        self._states: dict[str, ContainerState] = {}
        self._global_timestamps: deque = deque()
        self._running = False
        self._last_cleanup = time.time()

    def _should_monitor(self, name: str) -> bool:
        if name in self.config.monitor.exclude_containers:
            return False
        return True

    def _cleanup_window(self, timestamps: deque, window: float = 3600) -> None:
        cutoff = time.time() - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _is_rate_limited(self, name: str) -> bool:
        rc = self.config.restart
        state = self._get_state(name)
        self._cleanup_window(state.restart_timestamps)
        if len(state.restart_timestamps) >= rc.max_per_container_per_hour:
            return True
        self._cleanup_window(self._global_timestamps)
        if len(self._global_timestamps) >= rc.max_global_per_hour:
            return True
        return False

    def _record_restart(self, name: str) -> None:
        now = time.time()
        self._get_state(name).restart_timestamps.append(now)
        self._global_timestamps.append(now)

    def _get_state(self, name: str) -> ContainerState:
        if name not in self._states:
            self._states[name] = ContainerState()
        return self._states[name]

    def _restart(self, name: str) -> bool:
        try:
            container = self.client.containers.get(name)
        except Exception:
            return False
        try:
            container.restart(timeout=self.config.restart.timeout)
            logger.info(f"Restarted '{name}'")
            return True
        except Exception:
            pass
        try:
            container.stop(timeout=self.config.restart.timeout)
            container.start()
            logger.info(f"Stop+start '{name}' succeeded")
            return True
        except Exception as e:
            logger.error(f"Failed to restart '{name}': {e}")
            return False

    def _handle_event(self, event: dict) -> None:
        status = event.get("status", "")
        if status not in TRIGGER_EVENTS and not status.startswith(HEALTH_PREFIX):
            return
        name = event.get("Actor", {}).get("Attributes", {}).get("name")
        if not name or not self._should_monitor(name):
            return
        state = self._get_state(name)
        if state.stopped or self._is_rate_limited(name):
            return
        success = self._restart(name)
        if success:
            state.consecutive_fails = 0
        else:
            state.consecutive_fails += 1
            if state.consecutive_fails >= self.config.restart.max_consecutive_fails:
                state.stopped = True
                logger.error(f"'{name}' failed {state.consecutive_fails} times, stopping auto-restart")
        self._record_restart(name)

    def _cleanup_old_states(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        to_remove = [n for n, s in self._states.items()
                     if s.restart_timestamps and s.restart_timestamps[-1] < now - MAX_STATE_AGE]
        for n in to_remove:
            del self._states[n]

    def _event_loop(self) -> None:
        while self._running:
            try:
                for event in self.client.events(decode=True):
                    if not self._running:
                        break
                    try:
                        self._handle_event(event)
                        self._cleanup_old_states()
                    except Exception:
                        logger.exception("Error handling event")
            except Exception:
                if not self._running:
                    break
                logger.exception(f"Events lost, reconnecting in {RECONNECT_DELAY}s...")
                time.sleep(RECONNECT_DELAY)

    async def start(self) -> None:
        self._running = True
        await asyncio.to_thread(self._event_loop)

    async def stop(self) -> None:
        self._running = False
