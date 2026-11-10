# Operating Apache NiFi through nifipilot-plus

Instructions for an AI agent that has the nifipilot-plus MCP tools. Put them in `CLAUDE.md`,
a system prompt or a Cursor rule. They describe how to work safely with the tools and what
NiFi does when you ask it for something it cannot do. Written for this server's tool set.

## 1. Ground rules

- **Look first, then change.** Before you modify anything, read the component you are about to
  touch. After you modify it, read it again and say what you saw.
- **Know your mode.** If the write and control tools are not in your tool list, the server runs
  read-only. Say so, stay read-only, and do not look for a way around it.
- **Destructive means ask.** Deleting anything, emptying a queue and `terminate_processor`
  need the user's explicit yes for that specific object. A general "go ahead and build it" does
  not cover deleting something that already existed.
- **Small steps.** Make a change, check it, then make the next one. Never queue up a long list
  of writes and look at the result only at the end.
- **Stop on errors.** When a call fails, report the full message and what you were trying to
  do. Do not repeat the same call hoping it works, and do not try a different destructive
  route to get around the failure.
- **Production needs more care.** If the user calls the instance "prod" or "live", describe
  the change and get a yes before every write. If `MCP_DRY_RUN` is on, point out that the
  results are only previews.
- **Never print secrets.** Sensitive values come back masked. Do not try to recover them, and
  do not paste passwords the user gave you into flow properties without being asked.

## 2. What you have

| Area | Read tools | Write/control tools (full mode only) |
|---|---|---|
| Overview | `get_nifi_version`, `get_system_diagnostics`, `get_root_process_group`, `get_process_groups`, `get_flow_status`, `get_flow_summary`, `get_flow_health_status`, `get_bulletins`, `search_flow` | |
| Flow | `list_processors`, `list_connections`, `get_processor_details`, `get_processor_state`, `get_processor_types`, `get_connection_details`, `check_connection_queue` | create/delete processors, connections and groups; `update_processor_config`; start/stop processors and groups; `empty_connection_queue`; `terminate_processor`; `start_new_flow` |
| Controller services | `get_controller_services`, `get_controller_service_details`, `find_controller_services_by_type` | create/update/delete, enable/disable (single or whole group) |
| Parameters | `list_parameter_contexts`, `get_parameter_context_details` | create/update/delete, apply to a process group |
| Ports | `list_input_ports`, `list_output_ports` | create/update/delete, start/stop |
| Guidance | `analyze_flow_build_request`, `get_best_practices_guide`, `get_recommended_workflow`, `check_configuration`, `get_setup_instructions`, `get_jdbc_driver_troubleshooting` | |

Most list tools return compact summaries. Use a `get_*_details` tool when you need the full
configuration of one component. Long lists are cut off after 200 entries, so narrow the query
(group id, `search_flow`) instead of assuming you saw everything.

Write tools take an optional `version` (NiFi's revision number). Leave it out: the server reads
the current revision itself.

## 3. Procedures

### Is anything wrong?

1. `get_nifi_version` and `get_system_diagnostics`: is NiFi up, how is memory?
2. `get_flow_health_status` for the root group and for the groups the user cares about:
   invalid processors, stopped processors, queues near back-pressure.
3. `get_bulletins` for recent errors.
4. For a suspicious queue: `list_connections`, then `check_connection_queue` for the one that
   looks full. Report what you found and let the user pick the action.

### Understand an existing flow

`get_process_groups` for the layout, then `list_processors` and `list_connections` per group.
Use `search_flow` to find a component by name or property value. Summarise in the order the
data flows, not in the order the API returned things.

### Build a new flow (full mode)

1. Call `analyze_flow_build_request` with the user's wording and ask the user for whatever it
   says is missing (hosts, credentials, formats, schedules). Do not invent connection details.
2. `start_new_flow` creates a dedicated group. Build inside it, not on the root canvas.
3. Controller services first when a processor needs one: look for an existing one with
   `find_controller_services_by_type`, otherwise create it, set its properties, and enable it.
4. Create the processors one at a time. Spread them out (for example 200 px apart in `x`) so the
   canvas is readable. Check valid property names with `get_processor_types` and
   `get_processor_details` before setting them.
5. Configure with `update_processor_config`. The argument has a nested shape, for example
   `{"config": {"schedulingPeriod": "2 sec", "autoTerminatedRelationships": ["success"]}}`.
6. Connect the processors with `create_connection`. Every relationship of every processor must
   either be connected or auto-terminated, otherwise the processor stays invalid. Decide on
   purpose what happens to `failure`.
7. Enable the services, start the processors, wait a moment, and run
   `get_flow_health_status`. Report the group id, what runs, and what is still invalid.

### Change a flow that is running

Stop the component, change it, start it, check it: `stop_processor`,
`update_processor_config`, `start_processor`, `get_processor_state`. For a change that touches
several components, stop the whole group first (`stop_process_group`) and start it again after.

### Remove things

Ask the user to confirm the exact objects. NiFi only deletes a component that is stopped and
has nothing waiting: stop it, empty the queues on its connections (with confirmation), delete
the connections, then the processors, then the group. Controller services must be disabled
before they can be deleted.

### Stuck processor

`stop_processor` first. If it still shows active threads after a while, tell the user and offer
`terminate_processor` as a last resort. It kills the running threads and may lose in-flight
work.

## 4. NiFi behaviour that explains most errors

| You see | Usual reason |
|---|---|
| 409 on an update or delete | The component is running, a queue is not empty, something still references it, or the revision is stale. Re-read it and check its state before anything else. |
| 409 starting a port | A port with no incoming connection cannot start. Connect it first. |
| 400 | A property name or value NiFi does not accept. Compare with `get_processor_details`. |
| 403 | The NiFi user lacks the permission for that component. |
| Processor stays "invalid" | A required property is missing, a relationship is neither connected nor terminated, or a controller service is not enabled. |
| "Driver class not found" on a database service | See `get_jdbc_driver_troubleshooting`. |

`start_process_group` and `stop_process_group` work recursively through child groups.
`start_all_processors_in_group` and `stop_all_processors_in_group` handle only the direct
children and report each processor separately.

## 5. Reporting back

Keep it short and concrete: what you changed (tool and target), what the state is now (read
back, not assumed), what is still open, and the ids the user will need to find it in the NiFi
UI. If something failed or was skipped, say so first.
