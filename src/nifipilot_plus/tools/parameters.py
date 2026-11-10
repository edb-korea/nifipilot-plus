"""Parameter context write tools (MCP_MODE=full only). Read tools live in read.py."""

import json
from typing import Any

from .context import ToolContext


def _param_entities(parameters: list[dict[str, Any]] | str | None) -> list[dict[str, Any]]:
    """Accept a list of dicts or a JSON string; build NiFi ParameterEntity objects."""
    if not parameters:
        return []
    if isinstance(parameters, str):
        parameters = json.loads(parameters)
    entities = []
    for p in parameters:
        param: dict[str, Any] = {
            "name": p["name"],
            "value": p.get("value"),
            "sensitive": bool(p.get("sensitive", False)),
        }
        if p.get("description"):
            param["description"] = p["description"]
        entities.append({"parameter": param})
    return entities


def register(ctx: ToolContext) -> None:
    client = ctx.client
    write = ctx.tool("parameters", "write")

    @write
    def create_parameter_context(
        name: str,
        description: str = "",
        parameters: list[dict] | str | None = None,
    ) -> dict:
        """Create a parameter context. `parameters`: list of
        {"name","value","sensitive"(bool),"description"(optional)}, or a JSON string of it.
        Reference values in processor properties as #{name}."""
        result = client.post(
            "parameter-contexts",
            json={
                "revision": {"version": 0},
                "component": {
                    "name": name,
                    "description": description,
                    "parameters": _param_entities(parameters),
                },
            },
        )
        return {"id": result.get("id"), "name": name}

    @write
    def update_parameter_context(
        context_id: str,
        name: str | None = None,
        description: str | None = None,
        parameters: list[dict] | str | None = None,
        version: int | None = None,
    ) -> dict:
        """Update name/description and add or change parameters (same shape as create)."""
        fields: dict[str, Any] = {}
        if name:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if parameters:
            fields["parameters"] = _param_entities(parameters)
        result = client.update("parameter_context", context_id, fields, version)
        return {"id": context_id, "name": (result.get("component") or {}).get("name")}

    @write
    def delete_parameter_context(context_id: str, version: int | None = None) -> dict:
        """Delete a parameter context (must not be bound to any process group)."""
        client.delete_entity("parameter_context", context_id, version)
        return {"deleted": context_id}

    @write
    def apply_parameter_context_to_process_group(
        process_group_id: str, context_id: str, version: int | None = None
    ) -> dict:
        """Bind a parameter context to a process group so its components can use #{param}."""
        result = client.update(
            "process_group",
            client.resolve_group_id(process_group_id),
            {"parameterContext": {"id": context_id}},
            version,
        )
        return {
            "process_group_id": process_group_id,
            "parameter_context_id": (
                (result.get("component") or {}).get("parameterContext") or {}
            ).get("id"),
        }
