"""Administrative surface: authorisation, validation, audit trail, notifications."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import Database


def test_non_admin_cannot_read_switches(client: TestClient, hangzhou_headers: dict[str, str]) -> None:
    response = client.get("/api/assistant/admin/switches", headers=hangzhou_headers)
    assert response.status_code == 403
    assert response.json()["code"] == "SWITCH_FORBIDDEN"


def test_non_admin_cannot_write_switches(client: TestClient, manager_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "站点管理员越权尝试"},
        headers=manager_headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "SWITCH_FORBIDDEN"


def test_missing_token_is_401(client: TestClient) -> None:
    response = client.get("/api/assistant/status")
    assert response.status_code == 401


def test_admin_can_disable_global(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "平台升级演练"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["enabled"] is False
    assert body["scope"] == "global"
    assert body["reason"] == "平台升级演练"
    assert body["created"] is True

    listed = client.get("/api/assistant/admin/switches", headers=admin_headers).json()
    assert listed["count"] == 1
    assert listed["items"][0]["scope_ref"] == ""


def test_reason_is_required_when_disabling(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": ""},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SWITCH_INVALID"


def test_reason_over_200_chars_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "超" * 201},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_global_scope_ref_must_be_empty(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "site-hangzhou", "enabled": False, "reason": "错配"},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == "SWITCH_INVALID"


def test_site_scope_requires_a_ref(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "site", "scope_ref": "", "enabled": False, "reason": "缺站点"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_unknown_scope_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "tenant", "scope_ref": "t1", "enabled": False, "reason": "x"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_repeat_put_updates_instead_of_duplicating(
    client: TestClient, admin_headers: dict[str, str], db: Database
) -> None:
    first = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "site", "scope_ref": "site-hangzhou", "enabled": False, "reason": "维护"},
        headers=admin_headers,
    )
    second = client.put(
        "/api/assistant/admin/switches",
        json={"scope": "site", "scope_ref": "site-hangzhou", "enabled": True, "reason": "维护结束"},
        headers=admin_headers,
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]


def test_change_writes_audit_entry(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "global", "scope_ref": "", "enabled": False, "reason": "演练"},
        headers=admin_headers,
    )
    audit = client.get("/api/assistant/admin/audit", headers=admin_headers).json()
    actions = [item["action"] for item in audit["items"]]
    assert "assistant.switch.seed" in actions
    assert audit["items"][-1]["actor"] == "u-1001"
    assert audit["items"][-1]["target"] == "global"


def test_change_notifies_the_admin_group(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "role", "scope_ref": "operation", "enabled": False, "reason": "外包团队交接"},
        headers=admin_headers,
    )
    messages = client.get("/api/assistant/admin/notifications", headers=admin_headers).json()
    assert messages["count"] == 1
    assert messages["items"][0]["group"] == "group:platform-admins"
    assert "role:operation" in messages["items"][0]["title"]


def test_cache_keys_report_generation(client: TestClient, admin_headers: dict[str, str]) -> None:
    body = client.get("/api/assistant/admin/cache-keys", headers=admin_headers).json()
    assert body["backend"] == "memory"
    assert body["ttl_seconds"] == 5.0
    assert body["generation"] >= 1
