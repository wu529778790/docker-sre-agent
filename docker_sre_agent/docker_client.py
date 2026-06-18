"""Shared Docker client — create once, use everywhere."""

from __future__ import annotations

import docker
from docker.models.containers import Container

_client: docker.DockerClient | None = None


def get_client() -> docker.DockerClient:
    """Get or create the shared Docker client."""
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def close() -> None:
    """Close the shared Docker client."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
