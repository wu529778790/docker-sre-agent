"""Configuration loader."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    quick_check_interval: int = 300       # 5 minutes - container status
    scan_interval: int = 3600             # 1 hour - Docker garbage scan
    deep_scan_interval: int = 86400       # 24 hours - full disk scan


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
class LLMConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tool_rounds: int = 10


@dataclass
class CleanupConfig:
    mode: str = "report"  # report / auto
    exclude_paths: list[str] = field(default_factory=lambda: ["/etc", "/boot", "/usr"])
    exclude_containers: list[str] = field(default_factory=lambda: ["docker-sre-agent"])
    auto_clean: list[str] = field(default_factory=lambda: [
        "docker system prune -f",
        "docker volume prune -f",
    ])


@dataclass
class WebConfig:
    port: int = 6700
    token: str = ""


@dataclass
class AgentConfig:
    name: str = "docker-sre-agent"
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"

    @property
    def web_token(self) -> str:
        return self.web.token


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML, falling back to defaults."""
    config = AgentConfig()
    config_path = Path(path) if path else Path("config.yaml")

    if not config_path.exists():
        logger.warning(f"Config not found: {config_path}, using defaults")
        return _apply_env(config)

    try:
        with open(config_path) as f:
            raw: dict = yaml.safe_load(f) or {}

        if "agent" in raw:
            config.name = raw["agent"].get("name", config.name)

        if "scheduler" in raw:
            s = raw["scheduler"]
            config.scheduler = SchedulerConfig(
                quick_check_interval=s.get("quick_check_interval", config.scheduler.quick_check_interval),
                scan_interval=s.get("scan_interval", config.scheduler.scan_interval),
                deep_scan_interval=s.get("deep_scan_interval", config.scheduler.deep_scan_interval),
            )

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

        if "llm" in raw:
            l = raw["llm"]
            config.llm = LLMConfig(
                api_key=l.get("api_key", config.llm.api_key),
                base_url=l.get("base_url", config.llm.base_url),
                model=l.get("model", config.llm.model),
                max_tool_rounds=l.get("max_tool_rounds", config.llm.max_tool_rounds),
            )

        if "cleanup" in raw:
            c = raw["cleanup"]
            config.cleanup = CleanupConfig(
                mode=c.get("mode", config.cleanup.mode),
                exclude_paths=c.get("exclude_paths", config.cleanup.exclude_paths),
                exclude_containers=c.get("exclude_containers", config.cleanup.exclude_containers),
                auto_clean=c.get("auto_clean", config.cleanup.auto_clean),
            )

        if "web" in raw:
            w = raw["web"]
            config.web = WebConfig(
                port=w.get("port", config.web.port),
                token=w.get("token", config.web.token),
            )

        if "log" in raw:
            config.log_level = raw["log"].get("level", config.log_level)

        logger.info(f"Loaded config from {config_path}")
    except Exception:
        logger.exception(f"Failed to load config from {config_path}, using defaults")

    return _apply_env(config)


def _load_dotenv(path: str = "/app/.env") -> None:
    """Load .env file into os.environ (simple parser, no external dep)."""
    p = Path(path)
    if not p.exists():
        logger.warning(f".env not found: {path}")
        return

    logger.info(f".env exists: {p}, size={p.stat().st_size}")

    try:
        with open(p, "rb") as f:
            raw = f.read()

        logger.info(f".env raw bytes: {raw[:100]}")

        # Detect BOM
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
            logger.info("Detected UTF-8 BOM, stripped")

        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                already = key in os.environ
                logger.info(f".env line: key={key}, value_len={len(value)}, already_in_env={already}")
                if key and not already:
                    os.environ[key] = value
    except Exception as e:
        logger.warning(f"Failed to load .env: {e}")


def _apply_env(config: AgentConfig) -> AgentConfig:
    """Apply environment variable overrides."""
    _load_dotenv()
    if not config.llm.api_key:
        config.llm.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not config.llm.base_url:
        config.llm.base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if os.environ.get("ANTHROPIC_MODEL"):
        config.llm.model = os.environ["ANTHROPIC_MODEL"]
    if not config.web.token:
        config.web.token = os.environ.get("WEB_TOKEN", "")
    return config
