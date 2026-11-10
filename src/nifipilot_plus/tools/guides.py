"""Guidance tools: flow-pattern analysis, best practices, setup help. No NiFi calls."""

from ..guides.best_practices import NiFiBestPractices
from ..guides.flow_builder import analyze_flow_request
from ..guides.jdbc import get_jdbc_driver_troubleshooting as _jdbc_text
from ..guides.setup import setup_instructions, validate_config
from .context import ToolContext


def register(ctx: ToolContext) -> None:
    guides = ctx.tool("guides")

    @guides
    def analyze_flow_build_request(user_request: str) -> dict:
        """Recognise common integration patterns (database to object storage, Kafka to S3,
        REST to database, ...) and list the requirements to collect BEFORE building a flow."""
        return analyze_flow_request(user_request)

    @guides
    def get_best_practices_guide() -> str:
        """NiFi flow-building best practices (groups, naming, lifecycle, parameters, safety)."""
        return NiFiBestPractices.get_best_practices_guide()

    @guides
    def get_recommended_workflow(user_request: str) -> str:
        """Step-by-step workflow (with the tools to call) for building the requested flow."""
        return NiFiBestPractices.get_recommended_workflow_for_request(user_request)

    @guides
    def check_configuration() -> dict:
        """Validate this MCP server's own configuration and report errors/warnings."""
        is_valid, errors, warnings = validate_config(ctx.settings)
        return {
            "is_valid": is_valid,
            "errors": errors,
            "warnings": warnings,
            "mode": ctx.settings.mcp_mode,
            "toolsets": sorted(ctx.settings.toolsets),
            "nifi_api_base": ctx.settings.api_base,
        }

    @guides
    def get_setup_instructions() -> str:
        """Environment variables and a typical build sequence for this MCP server."""
        return setup_instructions()

    @guides
    def get_jdbc_driver_troubleshooting() -> str:
        """Explain 'driver class not found' errors for DBCPConnectionPool and what to do."""
        return _jdbc_text()
