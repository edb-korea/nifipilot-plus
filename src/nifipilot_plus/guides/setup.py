"""Configuration validation and setup instructions (no NiFi connection needed)."""

from __future__ import annotations

from ..config import Settings


def validate_config(settings: Settings) -> tuple[bool, list[str], list[str]]:
    """Return (is_valid, errors, warnings) for the active settings."""
    errors: list[str] = []
    warnings: list[str] = []

    if not settings.nifi_url.startswith(("http://", "https://")):
        errors.append("NIFI_URL must start with http:// or https://")
    if settings.nifi_url.startswith("http://"):
        warnings.append("NIFI_URL uses plain HTTP; NiFi 2.x only serves HTTPS.")
    if not settings.nifi_password:
        warnings.append(
            "NIFI_PASSWORD is empty: requests are sent unauthenticated (only works on an "
            "unsecured NiFi 1.x). NiFi single-user passwords need at least 12 characters."
        )
    if not settings.nifi_verify_ssl and not settings.nifi_ca_bundle:
        warnings.append(
            "TLS verification is disabled (NIFI_VERIFY_SSL=false). Acceptable for a local "
            "self-signed NiFi, not for production; prefer NIFI_CA_BUNDLE."
        )
    if settings.mcp_mode == "full":
        warnings.append("MCP_MODE=full: write and control tools are enabled.")
    if settings.mcp_transport != "stdio" and settings.mcp_host in ("0.0.0.0", "::"):
        warnings.append(
            f"MCP_TRANSPORT={settings.mcp_transport} is listening on {settings.mcp_host}; the "
            "MCP endpoint itself has no authentication, so restrict network access."
        )
    return not errors, errors, warnings


def setup_instructions() -> str:
    return """\
nifipilot-plus — setup guide
============================

Required
  NIFI_URL                https://<host>:8443          (the /nifi-api suffix is added for you)
  NIFI_USERNAME           NiFi user (single-user or LDAP)
  NIFI_PASSWORD           password (NiFi single-user passwords need >= 12 characters)

TLS
  NIFI_VERIFY_SSL         true (default) | false for a local self-signed certificate
  NIFI_CA_BUNDLE          path to a CA bundle (preferred over disabling verification)

NiFi behind a reverse proxy
  NIFI_PROXY_CONTEXT_PATH sent as X-ProxyContextPath
  Note: NiFi also validates the Host header. Add the host:port you connect with to
  nifi.web.proxy.host (container env NIFI_WEB_PROXY_HOST).

Behaviour
  MCP_MODE                readonly (default) | full  — full enables write/control tools
  MCP_TOOLSETS            all (default) or a list from: core,flow,services,parameters,ports,guides
  MCP_DRY_RUN             true: write/control tools only report what they would do
  MCP_AUDIT_LOG           true (default); MCP_AUDIT_LOG_PATH=./logs/audit.log

Transport
  MCP_TRANSPORT           stdio (default) | sse | http
  MCP_HOST / MCP_PORT     for sse/http (default 127.0.0.1:8000)

Typical sequence for a new flow (MCP_MODE=full)
  start_new_flow -> create_controller_service -> create_processor -> update_processor_config
  -> create_connection -> enable_all_controller_services_in_group
  -> start_all_processors_in_group -> get_flow_health_status
"""
