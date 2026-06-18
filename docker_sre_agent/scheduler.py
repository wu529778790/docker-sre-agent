"""Scheduler for periodic scan tasks."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from docker_sre_agent.agent import Agent
from docker_sre_agent.config import AgentConfig
from docker_sre_agent.prompts import SCAN_PROMPT
from docker_sre_agent.tools.docker import ScanDockerTool
from docker_sre_agent.tools.disk import ScanDiskTool

logger = logging.getLogger(__name__)


class Scheduler:
    """Manages periodic AI scan tasks."""

    def __init__(self, config: AgentConfig, agent: Agent) -> None:
        self.config = config
        self.agent = agent
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the scheduler with periodic tasks."""
        if self._running:
            return
        self._running = True
        logger.info("Scheduler started")

        self._tasks = [
            asyncio.create_task(self._run_periodic(
                "docker_scan",
                self.config.scheduler.scan_interval,
                self._docker_scan,
            )),
            asyncio.create_task(self._run_periodic(
                "deep_scan",
                self.config.scheduler.deep_scan_interval,
                self._deep_scan,
            )),
        ]

        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        """Stop all scheduled tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def _run_periodic(self, name: str, interval: int, handler) -> None:
        """Run a task periodically."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                logger.info(f"Running scheduled task: {name}")
                await handler()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"Error in scheduled task '{name}'")
                await asyncio.sleep(5)

    async def _docker_scan(self) -> None:
        """Run Docker environment scan and analyze with LLM."""
        docker_tool = ScanDockerTool()
        scan_data = docker_tool.execute()

        prompt = SCAN_PROMPT.format(scan_data=scan_data)

        def on_tool_call(name, inp):
            logger.info(f"  Agent calling: {name}")

        result = self.agent.run_streaming(
            prompt,
            on_tool_call=on_tool_call,
        )

        logger.info(f"Docker scan result:\n{result}")

    async def _deep_scan(self) -> None:
        """Run full disk scan and analyze with LLM."""
        docker_tool = ScanDockerTool()
        disk_tool = ScanDiskTool()

        scan_data = {
            "docker": docker_tool.execute(),
            "disk": disk_tool.execute(),
        }

        prompt = SCAN_PROMPT.format(
            scan_data=f"Docker 环境:\n{scan_data['docker']}\n\n磁盘状况:\n{scan_data['disk']}"
        )

        result = self.agent.run(prompt)
        logger.info(f"Deep scan result:\n{result}")
