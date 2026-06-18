"""Configuration loader."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class MonitorConfig:
    exclude_containers: list[str] = field(default_factory=lambda: ["server-sre-agent"])
    check_interval: int = 300


@dataclass
class RestartConfig:
    max_per_container_per_hour: int = 5
    max_global_per_hour: int = 20
    timeout: int = 10
    max_consecutive_fails: int = 3


@dataclass
class CleanupConfig:
    mode: str = "report"
    exclude_paths: list[str] = field(default_factory=lambda: ["/etc", "/boot", "/usr"])
    auto_clean: list[str] = field(default_factory=lambda: [
        "docker system prune -f",
        "docker volume prune -f",
    ])


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    max_tool_rounds: int = 10
    timeout: int = 120
    enabled: bool = True


@dataclass
class WebConfig:
    port: int = 6700
    token: str = ""


@dataclass
class AgentConfig:
    name: str = "server-sre"
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    restart: RestartConfig = field(default_factory=RestartConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    web: WebConfig = field(default_factory=WebConfig)
    log_level: str = "INFO"


def load_config(path: str | Path | None = None) -> AgentConfig:
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
        if "monitor" in raw:
            m = raw["monitor"]
            config.monitor = MonitorConfig(
                exclude_containers=m.get("exclude_containers", config.monitor.exclude_containers),
                check_interval=m.get("check_interval", config.monitor.check_interval),
            )
        if "restart" in raw:
            r = raw["restart"]
            config.restart = RestartConfig(
                max_per_container_per_hour=r.get("max_per_container_per_hour", config.restart.max_per_container_per_hour),
                max_global_per_hour=r.get("max_global_per_hour", config.restart.max_global_per_hour),
                timeout=r.get("timeout", config.restart.timeout),
                max_consecutive_fails=r.get("max_consecutive_fails", config.restart.max_consecutive_fails),
            )
        if "cleanup" in raw:
            c = raw["cleanup"]
            config.cleanup = CleanupConfig(
                mode=c.get("mode", config.cleanup.mode),
                exclude_paths=c.get("exclude_paths", config.cleanup.exclude_paths),
                auto_clean=c.get("auto_clean", config.cleanup.auto_clean),
            )
        if "llm" in raw:
            l = raw["llm"]
            config.llm = LLMConfig(
                api_key=l.get("api_key", config.llm.api_key),
                base_url=l.get("base_url", config.llm.base_url),
                model=l.get("model", config.llm.model),
                max_tokens=l.get("max_tokens", config.llm.max_tokens),
                max_tool_rounds=l.get("max_tool_rounds", config.llm.max_tool_rounds),
                timeout=l.get("timeout", config.llm.timeout),
                enabled=l.get("enabled", config.llm.enabled),
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
        logger.exception(f"Failed to load config from {config_path}")

    return _apply_env(config)


def _load_dotenv(path: str = "/app/.env") -> None:
    """Load .env from container path or project root."""
    for env_path in [Path(path), Path(".env")]:
        if env_path.exists():
            p = env_path
            break
    else:
        return
    try:
        with open(p, "rb") as f:
            raw = f.read()
        if raw.startswith(b'\xef\xbb\xbf'):
            raw = raw[3:]
        text = raw.decode("utf-8", errors="replace")
        loaded = 0
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded += 1
        if loaded:
            logger.info(f"Loaded {loaded} variables from {path}")
    except Exception as e:
        logger.warning(f"Failed to load .env: {e}")


def _apply_env(config: AgentConfig) -> AgentConfig:
    _load_dotenv()
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key and env_key not in ("your-key-here", ""):
        config.llm.api_key = env_key
    env_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if env_url:
        config.llm.base_url = env_url
    if os.environ.get("ANTHROPIC_MODEL"):
        config.llm.model = os.environ["ANTHROPIC_MODEL"]
    env_token = os.environ.get("WEB_TOKEN", "")
    if env_token:
        config.web.token = env_token
    # Clear placeholder tokens
    if config.web.token.startswith("${"):
        config.web.token = ""
    # Validate critical values
    if config.monitor.check_interval < 1:
        logger.warning("check_interval < 1, resetting to 300")
        config.monitor.check_interval = 300
    if config.restart.max_per_container_per_hour < 1:
        config.restart.max_per_container_per_hour = 5
    return config
