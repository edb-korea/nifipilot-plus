"""Input/output port write and control tools (MCP_MODE=full only). Read tools live in read.py."""

from .context import ToolContext


def _brief(entity: dict) -> dict:
    comp = entity.get("component") or {}
    return {"id": entity.get("id"), "name": comp.get("name"), "state": comp.get("state")}


def register(ctx: ToolContext) -> None:
    client = ctx.client
    write = ctx.tool("ports", "write")
    control = ctx.tool("ports", "control")

    @write
    def create_input_port(name: str, process_group_id: str, x: float = 0.0, y: float = 0.0) -> dict:
        """Create an input port (receives data from the parent group). Created STOPPED."""
        return _brief(
            client.create_in_group(
                "input_port", process_group_id, {"name": name, "position": {"x": x, "y": y}}
            )
        )

    @write
    def create_output_port(
        name: str, process_group_id: str, x: float = 0.0, y: float = 0.0
    ) -> dict:
        """Create an output port (sends data to the parent group). Created STOPPED."""
        return _brief(
            client.create_in_group(
                "output_port", process_group_id, {"name": name, "position": {"x": x, "y": y}}
            )
        )

    @write
    def update_input_port(port_id: str, name: str, version: int | None = None) -> dict:
        """Rename an input port."""
        return _brief(client.update("input_port", port_id, {"name": name}, version))

    @write
    def update_output_port(port_id: str, name: str, version: int | None = None) -> dict:
        """Rename an output port."""
        return _brief(client.update("output_port", port_id, {"name": name}, version))

    @write
    def delete_input_port(port_id: str, version: int | None = None) -> dict:
        """Delete an input port (stopped, no connections)."""
        client.delete_entity("input_port", port_id, version)
        return {"deleted": port_id}

    @write
    def delete_output_port(port_id: str, version: int | None = None) -> dict:
        """Delete an output port (stopped, no connections)."""
        client.delete_entity("output_port", port_id, version)
        return {"deleted": port_id}

    @control
    def start_input_port(port_id: str, version: int | None = None) -> dict:
        """Start an input port so data can flow into the group."""
        return _brief(client.set_run_status("input_port", port_id, "RUNNING", version))

    @control
    def stop_input_port(port_id: str, version: int | None = None) -> dict:
        """Stop an input port."""
        return _brief(client.set_run_status("input_port", port_id, "STOPPED", version))

    @control
    def start_output_port(port_id: str, version: int | None = None) -> dict:
        """Start an output port so data can flow out of the group."""
        return _brief(client.set_run_status("output_port", port_id, "RUNNING", version))

    @control
    def stop_output_port(port_id: str, version: int | None = None) -> dict:
        """Stop an output port."""
        return _brief(client.set_run_status("output_port", port_id, "STOPPED", version))
