"""Docker event listener + auto-restart."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from docker_sre_agent.config import AgentConfig
from docker_sre_agent.docker_client import get_client

logger = logging.getLogger(__name__)

TRIGGER_EVENTS = {"die", "oom"}
HEALTH_EVENT_PREFIX = "health_status: unhealthy"
RECONNECT_DELAY = 5  # seconds


@dataclass
class ContainerState:
    restart_timestamps: deque = field(default_factory=deque)
    consecutive_fails: int = 0
    stopped: bool = False


MAX_STATE_AGE = 86400  # 24 hours — evict states older than this


class Scanner:
    """Monitors Docker events and restarts failed containers."""

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
        if self.config.monitor.watch_containers:
            return name in self.config.monitor.watch_containers
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
            logger.warning(f"Rate limited (per-container): '{name}'")
            return True
        self._cleanup_window(self._global_timestamps)
        if len(self._global_timestamps) >= rc.max_global_per_hour:
            logger.warning(f"Rate limited (global): reached max")
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

    def _is_trigger_event(self, event: dict) -> bool:
        status = event.get("status", "")
        if status in TRIGGER_EVENTS:
            return True
        if status.startswith(HEALTH_EVENT_PREFIX):
            return True
        return False

    def _get_container_name(self, event: dict) -> str | None:
        attrs = event.get("Actor", {}).get("Attributes", {})
        return attrs.get("name")

    def _restart(self, name: str) -> bool:
        """Restart a container (synchronous, called via to_thread)."""
        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            logger.warning(f"Container '{name}' not found")
            return False
        try:
            logger.info(f"Restarting '{name}'...")
            container.restart(timeout=self.config.restart.timeout)
            logger.info(f"Container '{name}' restarted successfully")
            return True
        except Exception as e:
            logger.warning(f"restart() failed for '{name}': {e}")
        try:
            logger.info(f"Trying stop+start for '{name}'...")
            container.stop(timeout=self.config.restart.timeout)
            container.start()
            logger.info(f"Container '{name}' stop+start succeeded")
            return True
        except Exception as e:
            logger.error(f"stop+start failed for '{name}': {e}")
            return False

    def _handle_event(self, event: dict) -> None:
        if not self._is_trigger_event(event):
            return
        name = self._get_container_name(event)
        if not name or not self._should_monitor(name):
            return

        state = self._get_state(name)
        if state.stopped:
            return
        if self._is_rate_limited(name):
            return

        success = self._restart(name)
        if success:
            state.consecutive_fails = 0
        else:
            state.consecutive_fails += 1
            if state.consecutive_fails >= self.config.restart.max_consecutive_fails:
                state.stopped = True
                logger.error(
                    f"Container '{name}' failed {state.consecutive_fails} consecutive "
                    f"restarts, stopping auto-restart."
                )
        self._record_restart(name)

    def _cleanup_old_states(self) -> None:
        """Evict container states older than MAX_STATE_AGE."""
        now = time.time()
        if now - self._last_cleanup < 3600:  # run at most once per hour
            return
        self._last_cleanup = now

        cutoff = now - MAX_STATE_AGE
        to_remove = []
        for name, state in self._states.items():
            # Check if last restart was too long ago
            if state.restart_timestamps:
                last = state.restart_timestamps[-1]
                if last < cutoff:
                    to_remove.append(name)
            elif not state.stopped:
                # No restart history and not stopped — check if container still exists
                to_remove.append(name)

        for name in to_remove:
            del self._states[name]

        if to_remove:
            logger.debug(f"Cleaned up {len(to_remove)} old container states")

    def _event_loop(self) -> None:
        """Blocking event loop — runs in a thread."""
        while self._running:
            try:
                logger.info("Connecting to Docker events...")
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
                logger.exception(f"Docker events connection lost, reconnecting in {RECONNECT_DELAY}s...")
                time.sleep(RECONNECT_DELAY)
        logger.info("Event listener stopped")

    async def start(self) -> None:
        """Start the event listener in a background thread."""
        self._running = True
        logger.info("Event listener starting")
        await asyncio.to_thread(self._event_loop)

    async def stop(self) -> None:
        self._running = False
        logger.info("Event listener stopping")
