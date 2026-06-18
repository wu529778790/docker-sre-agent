"""Shared Docker client — create once, use everywhere."""

from __future__ import annotations

import threading
import logging

import docker

logger = logging.getLogger(__name__)

_client: docker.DockerClient | None = None
_lock = threading.Lock()


def get_client() -> docker.DockerClient:
    """Get or create the shared Docker client (thread-safe)."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            _client = docker.from_env()
        return _client


def get_client_safe() -> docker.DockerClient:
    """Get client with reconnection on failure."""
    global _client
    try:
        if _client is not None:
            _client.ping()
            return _client
    except Exception:
        logger.warning("Docker client stale, reconnecting...")
        with _lock:
            _client = None

    with _lock:
        if _client is None:
            _client = docker.from_env()
        return _client


def close() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
