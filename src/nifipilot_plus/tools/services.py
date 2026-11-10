"""Controller service write/control tools (MCP_MODE=full only). Read tools live in read.py."""

from .context import ToolContext


def _brief(entity: dict) -> dict:
    comp = entity.get("component") or {}
    return {
        "id": entity.get("id"),
        "name": comp.get("name"),
        "state": comp.get("state"),
        "validation_status": comp.get("validationStatus"),
    }


def register(ctx: ToolContext) -> None:
    client = ctx.client
    write = ctx.tool("services", "write")
    control = ctx.tool("services", "control")

    @write
    def create_controller_service(
        service_type: str,
        name: str,
        process_group_id: str = "root",
        properties: dict[str, str] | None = None,
    ) -> dict:
        """Create a controller service in a group (e.g. type
        org.apache.nifi.dbcp.DBCPConnectionPool). Check find_controller_services_by_type first
        to avoid duplicates. Created DISABLED; configure it, then enable it."""
        component: dict = {"type": service_type, "name": name}
        if properties:
            component["properties"] = properties
        return _brief(client.create_in_group("controller_service", process_group_id, component))

    @write
    def update_controller_service_properties(
        service_id: str, properties: dict[str, str], version: int | None = None
    ) -> dict:
        """Set properties on a DISABLED controller service (disable it first if needed)."""
        return _brief(
            client.update("controller_service", service_id, {"properties": properties}, version)
        )

    @write
    def delete_controller_service(service_id: str, version: int | None = None) -> dict:
        """Delete a controller service (must be disabled and unreferenced)."""
        client.delete_entity("controller_service", service_id, version)
        return {"deleted": service_id}

    @control
    def enable_controller_service(service_id: str, version: int | None = None) -> dict:
        """Enable a controller service."""
        return _brief(client.set_run_status("controller_service", service_id, "ENABLED", version))

    @control
    def disable_controller_service(service_id: str, version: int | None = None) -> dict:
        """Disable a controller service (stop referencing components first)."""
        return _brief(client.set_run_status("controller_service", service_id, "DISABLED", version))

    @control
    def enable_all_controller_services_in_group(process_group_id: str) -> dict:
        """Enable every controller service in scope of the group, reporting per service
        (enabled / already_enabled / failed)."""
        group_id = client.resolve_group_id(process_group_id)
        results: dict = {"enabled": [], "already_enabled": [], "failed": []}
        listing = client.get(f"flow/process-groups/{group_id}/controller-services")
        for s in listing.get("controllerServices", []):
            comp = s.get("component") or {}
            info = {"id": s["id"], "name": comp.get("name")}
            if comp.get("state") == "ENABLED":
                results["already_enabled"].append(info)
                continue
            try:
                client.set_run_status(
                    "controller_service",
                    s["id"],
                    "ENABLED",
                    (s.get("revision") or {}).get("version"),
                )
                results["enabled"].append(info)
            except Exception as exc:
                results["failed"].append({**info, "error": str(exc)[:300]})
        return results
