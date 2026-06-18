"""Core agent: Docker event listener + restart handler + rate limiter."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import docker

from docker_sre_agent.config import AgentConfig

logger = logging.getLogger(__name__)

# Docker event types that indicate a problem
TRIGGER_EVENTS = {"die", "oom"}
HEALTH_EVENT_PREFIX = "health_status: unhealthy"


@dataclass
class ContainerState:
    """Per-container rate limiting state."""
    restart_timestamps: deque = field(default_factory=deque)
    consecutive_fails: int = 0
    stopped: bool = False  # True = max consecutive fails reached, needs manual intervention


class Agent:
    """Monitors Docker events and restarts failed containers."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client = docker.from_env()
        self._states: dict[str, ContainerState] = {}
        self._global_timestamps: deque = deque()
        self._running = False

    def _should_monitor(self, name: str) -> bool:
        """Check if a container should be monitored."""
        if name in self.config.monitor.exclude_containers:
            return False
        if self.config.monitor.watch_containers:
            return name in self.config.monitor.watch_containers
        return True

    def _cleanup_window(self, timestamps: deque, window: float = 3600) -> None:
        """Remove timestamps older than window seconds."""
        cutoff = time.time() - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _is_rate_limited(self, name: str) -> bool:
        """Check if a container is rate limited."""
        rc = self.config.restart

        # Per-container limit
        state = self._get_state(name)
        self._cleanup_window(state.restart_timestamps)
        if len(state.restart_timestamps) >= rc.max_per_container_per_hour:
            logger.warning(f"Rate limited (per-container): '{name}' reached {rc.max_per_container_per_hour}/hour")
            return True

        # Global limit
        self._cleanup_window(self._global_timestamps)
        if len(self._global_timestamps) >= rc.max_global_per_hour:
            logger.warning(f"Rate limited (global): reached {rc.max_global_per_hour}/hour")
            return True

        return False

    def _record_restart(self, name: str) -> None:
        """Record a restart timestamp."""
        now = time.time()
        self._get_state(name).restart_timestamps.append(now)
        self._global_timestamps.append(now)

    def _get_state(self, name: str) -> ContainerState:
        """Get or create state for a container."""
        if name not in self._states:
            self._states[name] = ContainerState()
        return self._states[name]

    def _is_trigger_event(self, event: dict) -> bool:
        """Check if a Docker event should trigger a restart attempt."""
        status = event.get("status", "")

        # Direct match: die, oom
        if status in TRIGGER_EVENTS:
            return True

        # Health check failure
        if status.startswith(HEALTH_EVENT_PREFIX):
            return True

        return False

    def _get_container_name(self, event: dict) -> str | None:
        """Extract container name from a Docker event."""
        attrs = event.get("Actor", {}).get("Attributes", {})
        return attrs.get("name")

    async def _restart(self, name: str) -> bool:
        """Attempt to restart a container. Returns True on success."""
        try:
            container = self.client.containers.get(name)
        except docker.errors.NotFound:
            logger.warning(f"Container '{name}' not found, skipping")
            return False

        # Level 1: restart
        try:
            logger.info(f"Restarting '{name}'...")
            container.restart(timeout=self.config.restart.timeout)
            logger.info(f"Container '{name}' restarted successfully")
            return True
        except Exception as e:
            logger.warning(f"restart() failed for '{name}': {e}")

        # Level 2: stop + start
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
        """Handle a single Docker event."""
        if not self._is_trigger_event(event):
            return

        name = self._get_container_name(event)
        if not name:
            return

        if not self._should_monitor(name):
            return

        state = self._get_state(name)

        # Check if this container is stopped due to max failures
        if state.stopped:
            logger.debug(f"Container '{name}' is stopped (max failures), skipping")
            return

        # Rate limit check
        if self._is_rate_limited(name):
            return

        # Attempt restart
        success = self._restart(name)

        if success:
            state.consecutive_fails = 0
            self._record_restart(name)
        else:
            state.consecutive_fails += 1
            self._record_restart(name)  # still count failed attempts for rate limiting
            if state.consecutive_fails >= self.config.restart.max_consecutive_fails:
                state.stopped = True
                logger.error(
                    f"Container '{name}' failed {state.consecutive_fails} consecutive restarts, "
                    f"stopping auto-restart. Manual intervention required."
                )

    def run(self) -> None:
        """Main event loop — blocks and processes Docker events."""
        self._running = True
        logger.info(f"Agent '{self.config.name}' started, listening for Docker events...")

        try:
            for event in self.client.events(decode=True):
                if not self._running:
                    break
                try:
                    self._handle_event(event)
                except Exception:
                    logger.exception("Error handling event")
        except KeyboardInterrupt:
            logger.info("Interrupted")
        except Exception:
            logger.exception("Event loop crashed")
        finally:
            self._running = False
            logger.info("Agent stopped")

    def stop(self) -> None:
        """Stop the event loop."""
        self._running = False
