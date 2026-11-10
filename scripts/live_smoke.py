"""End-to-end smoke test against a live NiFi (not part of the unit-test suite).

Needs a running NiFi and a .env (or environment) with NIFI_URL / NIFI_USERNAME / NIFI_PASSWORD.
It forces MCP_MODE=full, builds a throw-away flow under its own process group, exercises most
tools through the real MCP layer, and removes everything it created.

    .venv/bin/python scripts/live_smoke.py
"""

import asyncio
import os
import sys
import time
import traceback

from fastmcp import Client

os.environ["MCP_MODE"] = "full"
os.environ.setdefault("MCP_AUDIT_LOG", "false")

from nifipilot_plus.config import Settings  # noqa: E402
from nifipilot_plus.server import create_server  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


async def main() -> int:
    settings = Settings()
    mcp, ctx = create_server(settings)
    print(f"target {settings.api_base}  tools={len(ctx.registered)}")

    async with Client(mcp) as c:

        async def call(tool: str, **args):
            result = await c.call_tool(tool, args)
            return result.structured_content

        async def step(label: str, coro_fn):
            try:
                value = await coro_fn()
                RESULTS.append((label, True, ""))
                print(f"  ok    {label}")
                return value
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {str(exc)[:300]}"
                RESULTS.append((label, False, detail))
                print(f"  FAIL  {label}\n        {detail}")
                if os.environ.get("SMOKE_TRACE"):
                    traceback.print_exc()
                return None

        async def step_conflict(label: str, coro_fn):
            """NiFi must answer 409 here (a port without connections cannot start)."""
            try:
                await coro_fn()
            except Exception as exc:  # noqa: BLE001
                ok = "[409]" in str(exc)
                RESULTS.append((label, ok, "" if ok else str(exc)[:300]))
                print(f"  {'ok   ' if ok else 'FAIL '} {label} (expected 409)")
                return
            RESULTS.append((label, False, "expected a 409 but the call succeeded"))
            print(f"  FAIL  {label}: expected a 409 but the call succeeded")

        ids: dict[str, str] = {}

        # ---------------------------------------------------------------- reads
        v = await step("get_nifi_version", lambda: call("get_nifi_version"))
        if v:
            print(f"        NiFi {v['version']} (2.x={v['is_nifi_2x']})")
        await step("get_system_diagnostics", lambda: call("get_system_diagnostics"))
        await step("get_root_process_group", lambda: call("get_root_process_group"))
        await step("get_process_groups", lambda: call("get_process_groups"))
        await step("get_flow_status(root)", lambda: call("get_flow_status"))
        await step("get_flow_summary(root)", lambda: call("get_flow_summary"))
        await step("get_flow_health_status(root)", lambda: call("get_flow_health_status"))
        await step("get_bulletins", lambda: call("get_bulletins"))
        await step("search_flow", lambda: call("search_flow", query="x"))
        types = await step(
            "get_processor_types(search)",
            lambda: call("get_processor_types", search="GenerateFlowFile"),
        )
        if types:
            print(f"        {types['total_matches']} match(es)")
        await step("get_controller_services(global)", lambda: call("get_controller_services"))
        await step("list_parameter_contexts", lambda: call("list_parameter_contexts"))
        await step(
            "analyze_flow_build_request",
            lambda: call("analyze_flow_build_request", user_request="kafka to s3"),
        )

        # -------------------------------------------------------------- build
        flow = await step(
            "start_new_flow", lambda: call("start_new_flow", flow_name="nifipilot-smoke")
        )
        if not flow:
            return report()
        pg = flow["process_group"]["id"]
        ids["pg"] = pg

        gen = await step(
            "create_processor(GenerateFlowFile)",
            lambda: call(
                "create_processor",
                name="gen",
                processor_type="org.apache.nifi.processors.standard.GenerateFlowFile",
                process_group_id=pg,
            ),
        )
        log = await step(
            "create_processor(LogAttribute)",
            lambda: call(
                "create_processor",
                name="log",
                processor_type="org.apache.nifi.processors.standard.LogAttribute",
                process_group_id=pg,
                x=200.0,
            ),
        )
        if not (gen and log):
            return await cleanup(call, ids)
        await step(
            "update_processor_config(log)",
            lambda: call(
                "update_processor_config",
                processor_id=log["id"],
                config={"config": {"autoTerminatedRelationships": ["success"]}},
            ),
        )
        await step(
            "update_processor_config(gen schedule)",
            lambda: call(
                "update_processor_config",
                processor_id=gen["id"],
                config={"config": {"schedulingPeriod": "2 sec"}},
            ),
        )
        conn = await step(
            "create_connection(gen->log)",
            lambda: call(
                "create_connection",
                source_id=gen["id"],
                destination_id=log["id"],
                relationships="success",
            ),
        )
        await step("list_processors", lambda: call("list_processors", process_group_id=pg))
        await step("list_connections", lambda: call("list_connections", process_group_id=pg))
        await step(
            "get_processor_details", lambda: call("get_processor_details", processor_id=gen["id"])
        )
        await step(
            "get_processor_state", lambda: call("get_processor_state", processor_id=gen["id"])
        )
        if conn:
            await step(
                "get_connection_details",
                lambda: call("get_connection_details", connection_id=conn["id"]),
            )

        # ------------------------------------------------------------ control
        await step(
            "start_all_processors_in_group",
            lambda: call("start_all_processors_in_group", process_group_id=pg),
        )
        time.sleep(7)
        if conn:
            q = await step(
                "check_connection_queue",
                lambda: call("check_connection_queue", connection_id=conn["id"]),
            )
            if q:
                print(f"        queue: {q}")
        await step("get_flow_status(pg)", lambda: call("get_flow_status", group_id=pg))
        h = await step(
            "get_flow_health_status(pg)",
            lambda: call("get_flow_health_status", process_group_id=pg),
        )
        if h:
            print(f"        health: {h['overall']} {h['processors']}")
        await step(
            "stop_process_group (recursive)",
            lambda: call("stop_process_group", process_group_id=pg),
        )
        time.sleep(3)
        await step("start_processor(gen)", lambda: call("start_processor", processor_id=gen["id"]))
        time.sleep(3)
        await step("stop_processor(gen)", lambda: call("stop_processor", processor_id=gen["id"]))
        time.sleep(2)
        await step(
            "terminate_processor(gen)", lambda: call("terminate_processor", processor_id=gen["id"])
        )
        if conn:
            await step(
                "empty_connection_queue",
                lambda: call("empty_connection_queue", connection_id=conn["id"]),
            )

        # ----------------------------------------------------------- services
        svc = await step(
            "create_controller_service",
            lambda: call(
                "create_controller_service",
                service_type="org.apache.nifi.json.JsonTreeReader",
                name="smoke-json-reader",
                process_group_id=pg,
            ),
        )
        await step(
            "find_controller_services_by_type",
            lambda: call(
                "find_controller_services_by_type",
                process_group_id=pg,
                service_type="JsonTreeReader",
            ),
        )
        if svc:
            await step(
                "get_controller_service_details",
                lambda: call("get_controller_service_details", service_id=svc["id"]),
            )
            await step(
                "enable_controller_service",
                lambda: call("enable_controller_service", service_id=svc["id"]),
            )
            time.sleep(2)
            await step(
                "disable_controller_service",
                lambda: call("disable_controller_service", service_id=svc["id"]),
            )
            time.sleep(2)
            await step(
                "update_controller_service_properties",
                lambda: call(
                    "update_controller_service_properties",
                    service_id=svc["id"],
                    properties={"Max String Length": "10 MB"},
                ),
            )
            await step(
                "enable_all_controller_services_in_group",
                lambda: call("enable_all_controller_services_in_group", process_group_id=pg),
            )
            time.sleep(2)
            await step(
                "disable_controller_service(again)",
                lambda: call("disable_controller_service", service_id=svc["id"]),
            )
            time.sleep(2)
            await step(
                "delete_controller_service",
                lambda: call("delete_controller_service", service_id=svc["id"]),
            )

        # --------------------------------------------------------- parameters
        pc = await step(
            "create_parameter_context",
            lambda: call(
                "create_parameter_context",
                name="smoke-params",
                description="temporary",
                parameters=[
                    {"name": "db.host", "value": "localhost"},
                    {"name": "db.password", "value": "s3cret", "sensitive": True},
                ],
            ),
        )
        if pc:
            ids["pc"] = pc["id"]
            await step(
                "get_parameter_context_details",
                lambda: call("get_parameter_context_details", context_id=pc["id"]),
            )
            await step(
                "update_parameter_context",
                lambda: call(
                    "update_parameter_context",
                    context_id=pc["id"],
                    parameters=[{"name": "db.port", "value": "5432"}],
                ),
            )
            await step(
                "apply_parameter_context_to_process_group",
                lambda: call(
                    "apply_parameter_context_to_process_group",
                    process_group_id=pg,
                    context_id=pc["id"],
                ),
            )

        # -------------------------------------------------------------- ports
        port = await step(
            "create_input_port",
            lambda: call("create_input_port", name="smoke-in", process_group_id=pg),
        )
        if port:
            await step("list_input_ports", lambda: call("list_input_ports", process_group_id=pg))
            await step(
                "update_input_port",
                lambda: call("update_input_port", port_id=port["id"], name="smoke-in2"),
            )
            await step_conflict(
                "start_input_port", lambda: call("start_input_port", port_id=port["id"])
            )
            await step("stop_input_port", lambda: call("stop_input_port", port_id=port["id"]))
            await step("delete_input_port", lambda: call("delete_input_port", port_id=port["id"]))
        oport = await step(
            "create_output_port",
            lambda: call("create_output_port", name="smoke-out", process_group_id=pg),
        )
        if oport:
            await step("list_output_ports", lambda: call("list_output_ports", process_group_id=pg))
            await step(
                "update_output_port",
                lambda: call("update_output_port", port_id=oport["id"], name="smoke-out2"),
            )
            await step_conflict(
                "start_output_port", lambda: call("start_output_port", port_id=oport["id"])
            )
            await step("stop_output_port", lambda: call("stop_output_port", port_id=oport["id"]))
            await step(
                "delete_output_port", lambda: call("delete_output_port", port_id=oport["id"])
            )

        # ------------------------------------------------------------ cleanup
        await step(
            "update_process_group_name",
            lambda: call(
                "update_process_group_name", process_group_id=pg, name="nifipilot-smoke-renamed"
            ),
        )
        return await cleanup(call, ids, conn=conn, gen=gen, log=log)


