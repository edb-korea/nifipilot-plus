"""Write tools for process groups, processors and connections (MCP_MODE=full only).

`version` arguments are optional everywhere: when omitted the current revision is fetched
automatically, so the model does not have to round-trip revisions.
"""

from typing import Any

from .context import ToolContext


def _as_list(value: list[str] | str) -> list[str]:
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


def register(ctx: ToolContext) -> None:
    client = ctx.client
    flow = ctx.tool("flow", "write")

    @flow
    def start_new_flow(flow_name: str, parent_group_id: str | None = None) -> dict:
        """RECOMMENDED way to begin building: creates a dedicated process group for the flow
        (best practice: never build on the root canvas) and returns its id."""
        parent = client.resolve_group_id(parent_group_id)
        pg = client.create_in_group("process_group", parent, {"name": flow_name})
        return {
            "process_group": {"id": pg.get("id"), "name": flow_name, "parent_group_id": parent},
            "next_steps": [
                "Create controller services if needed (find_controller_services_by_type first)",
                "Add processors to this group with create_processor",
                "Connect them with create_connection",
                "enable_all_controller_services_in_group, then start_all_processors_in_group",
                "Check get_flow_health_status",
            ],
        }

    @flow
    def create_process_group(
        name: str, parent_group_id: str = "root", x: float = 0.0, y: float = 0.0
    ) -> dict:
        """Create a process group (folder) inside parent_group_id."""
        pg = client.create_in_group(
            "process_group", parent_group_id, {"name": name, "position": {"x": x, "y": y}}
        )
        return {"id": pg.get("id"), "name": name, "parent_group_id": parent_group_id}

    @flow
    def update_process_group_name(
        process_group_id: str, name: str, version: int | None = None
    ) -> dict:
        """Rename a process group."""
        result = client.update("process_group", process_group_id, {"name": name}, version)
        return {"id": process_group_id, "name": (result.get("component") or {}).get("name")}

    @flow
    def delete_process_group(process_group_id: str, version: int | None = None) -> dict:
        """Delete a process group. It must be empty and fully stopped."""
        client.delete_entity("process_group", process_group_id, version)
        return {"deleted": process_group_id}

    @flow
    def create_processor(
        name: str,
        processor_type: str,
        process_group_id: str = "root",
        x: float = 0.0,
        y: float = 0.0,
        properties: dict[str, str] | None = None,
    ) -> dict:
        """Add a processor. `processor_type` is the full class name, e.g.
        org.apache.nifi.processors.standard.LogAttribute (see get_processor_types).
        Optional `properties` are set at creation time."""
        component: dict[str, Any] = {
            "type": processor_type,
            "name": name,
            "position": {"x": x, "y": y},
        }
        if properties:
            component["config"] = {"properties": properties}
        p = client.create_in_group("processor", process_group_id, component)
        comp = p.get("component") or {}
        return {
            "id": p.get("id"),
            "name": comp.get("name"),
            "type": comp.get("type"),
            "state": comp.get("state"),
            "validation_errors": comp.get("validationErrors") or [],
        }

    @flow
    def update_processor_config(
        processor_id: str, config: dict, version: int | None = None
    ) -> dict:
        """Update a STOPPED processor. `config` is merged into the processor component, e.g.
        {"name": "x", "config": {"properties": {"Log Level": "info"},
        "schedulingPeriod": "30 sec", "autoTerminatedRelationships": ["success"]}}."""
        result = client.update("processor", processor_id, config, version)
        comp = result.get("component") or {}
        return {
            "id": processor_id,
            "name": comp.get("name"),
            "state": comp.get("state"),
            "validation_errors": comp.get("validationErrors") or [],
        }

    @flow
    def delete_processor(processor_id: str, version: int | None = None) -> dict:
        """Delete a processor (must be stopped, with no queued data in its connections)."""
        client.delete_entity("processor", processor_id, version)
        return {"deleted": processor_id}

    @flow
    def create_connection(
        source_id: str,
        destination_id: str,
        relationships: list[str] | str,
        source_type: str = "PROCESSOR",
        destination_type: str = "PROCESSOR",
        process_group_id: str | None = None,
    ) -> dict:
        """Connect two components for the given relationships (list or "success,failure").
        Types: PROCESSOR | INPUT_PORT | OUTPUT_PORT | FUNNEL. The owning groups of both ends
        are looked up automatically, so cross-group links through ports work."""
        src_group = client.parent_group_of(source_type, source_id)
        dst_group = client.parent_group_of(destination_type, destination_id)
        if process_group_id:
            owner = client.resolve_group_id(process_group_id)
        elif src_group == dst_group:
            owner = src_group
        elif source_type.upper() == "OUTPUT_PORT":
            owner = dst_group  # a child group's output port feeding a component in the parent
        else:
            owner = src_group  # a component feeding a child group's input port
        conn = client.create_in_group(
            "connection",
            owner,
            {
                "source": {"id": source_id, "groupId": src_group, "type": source_type.upper()},
                "destination": {
                    "id": destination_id,
                    "groupId": dst_group,
                    "type": destination_type.upper(),
                },
                "selectedRelationships": _as_list(relationships),
            },
        )
        return {
            "id": conn.get("id"),
            "owner_group_id": owner,
            "relationships": _as_list(relationships),
        }

    @flow
    def delete_connection(connection_id: str, version: int | None = None) -> dict:
        """Delete a connection. Its queue must be empty (see empty_connection_queue)."""
        client.delete_entity("connection", connection_id, version)
        return {"deleted": connection_id}
