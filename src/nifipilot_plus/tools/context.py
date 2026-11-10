"""Shared plumbing that every tool goes through.

``ctx.tool(toolset, kind)`` decorates a plain function and:
  * registers it with FastMCP only if its toolset is enabled (MCP_TOOLSETS) and, for
    write/control tools, only when MCP_MODE=full — so a read-only agent never even sees
    them in its tool list;
  * short-circuits write/control tools when MCP_DRY_RUN=true;
  * writes an audit line for every call (success or error);
  * redacts sensitive keys and caps very large lists in the returned data.

Note: tool modules must NOT use ``from __future__ import annotations`` — FastMCP builds the
JSON schema from the real annotations.
"""

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fastmcp import FastMCP

from ..client import NiFiClient
from ..config import Settings
from ..security import Audit, redact

Kind = Literal["read", "write", "control"]


@dataclass
class ToolContext:
    mcp: FastMCP
    client: NiFiClient
    settings: Settings
    audit: Audit
    registered: list[str] = field(default_factory=list)

    def tool(self, toolset: str, kind: Kind = "read") -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            if toolset not in self.settings.toolsets:
                return fn
            if kind != "read" and not self.settings.can_write:
                return fn

            name = fn.__name__
            signature = inspect.signature(fn)

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                params = dict(bound.arguments)
                try:
                    if kind != "read" and self.settings.mcp_dry_run:
                        self.audit.log(name, params, "dry_run")
                        return {"dry_run": True, "tool": name, "params": redact(params)}
                    result = fn(*args, **kwargs)
                    self.audit.log(name, params, "ok")
                    return redact(result)
                except Exception as exc:
                    self.audit.log(name, params, "error", error=str(exc))
                    raise

            self.mcp.tool()(wrapper)
            self.registered.append(name)
            return wrapper

        return decorator
