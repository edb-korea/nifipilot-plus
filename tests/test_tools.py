import json
import re

import pytest

from nifipilot_plus.config import Settings
from nifipilot_plus.security import REDACTED, Audit, redact

from .conftest import BASE, call_tool, list_tool_names, request_bodies

WRITE_SAMPLE = {"create_processor", "delete_processor", "start_processor", "empty_connection_queue"}


# --------------------------------------------------------------- registration
def test_readonly_mode_hides_every_write_and_control_tool(make_server):
    mcp, ctx = make_server(mcp_mode="readonly")
    names = list_tool_names(mcp)
    assert not names & WRITE_SAMPLE
    assert {"list_processors", "get_flow_health_status", "get_nifi_version"} <= names
    assert len(names) == len(ctx.registered)


def test_full_mode_exposes_write_and_control_tools(make_server):
    mcp, _ = make_server(mcp_mode="full")
    assert WRITE_SAMPLE <= list_tool_names(mcp)


def test_toolsets_limit_what_is_registered(make_server):
    mcp, _ = make_server(mcp_mode="full", mcp_toolsets="ports")
    names = list_tool_names(mcp)
    assert "create_input_port" in names and "list_input_ports" in names
    assert "create_controller_service" not in names
    assert "list_processors" in names  # core is always on


def test_unknown_toolset_is_rejected():
    with pytest.raises(ValueError, match="Unknown MCP_TOOLSETS"):
        Settings(_env_file=None, mcp_toolsets="core,bogus")


# --------------------------------------------------------------------- reads
def test_list_processors_returns_compact_summaries(make_server, login):
    login.add_response(
        url=f"{BASE}/process-groups/g1/processors",
        json={
            "processors": [
                {
                    "id": "p1",
                    "revision": {"version": 2},
                    "component": {
                        "name": "Fetch",
                        "type": "org.apache.nifi.processors.standard.GetFile",
                        "state": "STOPPED",
                        "validationErrors": ["Input Directory is required"],
                        "config": {"properties": {"Password": "hunter2"}},
                    },
                    "status": {
                        "runStatus": "Invalid",
                        "aggregateSnapshot": {"activeThreadCount": 0},
                    },
                }
            ]
        },
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "list_processors", process_group_id="g1")
    (p,) = data["processors"]
    assert p["name"] == "Fetch" and p["run_status"] == "Invalid" and p["revision"] == 2
    assert p["validation_errors"] == ["Input Directory is required"]
    assert "config" not in p


def test_processor_details_are_redacted(make_server, login):
    login.add_response(
        url=f"{BASE}/processors/p1",
        json={"id": "p1", "component": {"config": {"properties": {"Db Password": "hunter2"}}}},
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "get_processor_details", processor_id="p1")
    assert data["component"]["config"]["properties"]["Db Password"] == REDACTED


def test_health_reports_unhealthy_for_invalid_processor(make_server, login):
    login.add_response(
        url=f"{BASE}/process-groups/g1/processors",
        json={
            "processors": [
                {
                    "id": "p1",
                    "component": {"name": "Bad", "validationErrors": ["missing property"]},
                    "status": {"runStatus": "Invalid"},
                },
                {"id": "p2", "component": {"name": "Ok"}, "status": {"runStatus": "Running"}},
            ]
        },
    )
    login.add_response(
        url=f"{BASE}/flow/process-groups/g1/controller-services", json={"controllerServices": []}
    )
    login.add_response(
        url=f"{BASE}/process-groups/g1/connections",
        json={
            "connections": [
                {
                    "component": {"source": {"name": "A"}, "destination": {"name": "B"}},
                    "status": {"aggregateSnapshot": {"flowFilesQueued": 9, "percentUseCount": 90}},
                }
            ]
        },
    )
    login.add_response(
        url=re.compile(rf"{re.escape(BASE)}/flow/bulletin-board.*"),
        json={"bulletinBoard": {"bulletins": []}},
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "get_flow_health_status", process_group_id="g1")
    assert data["overall"] == "UNHEALTHY"
    assert data["processors"] == {
        "total": 2,
        "running": 1,
        "stopped": 0,
        "invalid": 1,
        "disabled": 0,
    }
    assert data["connections"]["near_backpressure"] == 1
    assert any("Bad" in p for p in data["problems"])


