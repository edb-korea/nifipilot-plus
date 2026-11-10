"""MCP tools. Each module exposes ``register(ctx)``; see :mod:`nifipilot_plus.tools.context`."""

from . import control, flow_write, guides, parameters, ports, read, services
from .context import ToolContext

__all__ = ["ToolContext", "register_all"]

_MODULES = (read, flow_write, control, services, parameters, ports, guides)


def register_all(ctx: ToolContext) -> list[str]:
    for module in _MODULES:
        module.register(ctx)
    return ctx.registered
