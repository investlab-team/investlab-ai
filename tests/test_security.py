from fastapi.testclient import TestClient

from investlab_ai.config import Settings
from investlab_ai.main import create_app

TOKEN = "test-only-internal-service-token-123456"


def client():
    return TestClient(create_app(Settings(service_token=TOKEN)))


def test_health_is_public():
    assert client().get("/health").status_code == 200


def test_internal_api_requires_service_auth():
    assert client().post("/internal/v1/questions", json={"question": "매출은?"}).status_code == 401


def test_authenticated_request_does_not_fake_analysis():
    response = client().post(
        "/internal/v1/questions", json={"question": "매출은?"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "AI_NOT_CONFIGURED"


def test_invalid_input_rejected():
    response = client().post(
        "/internal/v1/questions", json={"question": ""},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 422
