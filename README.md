# nifipilot-plus

An [MCP](https://modelcontextprotocol.io) server that lets AI agents inspect, build and operate
**Apache NiFi** flows — on plain NiFi with username/password login (no Knox/Cloudera gateway
required).

It merges two open-source projects:

| Project | What it contributes here |
|---|---|
| [Giojacke/mcp-nifipilot](https://github.com/Giojacke/mcp-nifipilot) (MIT) | the readonly/full mode, dry-run and audit-log design (ideas only, no code or text copied) |
| [cloudera/NiFi-MCP-Server](https://github.com/cloudera/NiFi-MCP-Server) (Apache-2.0) | the broad tool catalogue (controller services, parameter contexts, ports, bulk operations, health, flow-pattern guides) — **without** its Knox authentication |

Result: **29 read-only tools** and **66 tools in full mode**, one HTTP client, NiFi 1.x/2.x
compatible endpoints. See [NOTICE](NOTICE) for provenance and licenses.

> **Status: early (0.1.0).** 34 unit tests run against a mocked NiFi REST API, and
> `scripts/live_smoke.py` exercised 59 tool calls (build, run, stop and delete a flow, controller
> services, parameter contexts, ports) against a real **single-node NiFi 2.2.0**, as did the
> Docker demo below. The same 59 calls also passed against **NiFi 2.11.0** (the sidecar compose
> setup below). **NiFi 1.x, clustered NiFi, LDAP/OIDC logins and the Kubernetes manifest have
> not been tested.** Start with `MCP_MODE=readonly`.

## Quick start

```bash
git clone https://github.com/edb-korea/nifipilot-plus && cd nifipilot-plus
uv venv && uv pip install -e .          # Python 3.11+
cp .env.example .env                    # then edit NIFI_URL / NIFI_PASSWORD
```

Claude Code (stdio):

```bash
claude mcp add nifi \
  -e NIFI_URL=https://localhost:8443 -e NIFI_USERNAME=admin \
  -e NIFI_PASSWORD='<password>' -e NIFI_VERIFY_SSL=false -e MCP_MODE=readonly \
  -- /path/to/nifipilot-plus/.venv/bin/nifipilot-plus
```

Any MCP client config (stdio):

```json
{
  "mcpServers": {
    "nifi": {
      "command": "/path/to/nifipilot-plus/.venv/bin/nifipilot-plus",
      "env": {
        "NIFI_URL": "https://localhost:8443",
        "NIFI_USERNAME": "admin",
        "NIFI_PASSWORD": "<password>",
        "NIFI_VERIFY_SSL": "false",
        "MCP_MODE": "readonly"
      }
    }
  }
}
```

Docker demo — NiFi (default 2.11.0) with the MCP server as a **sidecar** container that shares
NiFi's network namespace (it reaches NiFi at `https://localhost:8443`):

```bash
cp .env.example .env     # set NIFI_PASSWORD (min 12 chars)
docker compose up --build                  # NIFI_VERSION=2.2.0 docker compose up ... for another release
claude mcp add --transport http nifi http://localhost:8000/mcp
```

Both ports are published on `127.0.0.1` only; the MCP endpoint has no authentication.

### Kubernetes / k3s: one Pod, two containers

[deploy/k3s/nifi-with-mcp.yaml](deploy/k3s/nifi-with-mcp.yaml) runs NiFi and the MCP server as
sidecar containers of one Pod (same `localhost`), with a PVC for NiFi's repositories, a Secret
for the credentials, a ClusterIP Service for NiFi and an internal-only Service for MCP. The
header of the file lists the steps (build/import the image, create the Secret, apply,
`kubectl port-forward`). Keep the MCP Service internal and reach it with `port-forward`.
It was deployed to a real k3s (running in Docker) and passed the live smoke test through
`kubectl port-forward`. For a step-by-step AWS/k3s guide with build, import and verify scripts
see [nifi-mcp-k3s-deploy](https://github.com/edb-korea/nifi-mcp-k3s-deploy).

## Configuration

All settings are environment variables (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `NIFI_URL` | `https://localhost:8443` | NiFi base URL (`/nifi-api` is appended) |
| `NIFI_USERNAME` / `NIFI_PASSWORD` | `admin` / — | Login via `POST /access/token`; JWT renewed on 401. Empty password = no auth (unsecured NiFi 1.x). Single-user passwords need ≥ 12 chars |
| `NIFI_VERIFY_SSL` | `true` | `false` for a local self-signed certificate |
| `NIFI_CA_BUNDLE` | — | CA bundle path (preferred over disabling verification) |
| `NIFI_PROXY_CONTEXT_PATH` | — | Sent as `X-ProxyContextPath` behind a path-prefix proxy |
| `NIFI_TIMEOUT` / `NIFI_MAX_RETRIES` | `30` / `3` | HTTP timeout (s), attempts for transient failures |
| `MCP_MODE` | `readonly` | `readonly` or `full` (enables write/control tools) |
| `MCP_TOOLSETS` | `all` | Subset of `core,flow,services,parameters,ports,guides` (`core` is always on) |
| `MCP_DRY_RUN` | `false` | Write/control tools only report what they would do |
| `MCP_AUDIT_LOG` / `MCP_AUDIT_LOG_PATH` | `true` / `./logs/audit.log` | JSON-lines log of every call (secrets redacted) |
| `MCP_TRANSPORT` | `stdio` | `stdio`, `sse` or `http` (streamable HTTP, path `/mcp`) |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | Bind address for `sse`/`http` |

NiFi validates the HTTP `Host` header. Whatever `host:port` you use to reach NiFi (including
in-cluster service names) must be listed in NiFi's `nifi.web.proxy.host`
(container env `NIFI_WEB_PROXY_HOST`).

## Safety model

- **Read-only by default.** In `readonly` mode write/control tools are not registered at all,
  so the model never sees them.
- **Toolsets** keep the tool list small: `MCP_TOOLSETS=core,guides` exposes 22 tools in full
  mode instead of 66.
- **Dry run** (`MCP_DRY_RUN=true`) short-circuits every write/control call.
- **Audit log** records every call with its parameters (sensitive keys redacted) and outcome.
- **Redaction**: password/secret/token-like keys are masked in all tool output and large lists
  are truncated.
- **Optimistic locking made easy**: every write tool takes an optional `version`; when omitted
  the current revision is fetched, so the model does not juggle revisions. NiFi's `409`
  conflicts still surface with a hint.
- Retries only for transient failures (connection errors, 429/502/503/504); a POST is never
  retried after it may have reached NiFi, so creates are not duplicated.
- The MCP endpoint itself has **no authentication**: use stdio, or bind to localhost and
  tunnel/port-forward.

## Tools

**Read (29, always available)**

| Toolset | Tools |
|---|---|
| core | `get_nifi_version` `get_system_diagnostics` `get_root_process_group` `get_process_groups` `get_flow_status` `get_flow_summary` `get_flow_health_status` `list_processors` `list_connections` `get_processor_details` `get_processor_state` `get_processor_types` `get_connection_details` `check_connection_queue` `get_bulletins` `search_flow` |
| services | `get_controller_services` `get_controller_service_details` `find_controller_services_by_type` |
| parameters | `list_parameter_contexts` `get_parameter_context_details` |
| ports | `list_input_ports` `list_output_ports` |
| guides | `analyze_flow_build_request` `get_best_practices_guide` `get_recommended_workflow` `check_configuration` `get_setup_instructions` `get_jdbc_driver_troubleshooting` |

**Write and control (+37, `MCP_MODE=full`)**

| Toolset | Tools |
|---|---|
| flow | `start_new_flow` `create_process_group` `update_process_group_name` `delete_process_group` `create_processor` `update_processor_config` `delete_processor` `create_connection` `delete_connection` · control: `start_processor` `stop_processor` `start_process_group` `stop_process_group` `start_all_processors_in_group` `stop_all_processors_in_group` `terminate_processor` `empty_connection_queue` |
| services | `create_controller_service` `update_controller_service_properties` `delete_controller_service` · control: `enable_controller_service` `disable_controller_service` `enable_all_controller_services_in_group` |
| parameters | `create_parameter_context` `update_parameter_context` `delete_parameter_context` `apply_parameter_context_to_process_group` |
| ports | `create_input_port` `create_output_port` `update_input_port` `update_output_port` `delete_input_port` `delete_output_port` · control: `start_input_port` `stop_input_port` `start_output_port` `stop_output_port` |

`start_process_group`/`stop_process_group` act recursively through NiFi's group scheduling API;
`start_all_processors_in_group`/`stop_all_processors_in_group` act on direct children and
report per processor (started / already running / failed).

### Where the old tool names went

| Was (nifipilot) | Now |
|---|---|
| `get_processors` | `list_processors` |
| `get_connections` | `list_connections` |
| `get_queue_status` | `check_connection_queue` |
| `purge_queue` | `empty_connection_queue` |
| `update_processor` | `update_processor_config` |

Cloudera's tool names are kept as they are. Differences from the Cloudera server: `version`
arguments are optional, list tools return compact summaries, and `start_new_flow` replaces its
`SmartFlowBuilder`.

The agent instructions in [skills/nifi-operator.md](skills/nifi-operator.md) tell a model
how to use these tools safely.

## Architecture

```
MCP client ──stdio/sse/http──> FastMCP ──> tools/*  ──> NiFiClient (httpx) ──HTTPS──> NiFi REST API
                                            │              login, JWT renewal, retries, revisions
                                            └─ ToolContext: toolset/mode gating, dry-run, audit, redaction
```

- `config.py` — settings (pydantic-settings) and toolset parsing
- `client.py` — the single REST client (generic entity CRUD, run-status, revision lookup)
- `tools/context.py` — the decorator every tool goes through
- `tools/{read,flow_write,control,services,parameters,ports,guides}.py` — the tools
- `guides/` — flow-pattern library and best-practice text (adapted from Cloudera's project)

## Development

```bash
uv venv && uv pip install -e . pytest pytest-httpx ruff
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
```

Live check against a running NiFi (builds and removes a throw-away flow; needs `NIFI_URL`,
`NIFI_USERNAME`, `NIFI_PASSWORD` in `.env`): `.venv/bin/python scripts/live_smoke.py`

Tool modules must not use `from __future__ import annotations` (FastMCP builds the schemas from
the real annotations).

## Known limitations

- Validated only against single-node NiFi 2.2.0 and 2.11.0; NiFi 1.x is untested.
- Only username/password (single-user, LDAP) login; no OIDC/client-certificate/Knox login.
- Clustered NiFi: requests go to whichever node the URL points at.
- `update_parameter_context` uses the direct PUT endpoint; NiFi may reject it while components
  that reference the changed parameters are running.
- No rate limiting yet.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Code and ideas adapted from
MIT-licensed mcp-nifipilot are covered by [licenses/MIT-mcp-nifipilot.txt](licenses/MIT-mcp-nifipilot.txt).
