"""Scheduler for periodic scan tasks."""

from __future__ import annotations

import asyncio
import logging

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
        self._scan_lock = asyncio.Lock()  # prevent overlapping scans

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
        # Wait for tasks to finish cancelling
        await asyncio.gather(*self._tasks, return_exceptions=True)
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
        if self._scan_lock.locked():
            logger.info("Docker scan already in progress, skipping")
            return
        async with self._scan_lock:
            def _do_scan():
                docker_tool = ScanDockerTool()
                return docker_tool.execute()

            scan_data = await asyncio.to_thread(_do_scan)
            prompt = SCAN_PROMPT.format(scan_data=scan_data)

            def on_tool_call(name, inp):
                logger.info(f"  Agent calling: {name}")

            result = await asyncio.to_thread(
                self.agent.run_streaming, prompt, None, on_tool_call, None
            )
            logger.info(f"Docker scan result:\n{result}")

    async def _deep_scan(self) -> None:
        """Run full disk scan and analyze with LLM."""
        if self._scan_lock.locked():
            logger.info("Deep scan already in progress, skipping")
            return
        async with self._scan_lock:
            def _do_scan():
                docker_tool = ScanDockerTool()
                disk_tool = ScanDiskTool()
                docker_data = docker_tool.execute()
                disk_data = disk_tool.execute()
                return docker_data, disk_data

            docker_data, disk_data = await asyncio.to_thread(_do_scan)
            prompt = SCAN_PROMPT.format(
                scan_data=f"Docker 环境:\n{docker_data}\n\n磁盘状况:\n{disk_data}"
            )
            result = await asyncio.to_thread(self.agent.run, prompt)
            logger.info(f"Deep scan result:\n{result}")
