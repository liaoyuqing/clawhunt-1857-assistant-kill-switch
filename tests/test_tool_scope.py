"""Tool-level scope: the blocked tool leaves the list and answers 409."""

from __future__ import annotations

from fastapi.testclient import TestClient

TOOLS = {"knowledge_search", "sop_draft", "member_profile_summary", "policy_qa"}


def test_blocked_tool_disappears_from_the_list(
    client: TestClient, admin_headers: dict[str, str], hangzhou_headers: dict[str, str]
) -> None:
    before = client.get("/api/assistant/tools", headers=hangzhou_headers).json()
    assert set(before["tools"]) == TOOLS
    assert before["blocked_tools"] == []

    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "tool", "scope_ref": "sop_draft", "enabled": False, "reason": "SOP 模板改版中"},
        headers=admin_headers,
    )

    after = client.get("/api/assistant/tools", headers=hangzhou_headers).json()
    assert "sop_draft" not in after["tools"]
    assert after["blocked_tools"] == ["sop_draft"]
    assert after["enabled"] is True
    assert set(after["tools"]) == TOOLS - {"sop_draft"}


def test_invoking_the_blocked_tool_answers_409(
    client: TestClient, admin_headers: dict[str, str], hangzhou_headers: dict[str, str]
) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "tool", "scope_ref": "sop_draft", "enabled": False, "reason": "SOP 模板改版中"},
        headers=admin_headers,
    )
    response = client.post(
        "/api/assistant/tools/sop_draft/invoke",
        json={"arguments": {"template": "member-onboarding"}},
        headers=hangzhou_headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TOOL_UNAVAILABLE"
    assert response.json()["tool"] == "sop_draft"


def test_other_tools_still_work(
    client: TestClient, admin_headers: dict[str, str], hangzhou_headers: dict[str, str]
) -> None:
    client.put(
        "/api/assistant/admin/switches",
        json={"scope": "tool", "scope_ref": "sop_draft", "enabled": False, "reason": "SOP 模板改版中"},
        headers=admin_headers,
    )
    response = client.post(
        "/api/assistant/tools/knowledge_search/invoke",
        json={"arguments": {"query": "会员服务标准"}},
        headers=hangzhou_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_tool_is_404(client: TestClient, hangzhou_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/assistant/tools/nope/invoke", json={"arguments": {}}, headers=hangzhou_headers
    )
    assert response.status_code == 404
