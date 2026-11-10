"""Thin NiFi REST client (httpx) shared by every tool.

Design notes
- One client, one auth path: NiFi single-user / LDAP style login via ``POST /access/token``
  (JWT, re-acquired automatically on a 401). An empty password means "no authentication"
  (unsecured NiFi 1.x over HTTP).
- NiFi uses optimistic locking: every update/delete needs the component's current revision.
  Callers may pass ``version=None`` and the client fetches the current revision itself.
- Retries only on transient problems (connection errors, 429/502/503/504). POST is retried
  only when the request never reached NiFi (connect errors), so retries never duplicate
  a create.
- Works with NiFi 1.x and 2.x: it only uses endpoints common to both. The detected
  version is exposed through :meth:`NiFiClient.version_info`.

Parts of the request/response shapes follow the cloudera/NiFi-MCP-Server client
(Apache-2.0); see NOTICE.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from .config import Settings

# kind -> REST collection path
COLLECTIONS: dict[str, str] = {
    "processor": "processors",
    "connection": "connections",
    "process_group": "process-groups",
    "input_port": "input-ports",
    "output_port": "output-ports",
    "funnel": "funnels",
    "controller_service": "controller-services",
    "parameter_context": "parameter-contexts",
}

# NiFi "type" strings used in connection endpoints -> kind
COMPONENT_TYPE_KIND: dict[str, str] = {
    "PROCESSOR": "processor",
    "INPUT_PORT": "input_port",
    "OUTPUT_PORT": "output_port",
    "FUNNEL": "funnel",
}

_HINTS = {
    400: "NiFi rejected the request as invalid (check ids, property names and values).",
    401: "Authentication failed (check NIFI_USERNAME / NIFI_PASSWORD).",
    403: "The NiFi user is not authorized for this operation.",
    404: "Component not found (check the id; use search_flow or list_* tools).",
    409: (
        "Conflict: stale revision, or the component is in the wrong state "
        "(running, queue not empty, still referenced...)."
    ),
}

_TRANSIENT_STATUS = {429, 502, 503, 504}
_IDEMPOTENT = {"GET", "PUT", "DELETE", "HEAD"}


class NiFiError(Exception):
    def __init__(
        self, message: str, status_code: int | None = None, response_body: str | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

    def __str__(self) -> str:
        text = super().__str__()
        if self.status_code:
            text = f"[{self.status_code}] {text}"
            hint = _HINTS.get(self.status_code)
            if hint:
                text += f" — {hint}"
        if self.response_body:
            text += f"\nNiFi response: {self.response_body[:2000]}"
        return text


class NiFiClient:
    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._sleep = sleep
        headers = {"Accept": "application/json"}
        if settings.nifi_proxy_context_path:
            headers["X-ProxyContextPath"] = settings.nifi_proxy_context_path
        self._http = httpx.Client(
            base_url=settings.api_base + "/",
            verify=settings.verify,
            timeout=settings.nifi_timeout,
            headers=headers,
        )
        self._token: str | None = None
        self._lock = threading.Lock()
        self._root_id: str | None = None
        self._version: dict[str, Any] | None = None

    # ------------------------------------------------------------------ auth
    @property
    def _can_login(self) -> bool:
        return bool(self.settings.nifi_username and self.settings.nifi_password)

    def _login(self) -> None:
        # /access/token answers text/plain (the raw JWT); the client-wide
        # "Accept: application/json" default makes NiFi reply 406.
        resp = self._http.post(
            "access/token",
            data={
                "username": self.settings.nifi_username,
                "password": self.settings.nifi_password,
            },
            headers={"Accept": "text/plain"},
        )
        if resp.status_code not in (200, 201):
            raise NiFiError("NiFi login failed", resp.status_code, resp.text)
        self._token = resp.text.strip()

    def _ensure_token(self) -> None:
        if self._token is None and self._can_login:
            with self._lock:
                if self._token is None:
                    self._login()

    # --------------------------------------------------------------- transport
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        path = path.lstrip("/")
        max_attempts = max(1, self.settings.nifi_max_retries)
        attempt = 0
        relogged = False
        while True:
            self._ensure_token()
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
            try:
                resp = self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:
                retryable = method in _IDEMPOTENT or isinstance(
                    exc, (httpx.ConnectError, httpx.ConnectTimeout)
                )
                attempt += 1
                if retryable and attempt < max_attempts:
                    self._backoff(attempt)
                    continue
                raise NiFiError(f"{method} {path} failed: {exc!r}") from exc

            if resp.status_code == 401 and self._can_login and not relogged:
                relogged = True
                with self._lock:
                    self._token = None
                continue

            if resp.status_code in _TRANSIENT_STATUS:
                attempt += 1
                if attempt < max_attempts and (method in _IDEMPOTENT or resp.status_code == 503):
                    self._backoff(attempt)
                    continue

            if not resp.is_success:
                raise NiFiError(
                    f"{method} {path} failed: {resp.reason_phrase}",
                    resp.status_code,
                    resp.text or "(empty response)",
                )
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError:
                return {"raw": resp.text}

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(self.settings.nifi_retry_backoff * (2 ** (attempt - 1)), 5.0))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json, params=params)

    def put(self, path: str, json: Any = None) -> Any:
        return self._request("PUT", path, json=json)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, params=params)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ version/root
    def version_info(self) -> dict[str, Any]:
        """NiFi version as reported by /flow/about, plus a parsed major number."""
        if self._version is None:
            about = self.get("flow/about").get("about", {})
            raw = str(about.get("version", ""))
            major: int | None
            try:
                major = int(raw.split(".")[0])
            except ValueError:
                major = None
            self._version = {
                "version": raw or None,
                "major": major,
                "is_nifi_2x": None if major is None else major >= 2,
                "title": about.get("title"),
                "build_tag": about.get("buildTag"),
                "build_timestamp": about.get("buildTimestamp"),
            }
        return self._version

    def root_group_id(self) -> str:
        if self._root_id is None:
            self._root_id = self.get("flow/process-groups/root")["processGroupFlow"]["id"]
        return self._root_id

    def resolve_group_id(self, group_id: str | None) -> str:
        """Map the 'root' alias (or empty) to the real root group id."""
        if not group_id or group_id == "root":
            return self.root_group_id()
        return group_id

    # ------------------------------------------------------------ generic CRUD
    def entity(self, kind: str, entity_id: str) -> dict[str, Any]:
        return self.get(f"{COLLECTIONS[kind]}/{entity_id}")

    def revision(self, kind: str, entity_id: str, version: int | None = None) -> int:
        """Return `version` if given, else the component's current revision."""
        if version is not None:
            return int(version)
        return int(self.entity(kind, entity_id)["revision"]["version"])

    def create_in_group(
        self, kind: str, group_id: str, component: dict[str, Any]
    ) -> dict[str, Any]:
        group_id = self.resolve_group_id(group_id)
        return self.post(
            f"process-groups/{group_id}/{COLLECTIONS[kind]}",
            json={"revision": {"version": 0}, "component": component},
        )

    def update(
        self, kind: str, entity_id: str, fields: dict[str, Any], version: int | None = None
    ) -> dict[str, Any]:
        rev = self.revision(kind, entity_id, version)
        return self.put(
            f"{COLLECTIONS[kind]}/{entity_id}",
            # Parameter contexts reject the update unless the entity itself carries the id.
            json={
                "id": entity_id,
                "revision": {"version": rev},
                "component": {"id": entity_id, **fields},
            },
        )

    def delete_entity(
        self, kind: str, entity_id: str, version: int | None = None
    ) -> dict[str, Any]:
        rev = self.revision(kind, entity_id, version)
        return self.delete(
            f"{COLLECTIONS[kind]}/{entity_id}",
            params={"version": rev, "disconnectedNodeAcknowledged": "false"},
        )

    def set_run_status(
        self, kind: str, entity_id: str, state: str, version: int | None = None
    ) -> dict[str, Any]:
        rev = self.revision(kind, entity_id, version)
        return self.put(
            f"{COLLECTIONS[kind]}/{entity_id}/run-status",
            json={
                "revision": {"version": rev},
                "state": state,
                "disconnectedNodeAcknowledged": False,
            },
        )

    def schedule_group(self, group_id: str, state: str) -> dict[str, Any]:
        """Start/stop every schedulable component in a group (recursive)."""
        group_id = self.resolve_group_id(group_id)
        return self.put(
            f"flow/process-groups/{group_id}",
            json={"id": group_id, "state": state, "disconnectedNodeAcknowledged": False},
        )

    def parent_group_of(self, component_type: str, component_id: str) -> str:
        """Group that owns a connectable component (needed to build connections)."""
        kind = COMPONENT_TYPE_KIND[component_type.upper()]
        return self.entity(kind, component_id)["component"]["parentGroupId"]
