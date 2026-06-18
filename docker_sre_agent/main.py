"""Entry point for the Docker SRE Agent."""

import logging
import signal
import sys

from docker_sre_agent.config import load_config
from docker_sre_agent.agent import Agent


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Docker SRE Agent")
    parser.add_argument("--config", "-c", help="Path to config file", default=None)
    parser.add_argument("--version", "-v", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        from docker_sre_agent import __version__
        print(f"docker-sre-agent v{__version__}")
        return

    config = load_config(args.config)
    setup_logging(config.log_level)

    agent = Agent(config)

    def handle_signal(sig: int, frame: object) -> None:
        logging.info(f"Received signal {sig}, shutting down...")
        agent.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    agent.run()


if __name__ == "__main__":
    main()
