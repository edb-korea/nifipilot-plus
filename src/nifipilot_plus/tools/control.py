"""Run-state control for processors, groups and queues (MCP_MODE=full only)."""

import time

from .context import ToolContext

_DROP_POLL_SECONDS = 30
_TERMINATE_WAIT_SECONDS = 5


def _brief(entity: dict) -> dict:
    return {"id": entity.get("id"), "name": (entity.get("component") or {}).get("name")}


def register(ctx: ToolContext) -> None:
    client = ctx.client
    flow = ctx.tool("flow", "control")

    @flow
    def start_processor(processor_id: str, version: int | None = None) -> dict:
        """Start a processor. It must be valid (no validation errors)."""
        result = client.set_run_status("processor", processor_id, "RUNNING", version)
        return {**_brief(result), "requested_state": "RUNNING"}

    @flow
    def stop_processor(processor_id: str, version: int | None = None) -> dict:
        """Stop a running processor."""
        result = client.set_run_status("processor", processor_id, "STOPPED", version)
        return {**_brief(result), "requested_state": "STOPPED"}

    @flow
    def start_process_group(process_group_id: str = "root") -> dict:
        """Start every processor and port in a group, including child groups (recursive)."""
        client.schedule_group(process_group_id, "RUNNING")
        return {"process_group_id": process_group_id, "requested_state": "RUNNING"}

    @flow
    def stop_process_group(process_group_id: str = "root") -> dict:
        """Stop every processor and port in a group, including child groups (recursive)."""
        client.schedule_group(process_group_id, "STOPPED")
        return {"process_group_id": process_group_id, "requested_state": "STOPPED"}

    def _bulk_processors(process_group_id: str, target: str) -> dict:
        group_id = client.resolve_group_id(process_group_id)
        skip_status = "running" if target == "RUNNING" else "stopped"
        done_key = "started" if target == "RUNNING" else "stopped"
        skipped_key = "already_running" if target == "RUNNING" else "already_stopped"
        results: dict = {done_key: [], skipped_key: [], "failed": []}
        for p in client.get(f"process-groups/{group_id}/processors").get("processors", []):
            info = {"id": p["id"], "name": (p.get("component") or {}).get("name")}
            status = ((p.get("status") or {}).get("runStatus") or "").lower()
            if status == skip_status:
                results[skipped_key].append(info)
                continue
            try:
                client.set_run_status(
                    "processor", p["id"], target, (p.get("revision") or {}).get("version")
                )
                results[done_key].append(info)
            except Exception as exc:
                results["failed"].append({**info, "error": str(exc)[:300]})
        return results

    @flow
    def start_all_processors_in_group(process_group_id: str) -> dict:
        """Start each processor that is a DIRECT child of the group, reporting per processor
        (started / already_running / failed). Use start_process_group for a recursive start."""
        return _bulk_processors(process_group_id, "RUNNING")

    @flow
    def stop_all_processors_in_group(process_group_id: str) -> dict:
        """Stop each processor that is a DIRECT child of the group, reporting per processor
        (stopped / already_stopped / failed). Use stop_process_group for a recursive stop."""
        return _bulk_processors(process_group_id, "STOPPED")

    @flow
    def terminate_processor(processor_id: str) -> dict:
        """LAST RESORT for a processor that will not stop: requests a stop, waits briefly, and
        if threads are still active force-terminates them (may lose in-flight work)."""
        client.set_run_status("processor", processor_id, "STOPPED")
        deadline = time.monotonic() + _TERMINATE_WAIT_SECONDS
        threads = 0
        while True:
            entity = client.entity("processor", processor_id)
            threads = ((entity.get("status") or {}).get("aggregateSnapshot") or {}).get(
                "activeThreadCount", 0
            )
            if threads == 0 or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        if threads == 0:
            return {"processor_id": processor_id, "result": "stopped_without_termination"}
        client.delete(f"processors/{processor_id}/threads")
        return {
            "processor_id": processor_id,
            "result": "threads_terminated",
            "threads_at_termination": threads,
        }

    @flow
    def empty_connection_queue(connection_id: str) -> dict:
        """PERMANENTLY drop all flowfiles queued on a connection (data loss). Waits up to 30 s
        for NiFi to finish. Set MCP_DRY_RUN=true to preview."""
        request = client.post(f"flowfile-queues/{connection_id}/drop-requests")["dropRequest"]
        drop_id = request["id"]
        deadline = time.monotonic() + _DROP_POLL_SECONDS
        while not request.get("finished") and time.monotonic() < deadline:
            time.sleep(0.5)
            request = client.get(f"flowfile-queues/{connection_id}/drop-requests/{drop_id}")[
                "dropRequest"
            ]
        client.delete(f"flowfile-queues/{connection_id}/drop-requests/{drop_id}")
        return {
            "connection_id": connection_id,
            "finished": bool(request.get("finished")),
            "dropped_count": request.get("droppedCount"),
            "dropped_size": request.get("droppedSize"),
        }
