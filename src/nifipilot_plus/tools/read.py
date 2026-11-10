"""Read-only tools. Available in every mode.

List tools return compact summaries to keep LLM context small; the ``get_*_details`` tools
return the full (redacted) NiFi entity.
"""

from typing import Any

from .context import ToolContext

NEAR_BACKPRESSURE_PERCENT = 80


# ---------------------------------------------------------------- summarisers
def _snap(entity: dict[str, Any]) -> dict[str, Any]:
    return (entity.get("status") or {}).get("aggregateSnapshot") or {}


def processor_summary(p: dict[str, Any]) -> dict[str, Any]:
    comp = p.get("component") or {}
    return {
        "id": p.get("id"),
        "name": comp.get("name"),
        "type": comp.get("type"),
        "state": comp.get("state"),
        "run_status": (p.get("status") or {}).get("runStatus"),
        "active_threads": _snap(p).get("activeThreadCount", 0),
        "validation_errors": comp.get("validationErrors") or [],
        "revision": (p.get("revision") or {}).get("version"),
    }


def connection_summary(c: dict[str, Any]) -> dict[str, Any]:
    comp = c.get("component") or {}
    src, dst = comp.get("source") or {}, comp.get("destination") or {}
    snap = _snap(c)
    return {
        "id": c.get("id"),
        "name": comp.get("name") or None,
        "source": {"id": src.get("id"), "name": src.get("name"), "type": src.get("type")},
        "destination": {"id": dst.get("id"), "name": dst.get("name"), "type": dst.get("type")},
        "relationships": comp.get("selectedRelationships") or [],
        "flowfiles_queued": snap.get("flowFilesQueued", 0),
        "bytes_queued": snap.get("bytesQueued", 0),
        "percent_use_count": snap.get("percentUseCount", 0),
        "percent_use_bytes": snap.get("percentUseBytes", 0),
        "revision": (c.get("revision") or {}).get("version"),
    }


def service_summary(s: dict[str, Any]) -> dict[str, Any]:
    comp = s.get("component") or {}
    return {
        "id": s.get("id"),
        "name": comp.get("name"),
        "type": comp.get("type"),
        "state": comp.get("state"),
        "validation_status": comp.get("validationStatus"),
        "parent_group_id": comp.get("parentGroupId"),
        "revision": (s.get("revision") or {}).get("version"),
    }


def port_summary(p: dict[str, Any]) -> dict[str, Any]:
    comp = p.get("component") or {}
    return {
        "id": p.get("id"),
        "name": comp.get("name"),
        "state": comp.get("state"),
        "revision": (p.get("revision") or {}).get("version"),
    }


# ------------------------------------------------------------- shared logic
def group_summary(client, group_id: str) -> dict[str, Any]:
    """Counts of components, processor states and queued data for one process group."""
    group_id = client.resolve_group_id(group_id)
    pgf = client.get(f"flow/process-groups/{group_id}")["processGroupFlow"]
    flow = pgf.get("flow") or {}
    processors = flow.get("processors") or []
    connections = flow.get("connections") or []

    states: dict[str, int] = {}
    for p in processors:
        state = (p.get("status") or {}).get("runStatus") or (p.get("component") or {}).get(
            "state", "UNKNOWN"
        )
        states[state] = states.get(state, 0) + 1

    return {
        "process_group_id": pgf.get("id", group_id),
        "name": ((pgf.get("breadcrumb") or {}).get("breadcrumb") or {}).get("name"),
        "processor_count": len(processors),
        "processor_states": states,
        "connection_count": len(connections),
        "child_process_group_count": len(flow.get("processGroups") or []),
        "input_port_count": len(flow.get("inputPorts") or []),
        "output_port_count": len(flow.get("outputPorts") or []),
        "total_flowfiles_queued": sum(_snap(c).get("flowFilesQueued", 0) for c in connections),
        "total_bytes_queued": sum(_snap(c).get("bytesQueued", 0) for c in connections),
    }


