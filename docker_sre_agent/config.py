"""Configuration loader."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class MonitorConfig:
    exclude_containers: list[str] = field(default_factory=lambda: ["docker-sre-agent"])
    watch_containers: list[str] = field(default_factory=list)


@dataclass
class RestartConfig:
    max_per_container_per_hour: int = 5
    max_global_per_hour: int = 20
    timeout: int = 10
    max_consecutive_fails: int = 3


@dataclass
class AgentConfig:
    name: str = "docker-sre-agent"
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    log_level: str = "INFO"


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML, falling back to defaults."""
    config = AgentConfig()
    config_path = Path(path) if path else Path("config.yaml")

    if not config_path.exists():
        logger.warning(f"Config not found: {config_path}, using defaults")
        return config

    try:
        with open(config_path) as f:
            raw: dict = yaml.safe_load(f) or {}

        if "agent" in raw:
            config.name = raw["agent"].get("name", config.name)

        if "monitor" in raw:
            m = raw["monitor"]
            config.monitor = MonitorConfig(
                exclude_containers=m.get("exclude_containers", config.monitor.exclude_containers),
                watch_containers=m.get("watch_containers", config.monitor.watch_containers),
            )

        if "restart" in raw:
            r = raw["restart"]
            config.restart = RestartConfig(
                max_per_container_per_hour=r.get("max_per_container_per_hour", config.restart.max_per_container_per_hour),
                max_global_per_hour=r.get("max_global_per_hour", config.restart.max_global_per_hour),
                timeout=r.get("timeout", config.restart.timeout),
                max_consecutive_fails=r.get("max_consecutive_fails", config.restart.max_consecutive_fails),
            )

        if "log" in raw:
            config.log_level = raw["log"].get("level", config.log_level)

        logger.info(f"Loaded config from {config_path}")
    except Exception:
        logger.exception(f"Failed to load config from {config_path}, using defaults")

    return config