async def cleanup(call, ids, conn=None, gen=None, log=None) -> int:
    print("cleanup")

    async def attempt(label, coro_fn):
        try:
            await coro_fn()
            print(f"  ok    {label}")
        except Exception as exc:  # noqa: BLE001
            RESULTS.append((f"cleanup:{label}", False, str(exc)[:300]))
            print(f"  FAIL  cleanup:{label} {str(exc)[:200]}")

    pg = ids.get("pg")
    if pg:
        await attempt("stop_process_group", lambda: call("stop_process_group", process_group_id=pg))
        time.sleep(2)
    if conn:
        await attempt(
            "empty_connection_queue",
            lambda: call("empty_connection_queue", connection_id=conn["id"]),
        )
        await attempt(
            "delete_connection", lambda: call("delete_connection", connection_id=conn["id"])
        )
    for proc in (gen, log):
        if proc:
            await attempt(
                f"delete_processor({proc['id'][:8]})",
                lambda p=proc: call("delete_processor", processor_id=p["id"]),
            )
    if pg:
        await attempt(
            "delete_process_group", lambda: call("delete_process_group", process_group_id=pg)
        )
    if ids.get("pc"):
        await attempt(
            "delete_parameter_context",
            lambda: call("delete_parameter_context", context_id=ids["pc"]),
        )
    return report()


def report() -> int:
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} steps ok")
    for label, _, detail in failed:
        print(f"  FAIL {label}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