def flow_health(client, group_id: str) -> dict[str, Any]:
    """Processor / service / queue / bulletin health of one process group (direct children)."""
    group_id = client.resolve_group_id(group_id)
    health: dict[str, Any] = {
        "process_group_id": group_id,
        "processors": {"total": 0, "running": 0, "stopped": 0, "invalid": 0, "disabled": 0},
        "controller_services": {"total": 0, "enabled": 0, "disabled": 0, "invalid": 0},
        "connections": {"total": 0, "with_queued_data": 0, "near_backpressure": 0},
        "bulletins": [],
        "problems": [],
    }

    for p in client.get(f"process-groups/{group_id}/processors").get("processors", []):
        health["processors"]["total"] += 1
        status = ((p.get("status") or {}).get("runStatus") or "").lower()
        name = (p.get("component") or {}).get("name")
        if status in ("running", "stopped", "invalid", "disabled"):
            health["processors"][status] += 1
        if status == "invalid":
            errors = (p.get("component") or {}).get("validationErrors") or []
            health["problems"].append(f"Processor '{name}' is invalid: {'; '.join(errors)}")

    try:
        services = client.get(f"flow/process-groups/{group_id}/controller-services")
        for s in services.get("controllerServices", []):
            comp = s.get("component") or {}
            health["controller_services"]["total"] += 1
            if comp.get("validationStatus") == "INVALID":
                health["controller_services"]["invalid"] += 1
                health["problems"].append(f"Controller service '{comp.get('name')}' is invalid")
            elif comp.get("state") == "ENABLED":
                health["controller_services"]["enabled"] += 1
            else:
                health["controller_services"]["disabled"] += 1
    except Exception as exc:  # services may be hidden by authorization
        health["controller_services"]["error"] = str(exc)[:200]

    for c in client.get(f"process-groups/{group_id}/connections").get("connections", []):
        health["connections"]["total"] += 1
        snap = _snap(c)
        if snap.get("flowFilesQueued", 0) > 0:
            health["connections"]["with_queued_data"] += 1
        pct = max(snap.get("percentUseCount", 0), snap.get("percentUseBytes", 0))
        if pct >= NEAR_BACKPRESSURE_PERCENT:
            health["connections"]["near_backpressure"] += 1
            comp = c.get("component") or {}
            src = (comp.get("source") or {}).get("name")
            dst = (comp.get("destination") or {}).get("name")
            health["problems"].append(f"Connection {src} -> {dst} is {pct}% full")

    try:
        board = client.get("flow/bulletin-board", params={"groupId": group_id, "limit": 10})
        for b in (board.get("bulletinBoard") or {}).get("bulletins", []):
            bulletin = b.get("bulletin") or {}
            health["bulletins"].append(
                {
                    "level": bulletin.get("level"),
                    "source": bulletin.get("sourceName"),
                    "message": bulletin.get("message"),
                    "timestamp": bulletin.get("timestamp"),
                }
            )
            if bulletin.get("level") in ("ERROR", "WARN"):
                health["problems"].append(
                    f"{bulletin.get('level')} bulletin from {bulletin.get('sourceName')}: "
                    f"{bulletin.get('message')}"
                )
    except Exception as exc:
        health["bulletins_error"] = str(exc)[:200]

    if health["processors"]["invalid"] or health["controller_services"]["invalid"]:
        health["overall"] = "UNHEALTHY"
    elif health["problems"]:
        health["overall"] = "DEGRADED"
    else:
        health["overall"] = "HEALTHY"
    return health


