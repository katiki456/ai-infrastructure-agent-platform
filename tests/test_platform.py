from fastapi.testclient import TestClient

from main import app
from platform_app.agent import kubernetes_agent, triage_agent

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics_endpoint() -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "ai_chat_requests_total" in response.text


def test_agent_exposes_cluster_health_tool() -> None:
    assert any(tool.name == "get_cluster_health" for tool in kubernetes_agent.tools)


def test_triage_agent_exposes_specialist_handoffs() -> None:
    assert triage_agent.name == "TriageAgent"
    assert {handoff.agent_name for handoff in triage_agent.handoffs} == {
        "KubernetesAgent",
        "GPUAgent",
        "IncidentAgent",
    }
