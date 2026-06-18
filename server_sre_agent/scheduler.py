"""Scheduler for periodic scan tasks."""

from __future__ import annotations

import asyncio
import logging

from server_sre_agent.agent import Agent
from server_sre_agent.config import AgentConfig
from server_sre_agent.prompts import SCAN_PROMPT

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, config: AgentConfig, agent: Agent) -> None:
        self.config = config
        self.agent = agent
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_periodic("disk_scan", 3600, self._disk_scan)),
            asyncio.create_task(self._run_periodic("resource_check", self.config.monitor.check_interval, self._resource_check)),
        ]
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_periodic(self, name: str, interval: int, handler) -> None:
        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                await handler()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"Error in {name}")

    async def _disk_scan(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            from server_sre_agent.tools.docker import DockerInfoTool
            from server_sre_agent.tools.host import HostDiskScanTool
            def scan():
                return DockerInfoTool().execute() + "\n\n" + HostDiskScanTool().execute()
            scan_data = await asyncio.to_thread(scan)
            prompt = SCAN_PROMPT.format(scan_data=scan_data)
            result = await asyncio.to_thread(self.agent.chat, [{"role": "user", "content": prompt}])
            logger.info(f"Disk scan result: {result[-1].get('content', '')[:500]}")

    async def _resource_check(self) -> None:
        from server_sre_agent.tools.docker import ContainerListTool
        def check():
            return ContainerListTool().execute()
        result = await asyncio.to_thread(check)
        logger.debug(f"Resource check: {result[:200]}")
