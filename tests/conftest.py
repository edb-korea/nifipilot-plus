import asyncio
import json

import pytest
from fastmcp import Client

from nifipilot_plus.client import NiFiClient
from nifipilot_plus.config import Settings
from nifipilot_plus.server import create_server

BASE = "https://nifi.test:8443/nifi-api"


def make_settings(**overrides) -> Settings:
    values = {
        "nifi_url": "https://nifi.test:8443",
        "nifi_username": "admin",
        "nifi_password": "password12345",
        "nifi_verify_ssl": False,
        "nifi_max_retries": 3,
        "nifi_retry_backoff": 0.0,
        "mcp_mode": "full",
        "mcp_audit_log": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings) -> NiFiClient:
    return NiFiClient(settings, sleep=lambda _s: None)


@pytest.fixture
def login(httpx_mock):
    """Register the token endpoint once; most tests need an authenticated client."""
    httpx_mock.add_response(method="POST", url=f"{BASE}/access/token", text="jwt-token")
    return httpx_mock


@pytest.fixture
def make_server():
    def _make(**overrides):
        settings = make_settings(**overrides)
        return create_server(settings, NiFiClient(settings, sleep=lambda _s: None))

    return _make


def call_tool(mcp, tool_name: str, /, **arguments):
    """Call a tool in-process through the real MCP protocol layer and return its data."""

    async def _run():
        async with Client(mcp) as c:
            result = await c.call_tool(tool_name, arguments)
            return result.structured_content

    return asyncio.run(_run())


def list_tool_names(mcp) -> set[str]:
    async def _run():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}

    return asyncio.run(_run())


def request_bodies(httpx_mock, method: str, path_suffix: str):
    return [
        json.loads(r.content)
        for r in httpx_mock.get_requests()
        if r.method == method and str(r.url).split("?")[0].endswith(path_suffix) and r.content
    ]
