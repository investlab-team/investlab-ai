import json

import httpx
import pytest
from openai import OpenAI

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.providers.openai_provider import OpenAIProvider
from investlab_ai.schemas import Evidence


def provider(handler):
    instance = OpenAIProvider(Settings(
        service_token="test-only-internal-service-token-123456",
        openai_api_key="unit-test-key", openai_model="test-model",
    ))
    instance.client.close()
    instance.client = OpenAI(
        api_key="unit-test-key", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return instance


def test_sdk_structured_response_and_no_automatic_retries():
    def handler(request):
        data = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert data["store"] is False
        assert data["text"]["format"]["type"] == "json_schema"
        schema = data["text"]["format"]["schema"]
        allowed = schema["$defs"]["GroundedClaim"]["properties"]["evidenceIds"]["items"]
        assert allowed.get("enum", [allowed.get("const")]) == ["source-1"]
        assert "신뢰할 수 없는 데이터" in data["instructions"]
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1,
            "model": "test-model", "status": "completed", "output": [{
                "id": "msg_test", "type": "message", "role": "assistant",
                "status": "completed", "content": [{
                    "type": "output_text", "annotations": [], "text": json.dumps({
                        "status": "answered", "claims": [{
                            "category": "performance", "text": "매출액은 100억원입니다.",
                            "evidenceIds": ["source-1"],
                        }],
                    }, ensure_ascii=False),
                }],
            }],
        })
    client = provider(handler)
    result = client.generate("매출은?", [Evidence(
        chunk_id="source-1", section="실적", text="매출액 100억원",
        source_url="https://dart.fss.or.kr/",
    )])
    assert result.claims[0].evidence_ids == ["source-1"]
    client.close()


def test_embedding_order_reconstructed_from_indices():
    def handler(request):
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={
            "object": "list", "model": "test", "data": [
                {"object": "embedding", "index": 1, "embedding": [0, 1]},
                {"object": "embedding", "index": 0, "embedding": [1, 0]},
            ], "usage": {"prompt_tokens": 2, "total_tokens": 2},
        })
    client = provider(handler)
    assert client.embed(["first", "second"]) == [[1, 0], [0, 1]]
    client.close()


def test_openai_rate_limit_is_sanitized_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "secret-provider-detail"}})
    client = provider(handler)
    with pytest.raises(ServiceError, match="OPENAI_RATE_LIMITED"):
        client.embed(["test"])
    assert len(calls) == 1
    client.close()


def test_incomplete_model_output_is_an_error():
    client = provider(lambda _: httpx.Response(200, json={
        "id": "resp_test", "object": "response", "created_at": 1, "model": "test-model",
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "output": [],
    }))
    with pytest.raises(ServiceError, match="LLM_INCOMPLETE_OR_REFUSED"):
        client.generate("test", [])
    client.close()


def test_credit_balance_error_is_distinct_from_rate_limit():
    client = provider(lambda _: httpx.Response(429, json={"error": {
        "type": "insufficient_quota", "code": "credit_balance_exhausted",
        "message": "private-provider-message",
    }}))
    with pytest.raises(ServiceError, match="OPENAI_QUOTA_EXHAUSTED"):
        client.generate("test", [])
    client.close()


def test_project_limit_does_not_claim_credit_balance_is_empty():
    client = provider(lambda _: httpx.Response(429, json={"error": {
        "type": "insufficient_quota", "code": "project_spend_limit_exceeded", "message": "limit",
    }}))
    with pytest.raises(ServiceError, match="OPENAI_SPEND_LIMIT_REACHED"):
        client.generate("test", [])
    client.close()
