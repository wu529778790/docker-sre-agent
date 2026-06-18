"""Entry point for the Server SRE Agent."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from server_sre_agent.config import load_config


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_web(config) -> None:
    from server_sre_agent.web import create_app
    from server_sre_agent.docker_client import close
    import atexit

    app = create_app(config)
    atexit.register(close)
    logging.info(f"Web server on http://0.0.0.0:{config.web.port}")
    app.run(host="0.0.0.0", port=config.web.port, debug=False, threaded=True)


def cmd_run(config) -> None:
    from server_sre_agent.scanner import Scanner
    from server_sre_agent.scheduler import Scheduler
    from server_sre_agent.agent import Agent
    from server_sre_agent.llm import LLMClient
    from server_sre_agent.tools import ALL_TOOLS
    from server_sre_agent.docker_client import close

    llm = LLMClient(
        api_key=config.llm.api_key, base_url=config.llm.base_url,
        model=config.llm.model, max_tokens=config.llm.max_tokens, timeout=config.llm.timeout,
    )
    agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)
    scanner = Scanner(config)
    scheduler = Scheduler(config, agent)

    async def run_all():
        shutdown = asyncio.Event()

        def handle_signal(sig, frame):
            logging.info(f"Received signal {sig}, shutting down...")
            shutdown.set()

        # Register signals before starting tasks
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal, sig, None)

        scanner_task = asyncio.create_task(scanner.start())
        scheduler_task = asyncio.create_task(scheduler.start())

        await shutdown.wait()

        scanner_task.cancel()
        scheduler_task.cancel()
        await asyncio.gather(scanner_task, scheduler_task, return_exceptions=True)
        close()

    asyncio.run(run_all())


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Server SRE Agent")
    parser.add_argument("--version", "-v", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run daemon with event listener + scheduler")
    run_parser.add_argument("--config", "-c", default=None)

    web_parser = sub.add_parser("web", help="Run web server")
    web_parser.add_argument("--config", "-c", default=None)
    web_parser.add_argument("--port", "-p", type=int, default=None)

    args = parser.parse_args()

    if args.version:
        from server_sre_agent import __version__
        print(f"server-sre-agent v{__version__}")
        return

    config = load_config(args.config)
    setup_logging(config.log_level)

    # CLI --port overrides config
    if hasattr(args, "port") and args.port is not None:
        config.web.port = args.port

    if args.command == "web":
        cmd_web(config)
    else:
        cmd_run(config)


if __name__ == "__main__":
    main()