def test_find_controller_services_matches_short_type_names(make_server, login):
    login.add_response(
        url=f"{BASE}/flow/process-groups/g1/controller-services",
        json={
            "controllerServices": [
                {
                    "id": "s1",
                    "component": {"name": "pg", "type": "org.apache.nifi.dbcp.DBCPConnectionPool"},
                },
                {
                    "id": "s2",
                    "component": {"name": "json", "type": "org.apache.nifi.json.JsonTreeReader"},
                },
            ]
        },
    )
    mcp, _ = make_server()
    data = call_tool(
        mcp,
        "find_controller_services_by_type",
        process_group_id="g1",
        service_type="DBCPConnectionPool",
    )
    assert data["count"] == 1 and data["matches"][0]["id"] == "s1"


def test_processor_types_search_is_filtered_and_compact(make_server, login):
    login.add_response(
        url=f"{BASE}/flow/processor-types",
        json={
            "processorTypes": [
                {
                    "type": "org.x.PutKafka",
                    "description": "kafka sink",
                    "tags": ["kafka"],
                    "bundle": {"artifact": "k", "version": "1"},
                },
                {"type": "org.x.GetFile", "description": "files", "tags": ["file"], "bundle": {}},
            ]
        },
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "get_processor_types", search="kafka")
    assert data["total_matches"] == 1 and data["processor_types"][0]["type"] == "org.x.PutKafka"


# -------------------------------------------------------------------- writes
def test_start_processor_autofetches_revision(make_server, login):
    login.add_response(url=f"{BASE}/processors/p1", json={"revision": {"version": 4}})
    login.add_response(
        method="PUT",
        url=f"{BASE}/processors/p1/run-status",
        json={"id": "p1", "component": {"name": "Fetch"}},
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "start_processor", processor_id="p1")
    assert data["requested_state"] == "RUNNING" and data["name"] == "Fetch"
    (body,) = request_bodies(login, "PUT", "/run-status")
    assert body["revision"]["version"] == 4 and body["state"] == "RUNNING"


def test_bulk_start_skips_running_processors_and_reports_failures(make_server, login):
    login.add_response(
        url=f"{BASE}/process-groups/g1/processors",
        json={
            "processors": [
                {
                    "id": "a",
                    "revision": {"version": 1},
                    "component": {"name": "A"},
                    "status": {"runStatus": "Running"},
                },
                {
                    "id": "b",
                    "revision": {"version": 2},
                    "component": {"name": "B"},
                    "status": {"runStatus": "Stopped"},
                },
                {
                    "id": "c",
                    "revision": {"version": 3},
                    "component": {"name": "C"},
                    "status": {"runStatus": "Stopped"},
                },
            ]
        },
    )
    login.add_response(method="PUT", url=f"{BASE}/processors/b/run-status", json={"id": "b"})
    login.add_response(
        method="PUT", url=f"{BASE}/processors/c/run-status", status_code=409, text="invalid"
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "start_all_processors_in_group", process_group_id="g1")
    assert [p["id"] for p in data["already_running"]] == ["a"]
    assert [p["id"] for p in data["started"]] == ["b"]
    assert data["failed"][0]["id"] == "c" and "409" in data["failed"][0]["error"]


def test_create_connection_looks_up_owning_groups(make_server, login):
    login.add_response(url=f"{BASE}/processors/a", json={"component": {"parentGroupId": "g1"}})
    login.add_response(url=f"{BASE}/processors/b", json={"component": {"parentGroupId": "g1"}})
    login.add_response(
        method="POST", url=f"{BASE}/process-groups/g1/connections", json={"id": "c1"}
    )
    mcp, _ = make_server()
    data = call_tool(
        mcp,
        "create_connection",
        source_id="a",
        destination_id="b",
        relationships="success, failure",
    )
    assert data == {"id": "c1", "owner_group_id": "g1", "relationships": ["success", "failure"]}
    (body,) = request_bodies(login, "POST", "/connections")
    assert body["component"]["source"] == {"id": "a", "groupId": "g1", "type": "PROCESSOR"}
    assert body["component"]["selectedRelationships"] == ["success", "failure"]