# ------------------------------------------------------------------ register
def register(ctx: ToolContext) -> None:
    client = ctx.client
    core = ctx.tool("core")

    @core
    def get_nifi_version() -> dict:
        """NiFi version/build info, and whether this is NiFi 1.x or 2.x."""
        return client.version_info()

    @core
    def get_system_diagnostics() -> dict:
        """NiFi system health: heap, CPU load, uptime and repository utilisation."""
        snap = client.get("system-diagnostics")["systemDiagnostics"]["aggregateSnapshot"]
        return {
            "nifi_version": (snap.get("versionInfo") or {}).get("niFiVersion"),
            "uptime": snap.get("uptime"),
            "heap_utilization": snap.get("heapUtilization"),
            "heap_used": snap.get("usedHeap"),
            "heap_max": snap.get("maxHeap"),
            "available_processors": snap.get("availableProcessors"),
            "processor_load_average": snap.get("processorLoadAverage"),
            "flowfile_repo_utilization": (snap.get("flowFileRepositoryStorageUsage") or {}).get(
                "utilization"
            ),
            "content_repo_utilization": [
                r.get("utilization") for r in snap.get("contentRepositoryStorageUsage") or []
            ],
        }

    @core
    def get_root_process_group() -> dict:
        """Root process group id plus a summary of what it contains."""
        return group_summary(client, "root")

    @core
    def get_process_groups(group_id: str = "root") -> dict:
        """List the direct child process groups of a group (default: root)."""
        group_id = client.resolve_group_id(group_id)
        data = client.get(f"process-groups/{group_id}/process-groups")
        groups = [
            {
                "id": g.get("id"),
                "name": (g.get("component") or {}).get("name"),
                "running": g.get("runningCount", 0),
                "stopped": g.get("stoppedCount", 0),
                "invalid": g.get("invalidCount", 0),
                "disabled": g.get("disabledCount", 0),
                "revision": (g.get("revision") or {}).get("version"),
            }
            for g in data.get("processGroups", [])
        ]
        return {"group_id": group_id, "process_groups": groups}

    @core
    def get_flow_status(group_id: str = "root") -> dict:
        """Throughput and thread/queue statistics for a process group."""
        group_id = client.resolve_group_id(group_id)
        snap = client.get(f"flow/process-groups/{group_id}/status")["processGroupStatus"][
            "aggregateSnapshot"
        ]
        return {
            "group_id": group_id,
            "flowfiles_queued": snap.get("flowFilesQueued"),
            "bytes_queued": snap.get("bytesQueued"),
            "active_threads": snap.get("activeThreadCount"),
            "bytes_read_5min": snap.get("bytesRead"),
            "bytes_written_5min": snap.get("bytesWritten"),
            "flowfiles_in_5min": snap.get("flowFilesIn"),
            "flowfiles_out_5min": snap.get("flowFilesOut"),
        }

    @core
    def get_flow_summary(process_group_id: str = "root") -> dict:
        """Processor counts by state, connection count and total queued data of a group."""
        return group_summary(client, process_group_id)

    @core
    def get_flow_health_status(process_group_id: str = "root") -> dict:
        """Health report (HEALTHY | DEGRADED | UNHEALTHY): invalid processors/services, queues
        close to backpressure, recent WARN/ERROR bulletins. Direct children of the group only."""
        return flow_health(client, process_group_id)

    @core
    def list_processors(process_group_id: str = "root") -> dict:
        """List processors (direct children) of a process group with state and validation."""
        group_id = client.resolve_group_id(process_group_id)
        data = client.get(f"process-groups/{group_id}/processors")
        return {
            "process_group_id": group_id,
            "processors": [processor_summary(p) for p in data.get("processors", [])],
        }

    @core
    def list_connections(process_group_id: str = "root") -> dict:
        """List connections of a process group with queue depth and relationships."""
        group_id = client.resolve_group_id(process_group_id)
        data = client.get(f"process-groups/{group_id}/connections")
        return {
            "process_group_id": group_id,
            "connections": [connection_summary(c) for c in data.get("connections", [])],
        }

    @core
    def get_processor_details(processor_id: str) -> dict:
        """Full processor entity: properties, scheduling, relationships, validation, revision."""
        return client.entity("processor", processor_id)

    @core
    def get_processor_state(processor_id: str) -> dict:
        """Quick state check (RUNNING / STOPPED / DISABLED) for one processor."""
        entity = client.entity("processor", processor_id)
        comp = entity.get("component") or {}
        return {
            "processor_id": processor_id,
            "name": comp.get("name"),
            "state": comp.get("state"),
            "validation_status": comp.get("validationStatus"),
            "revision": (entity.get("revision") or {}).get("version"),
        }

    @core
    def get_processor_types(search: str | None = None, limit: int = 50) -> dict:
        """Processor types available on this NiFi. Use `search` (matches type, tags and
        description) — the full list has hundreds of entries."""
        types = client.get("flow/processor-types").get("processorTypes", [])
        if search:
            needle = search.lower()
            types = [
                t
                for t in types
                if needle in (t.get("type") or "").lower()
                or needle in (t.get("description") or "").lower()
                or any(needle in str(tag).lower() for tag in t.get("tags") or [])
            ]
        compact = [
            {
                "type": t.get("type"),
                "description": (t.get("description") or "")[:140],
                "bundle": (t.get("bundle") or {}).get("artifact"),
                "version": (t.get("bundle") or {}).get("version"),
            }
            for t in types[:limit]
        ]
        return {"total_matches": len(types), "returned": len(compact), "processor_types": compact}

    @core
    def get_connection_details(connection_id: str) -> dict:
        """Full connection entity: queue status, relationships, back-pressure settings."""
        return client.entity("connection", connection_id)

    @core
    def check_connection_queue(connection_id: str) -> dict:
        """Queue depth of a connection. A connection must be empty before it can be deleted."""
        entity = client.entity("connection", connection_id)
        snap = _snap(entity)
        return {
            "connection_id": connection_id,
            "flowfiles_queued": snap.get("flowFilesQueued", 0),
            "bytes_queued": snap.get("bytesQueued", 0),
            "percent_use_count": snap.get("percentUseCount", 0),
            "percent_use_bytes": snap.get("percentUseBytes", 0),
            "is_empty": snap.get("flowFilesQueued", 0) == 0,
        }

    @core
    def get_bulletins(
        after_ms: int | None = None, group_id: str | None = None, limit: int = 50
    ) -> dict:
        """Recent bulletins (warnings/errors). `after_ms`: only newer than this bulletin id
        cursor; `group_id`: restrict to one process group."""
        params: dict[str, Any] = {"limit": limit}
        if after_ms:
            params["after"] = after_ms
        if group_id:
            params["groupId"] = client.resolve_group_id(group_id)
        board = client.get("flow/bulletin-board", params=params)
        bulletins = []
        for b in (board.get("bulletinBoard") or {}).get("bulletins", []):
            bulletin = b.get("bulletin") or {}
            bulletins.append(
                {
                    "id": bulletin.get("id"),
                    "level": bulletin.get("level"),
                    "source_id": bulletin.get("sourceId"),
                    "source_name": bulletin.get("sourceName"),
                    "group_id": bulletin.get("groupId"),
                    "message": bulletin.get("message"),
                    "timestamp": bulletin.get("timestamp"),
                }
            )
        return {"count": len(bulletins), "bulletins": bulletins}

    @core
    def search_flow(query: str) -> dict:
        """Search processors, connections, groups, ports and services by name/property."""
        return client.get("flow/search-results", params={"q": query}).get("searchResultsDTO", {})

    # --- controller services ------------------------------------------------
    services = ctx.tool("services")

    @services
    def get_controller_services(process_group_id: str | None = None) -> dict:
        """List controller services. With a group id: services in scope of that group
        (including inherited ones); without: controller-level (global) services."""
        if process_group_id:
            group_id = client.resolve_group_id(process_group_id)
            data = client.get(f"flow/process-groups/{group_id}/controller-services")
        else:
            data = client.get("flow/controller/controller-services")
        return {
            "process_group_id": process_group_id,
            "controller_services": [service_summary(s) for s in data.get("controllerServices", [])],
        }

    @services
    def get_controller_service_details(service_id: str) -> dict:
        """Full controller service entity: properties, state, referencing components."""
        return client.entity("controller_service", service_id)

    @services
    def find_controller_services_by_type(process_group_id: str | None, service_type: str) -> dict:
        """Find existing services of a type (full class name or short name such as
        DBCPConnectionPool). Check this BEFORE creating a service to avoid duplicates."""
        listing = get_controller_services.__wrapped__(process_group_id)
        matches = [
            s
            for s in listing["controller_services"]
            if s["type"] == service_type or (s["type"] or "").endswith("." + service_type)
        ]
        return {"service_type": service_type, "count": len(matches), "matches": matches}

    # --- parameter contexts ---------------------------------------------------
    parameters = ctx.tool("parameters")

    @parameters
    def list_parameter_contexts() -> dict:
        """List parameter contexts with their parameter counts and bound process groups."""
        data = client.get("flow/parameter-contexts")
        contexts = []
        for pc in data.get("parameterContexts", []):
            comp = pc.get("component") or {}
            contexts.append(
                {
                    "id": pc.get("id"),
                    "name": comp.get("name"),
                    "description": comp.get("description"),
                    "parameter_count": len(comp.get("parameters") or []),
                    "bound_process_groups": [
                        g.get("id") for g in comp.get("boundProcessGroups") or []
                    ],
                    "revision": (pc.get("revision") or {}).get("version"),
                }
            )
        return {"parameter_contexts": contexts}

    @parameters
    def get_parameter_context_details(context_id: str) -> dict:
        """One parameter context with all its parameters (sensitive values are never returned)."""
        return client.entity("parameter_context", context_id)

    # --- ports -----------------------------------------------------------------
    ports = ctx.tool("ports")

    @ports
    def list_input_ports(process_group_id: str) -> dict:
        """Input ports of a process group."""
        group_id = client.resolve_group_id(process_group_id)
        data = client.get(f"process-groups/{group_id}/input-ports")
        return {
            "process_group_id": group_id,
            "input_ports": [port_summary(p) for p in data.get("inputPorts", [])],
        }

    @ports
    def list_output_ports(process_group_id: str) -> dict:
        """Output ports of a process group."""
        group_id = client.resolve_group_id(process_group_id)
        data = client.get(f"process-groups/{group_id}/output-ports")
        return {
            "process_group_id": group_id,
            "output_ports": [port_summary(p) for p in data.get("outputPorts", [])],
        }
