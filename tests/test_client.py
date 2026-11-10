import httpx
import pytest

from nifipilot_plus.client import NiFiClient, NiFiError
from nifipilot_plus.config import Settings

from .conftest import BASE, make_settings, request_bodies


def test_api_base_is_normalised():
    assert (
        Settings(_env_file=None, nifi_url="https://h:8443/").api_base == "https://h:8443/nifi-api"
    )
    assert Settings(_env_file=None, nifi_url="https://h/nifi-api").api_base == "https://h/nifi-api"


def test_logs_in_once_and_sends_bearer_token(client, login):
    login.add_response(url=f"{BASE}/flow/about", json={"about": {"version": "2.2.0"}})
    login.add_response(
        url=f"{BASE}/flow/process-groups/root", json={"processGroupFlow": {"id": "r"}}
    )
    client.version_info()
    client.root_group_id()
    requests = login.get_requests()
    assert [r.url.path for r in requests] == [
        "/nifi-api/access/token",
        "/nifi-api/flow/about",
        "/nifi-api/flow/process-groups/root",
    ]
    assert requests[1].headers["Authorization"] == "Bearer jwt-token"
    assert b"username=admin" in requests[0].content


def test_relogin_after_401(client, httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/access/token", text="jwt-1")
    httpx_mock.add_response(url=f"{BASE}/flow/about", status_code=401)
    httpx_mock.add_response(method="POST", url=f"{BASE}/access/token", text="jwt-2")
    httpx_mock.add_response(url=f"{BASE}/flow/about", json={"about": {"version": "2.2.0"}})
    assert client.version_info()["version"] == "2.2.0"
    assert httpx_mock.get_requests()[-1].headers["Authorization"] == "Bearer jwt-2"


def test_failed_login_raises(client, httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{BASE}/access/token", status_code=401, text="bad")
    with pytest.raises(NiFiError, match="login failed"):
        client.get("flow/about")


def test_no_password_means_no_auth(httpx_mock):
    c = NiFiClient(make_settings(nifi_password=""), sleep=lambda _s: None)
    httpx_mock.add_response(url=f"{BASE}/flow/about", json={"about": {"version": "1.28.1"}})
    info = c.version_info()
    assert info["major"] == 1 and info["is_nifi_2x"] is False
    assert "Authorization" not in httpx_mock.get_requests()[0].headers


def test_get_retries_transient_503(client, login):
    login.add_response(url=f"{BASE}/flow/about", status_code=503)
    login.add_response(url=f"{BASE}/flow/about", json={"about": {"version": "2.2.0"}})
    assert client.version_info()["is_nifi_2x"] is True


def test_post_is_not_retried_after_a_server_error(client, login):
    login.add_response(method="POST", url=f"{BASE}/process-groups/g/processors", status_code=500)
    with pytest.raises(NiFiError) as exc:
        client.create_in_group("processor", "g", {"name": "x"})
    assert exc.value.status_code == 500
    posts = [r for r in login.get_requests() if r.url.path.endswith("/processors")]
    assert len(posts) == 1


def test_post_retried_when_connection_fails(client, login):
    login.add_exception(httpx.ConnectError("boom"))
    login.add_response(method="POST", url=f"{BASE}/process-groups/g/processors", json={"id": "p"})
    assert client.create_in_group("processor", "g", {"name": "x"})["id"] == "p"


def test_error_message_has_hint_and_body(client, login):
    login.add_response(url=f"{BASE}/processors/p1", status_code=409, text="processor is running")
    with pytest.raises(NiFiError) as exc:
        client.entity("processor", "p1")
    message = str(exc.value)
    assert "[409]" in message and "wrong state" in message and "processor is running" in message


def test_update_fetches_current_revision_when_version_missing(client, login):
    login.add_response(url=f"{BASE}/processors/p1", json={"revision": {"version": 7}})
    login.add_response(method="PUT", url=f"{BASE}/processors/p1", json={"id": "p1"})
    client.update("processor", "p1", {"name": "renamed"})
    (body,) = request_bodies(login, "PUT", "/processors/p1")
    assert body == {
        "id": "p1",
        "revision": {"version": 7},
        "component": {"id": "p1", "name": "renamed"},
    }


def test_explicit_version_skips_the_lookup(client, login):
    login.add_response(method="PUT", url=f"{BASE}/processors/p1/run-status", json={"id": "p1"})
    client.set_run_status("processor", "p1", "RUNNING", version=3)
    (body,) = request_bodies(login, "PUT", "/run-status")
    assert body["revision"] == {"version": 3} and body["state"] == "RUNNING"


def test_root_alias_is_resolved_once(client, login):
    login.add_response(
        url=f"{BASE}/flow/process-groups/root", json={"processGroupFlow": {"id": "real"}}
    )
    assert client.resolve_group_id("root") == "real"
    assert client.resolve_group_id("root") == "real"
    assert client.resolve_group_id("abc") == "abc"


def test_login_asks_for_text_plain(client, login):
    login.add_response(url=f"{BASE}/flow/about", json={"about": {"version": "2.2.0"}})
    client.version_info()
    token_request, about_request = login.get_requests()
    assert token_request.headers["Accept"] == "text/plain"  # NiFi answers 406 to application/json
    assert about_request.headers["Accept"] == "application/json"