def test_create_parameter_context_builds_parameter_entities(make_server, login):
    login.add_response(method="POST", url=f"{BASE}/parameter-contexts", json={"id": "pc1"})
    mcp, _ = make_server()
    call_tool(
        mcp,
        "create_parameter_context",
        name="Prod",
        parameters='[{"name":"db.host","value":"h"},{"name":"db.pw","value":"x","sensitive":true}]',
    )
    (body,) = request_bodies(login, "POST", "/parameter-contexts")
    params = [p["parameter"] for p in body["component"]["parameters"]]
    assert params[0] == {"name": "db.host", "value": "h", "sensitive": False}
    assert params[1]["sensitive"] is True


def test_empty_connection_queue_polls_until_finished(make_server, login, monkeypatch):
    monkeypatch.setattr("nifipilot_plus.tools.control.time.sleep", lambda _s: None)
    queue = f"{BASE}/flowfile-queues/c1/drop-requests"
    login.add_response(
        method="POST", url=queue, json={"dropRequest": {"id": "d1", "finished": False}}
    )
    login.add_response(
        url=f"{queue}/d1", json={"dropRequest": {"id": "d1", "finished": True, "droppedCount": 12}}
    )
    login.add_response(method="DELETE", url=f"{queue}/d1", json={})
    mcp, _ = make_server()
    data = call_tool(mcp, "empty_connection_queue", connection_id="c1")
    assert data["finished"] is True and data["dropped_count"] == 12


def test_dry_run_makes_no_nifi_call(make_server, httpx_mock):
    # httpx_mock has no responses registered: any HTTP request would fail the test
    mcp, _ = make_server(mcp_dry_run=True)
    data = call_tool(mcp, "delete_processor", processor_id="p1")
    assert data["dry_run"] is True and data["tool"] == "delete_processor"


def test_start_new_flow_creates_group_under_root(make_server, login):
    login.add_response(
        url=f"{BASE}/flow/process-groups/root", json={"processGroupFlow": {"id": "root-id"}}
    )
    login.add_response(
        method="POST", url=f"{BASE}/process-groups/root-id/process-groups", json={"id": "new-pg"}
    )
    mcp, _ = make_server()
    data = call_tool(mcp, "start_new_flow", flow_name="Ingest")
    assert data["process_group"] == {"id": "new-pg", "name": "Ingest", "parent_group_id": "root-id"}


# ------------------------------------------------------------ security/audit
def test_redact_masks_sensitive_keys_and_truncates_lists():
    data = {"password": "x", "nested": {"apiToken": "y", "ok": 1}, "items": list(range(205))}
    out = redact(data)
    assert out["password"] == REDACTED and out["nested"]["apiToken"] == REDACTED
    assert out["nested"]["ok"] == 1
    assert out["items"][-1] == {"truncated": True, "omitted_count": 5} and len(out["items"]) == 201


def test_audit_log_records_calls_with_redacted_params(tmp_path, make_server, login):
    path = tmp_path / "audit.log"
    login.add_response(url=f"{BASE}/processors/p1", json={"id": "p1"})
    mcp, _ = make_server(mcp_audit_log=True, mcp_audit_log_path=str(path))
    call_tool(mcp, "get_processor_details", processor_id="p1")
    entry = json.loads(path.read_text().splitlines()[0])
    assert entry["tool"] == "get_processor_details" and entry["result"] == "ok"
    assert entry["params"] == {"processor_id": "p1"}


def test_audit_redacts_secret_parameters(tmp_path):
    audit = Audit(True, str(tmp_path / "a.log"))
    audit.log("t", {"password": "p", "name": "n"}, "ok")
    assert json.loads((tmp_path / "a.log").read_text())["params"]["password"] == REDACTED


# -------------------------------------------------------------------- guides
def test_guides_work_without_nifi(make_server, httpx_mock):
    mcp, _ = make_server(mcp_mode="readonly")
    found = call_tool(mcp, "analyze_flow_build_request", user_request="Move data from Kafka to S3")
    assert found["pattern_found"] is True
    cfg = call_tool(mcp, "check_configuration")
    assert cfg["is_valid"] is True and cfg["mode"] == "readonly"
    assert cfg["nifi_api_base"] == BASE


def test_workflow_guide_references_this_servers_tools(make_server):
    mcp, ctx = make_server()
    from nifipilot_plus.guides.best_practices import NiFiBestPractices

    text = NiFiBestPractices.get_recommended_workflow_for_request("load csv files into postgres")
    assert "start_new_flow" in text
    for tool in ("create_controller_service", "update_processor_config", "get_flow_health_status"):
        assert tool in ctx.registered
