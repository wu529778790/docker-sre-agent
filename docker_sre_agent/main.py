"""Entry point for the Docker SRE Agent."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from docker_sre_agent.config import load_config
from docker_sre_agent.agent import Agent
from docker_sre_agent.llm import LLMClient
from docker_sre_agent.tools import ALL_TOOLS
from docker_sre_agent.prompts import SYSTEM_PROMPT, ASK_PROMPT


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_run(config) -> None:
    """Run the agent as a daemon with periodic scans."""
    from docker_sre_agent.scanner import Scanner
    from docker_sre_agent.scheduler import Scheduler

    if not config.llm.api_key:
        print("错误: 未配置 ANTHROPIC_API_KEY，请在 config.yaml 或环境变量中设置")
        sys.exit(1)

    llm = LLMClient(api_key=config.llm.api_key, model=config.llm.model)
    agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)
    scanner = Scanner(config)
    scheduler = Scheduler(config, agent)

    async def run_all():
        shutdown = asyncio.Event()

        def handle_signal(sig, frame):
            logging.info(f"Received signal {sig}, shutting down...")
            shutdown.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        # Start scanner and scheduler in parallel
        scanner_task = asyncio.create_task(scanner.start())
        scheduler_task = asyncio.create_task(scheduler.start())

        await shutdown.wait()
        await scanner.stop()
        await scheduler.stop()

    logging.info(f"Agent '{config.name}' starting (daemon mode)")
    asyncio.run(run_all())


def cmd_ask(config, question: str) -> None:
    """Interactive Q&A mode — one-shot."""
    if not config.llm.api_key:
        print("错误: 未配置 ANTHROPIC_API_KEY")
        sys.exit(1)

    llm = LLMClient(api_key=config.llm.api_key, model=config.llm.model)
    agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)

    prompt = ASK_PROMPT.format(question=question)

    def on_tool_call(name, inp):
        print(f"  🔧 调用工具: {name}")

    def on_tool_result(name, result):
        print(f"  ✅ {name} 完成")

    print(f"🤔 分析中...\n")
    result = agent.run_streaming(prompt, on_tool_call=on_tool_call, on_tool_result=on_tool_result)
    print(f"\n📊 分析结果:\n{result}")


def cmd_scan(config) -> None:
    """One-shot scan mode — report only."""
    from docker_sre_agent.tools.docker import ScanDockerTool
    from docker_sre_agent.tools.disk import ScanDiskTool

    docker_tool = ScanDockerTool()
    disk_tool = ScanDiskTool()

    print("🔍 扫描 Docker 环境...")
    docker_data = docker_tool.execute()
    print("🔍 扫描磁盘...")
    disk_data = disk_tool.execute()

    if config.llm.api_key:
        llm = LLMClient(api_key=config.llm.api_key, model=config.llm.model)
        agent = Agent(llm=llm, tools=ALL_TOOLS, max_rounds=config.llm.max_tool_rounds)
        from docker_sre_agent.prompts import SCAN_PROMPT
        prompt = SCAN_PROMPT.format(
            scan_data=f"Docker 环境:\n{docker_data}\n\n磁盘状况:\n{disk_data}"
        )
        result = agent.run(prompt)
        print(f"\n📊 AI 分析结果:\n{result}")
    else:
        print(f"\nDocker 环境:\n{docker_data}")
        print(f"\n磁盘状况:\n{disk_data}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Docker SRE Agent")
    parser.add_argument("--config", "-c", help="Path to config file", default=None)
    parser.add_argument("--version", "-v", action="store_true", help="Show version")

    sub = parser.add_subparsers(dest="command")

    # run — daemon mode
    sub.add_parser("run", help="Run as daemon with periodic scans")

    # ask — interactive Q&A
    ask_parser = sub.add_parser("ask", help="Ask a question about the server")
    ask_parser.add_argument("question", help="Your question")

    # scan — one-shot scan
    sub.add_parser("scan", help="Run a one-time scan and report")

    args = parser.parse_args()

    if args.version:
        from docker_sre_agent import __version__
        print(f"docker-sre-agent v{__version__}")
        return

    config = load_config(args.config)
    setup_logging(config.log_level)

    if args.command == "run":
        cmd_run(config)
    elif args.command == "ask":
        cmd_ask(config, args.question)
    elif args.command == "scan":
        cmd_scan(config)
    else:
        # Default: daemon mode
        cmd_run(config)


if __name__ == "__main__":
    main()
