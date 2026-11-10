from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

from . import __version__
from .client import NiFiClient
from .config import Settings, get_settings
from .security import Audit
from .tools import ToolContext, register_all

logger = logging.getLogger("nifipilot_plus")


def _instructions(settings: Settings) -> str:
    access = (
        "READ-ONLY: only inspection tools are available."
        if not settings.can_write
        else "FULL ACCESS: write/control tools are enabled"
        + (" (DRY RUN: they only report what they would do)." if settings.mcp_dry_run else ".")
    )
    return (
        f"MCP server for Apache NiFi. {access} "
        "list_* tools return compact summaries; use get_*_details for full configuration. "
        "Write tools take an optional `version` (current revision is fetched when omitted). "
        "Build flows inside a process group (start_new_flow), enable controller services "
        "before starting processors, and check get_flow_health_status afterwards."
    )


def create_server(
    settings: Settings | None = None, client: NiFiClient | None = None
) -> tuple[FastMCP, ToolContext]:
    settings = settings or get_settings()
    mcp = FastMCP(name="nifipilot-plus", instructions=_instructions(settings))
    client = client or NiFiClient(settings)
    audit = Audit(settings.mcp_audit_log, settings.mcp_audit_log_path)
    ctx = ToolContext(mcp=mcp, client=client, settings=settings, audit=audit)
    register_all(ctx)
    return mcp, ctx


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    settings = get_settings()
    mcp, ctx = create_server(settings)
    logger.info(
        "nifipilot-plus %s: %d tools (mode=%s, toolsets=%s) -> %s",
        __version__,
        len(ctx.registered),
        settings.mcp_mode,
        ",".join(sorted(settings.toolsets)),
        settings.api_base,
    )
    if settings.mcp_transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=settings.mcp_transport, host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
