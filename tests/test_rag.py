from datetime import date

import pytest
from fastapi.testclient import TestClient

from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.main import create_app
from investlab_ai.schemas import Claim, Company, Disclosure, Draft
from investlab_ai.storage import Repository

RECEIPT = "20260316000001"
TOKEN = "test-only-internal-service-token-123456"


class FakeDart:
    def __init__(self, payload):
        self.payload = payload

    def lookup(self, receipt):
        return Disclosure(
            rcept_no=receipt, corp_code="00126380", corp_name="예시기업",
            report_name="사업보고서", filed_on=date(2026, 3, 16),
            source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        )

    def document(self, receipt):
        return self.payload


class FakeProvider:
    def __init__(self):
        self.embedded = []
        self.generated = []
        self.mode = "valid"

    def embed(self, texts):
        self.embedded.extend(texts)
        return [[1.0, 0.2] for _ in texts]

    def generate(self, question, evidence):
        self.generated.append(evidence)
        if self.mode == "insufficient":
            return Draft(status="insufficient_evidence", claims=[])
        identity = "invented-citation" if self.mode == "invalid" else evidence[0].chunk_id
        return Draft(status="answered", claims=[Claim(
            category="performance", text="공시에 매출액이 제시되어 있습니다.",
            evidence_ids=[identity],
        )])


@pytest.fixture
def service(tmp_path, archive):
    config = Settings(
        service_token=TOKEN, local_sqlite_path=tmp_path / "research.db",
        openai_model="test-model", chunk_tokens=100, analysis_batch_tokens=2000,
    )
    repo = Repository(config)
    repo.initialize_local()
    provider = FakeProvider()
    raw = archive("<h1>사업의 내용</h1><p>매출액은 100억원입니다.</p>"
                  "<h1>위험요인</h1><p>해외 수요 감소가 위험요인입니다.</p>")
    research = ResearchService(config, repo, FakeDart(raw), provider)
    yield research
    repo.close()


def test_prepare_is_persistent_and_reuses_embeddings(service):
    first = service.prepare(RECEIPT)
    embedded = len(service.provider.embedded)
    again = service.prepare(RECEIPT)
    assert first.document_id == again.document_id
    assert len(service.provider.embedded) == embedded
    second_connection = Repository(service.config)
    assert second_connection.document(RECEIPT).content_hash == first.content_hash
    second_connection.close()


def test_api_end_to_end_and_camel_case_contract(service):
    with TestClient(create_app(service.config, service)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        prepared = client.post(f"/internal/v1/disclosures/{RECEIPT}/prepare", headers=headers)
        assert prepared.status_code == 200
        assert prepared.json()["chunkCount"] == 2
        response = client.post("/internal/v1/questions", headers=headers, json={
            "rceptNo": RECEIPT, "question": "매출액은?",
        })
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "answered"
        ids = {source["chunkId"] for source in result["sources"]}
        assert set(result["claims"][0]["evidenceIds"]).issubset(ids)
        assert all("rcpNo=" + RECEIPT in source["sourceUrl"] for source in result["sources"])


def test_retrieval_never_crosses_selected_report(service):
    service.prepare(RECEIPT)
    service.prepare("20260316000002")
    selected = {row[0].chunk_id for row in service.repo.chunks(RECEIPT)}
    service.ask(RECEIPT, "매출")
    assert {item.chunk_id for item in service.provider.generated[-1]}.issubset(selected)


def test_fabricated_citation_rejected(service):
    service.prepare(RECEIPT)
    service.provider.mode = "invalid"
    with pytest.raises(ServiceError, match="LLM_INVALID_CITATIONS"):
        service.ask(RECEIPT, "매출")


def test_insufficient_evidence_is_not_provider_failure(service):
    service.prepare(RECEIPT)
    service.provider.mode = "insufficient"
    result = service.ask(RECEIPT, "내년 주가는?")
    assert result.status == "insufficient_evidence"
    assert result.sources == []
    assert "확인할 수 없습니다" in result.answer


def test_unprepared_report_is_not_silently_downloaded_or_billed(service):
    with pytest.raises(ServiceError, match="DOCUMENT_NOT_PREPARED"):
        service.ask(RECEIPT, "매출")
    assert not service.provider.generated and not service.provider.embedded


def test_embedding_model_change_requires_new_index(service):
    service.prepare(RECEIPT)
    service.config.embedding_model = "different-model"
    with pytest.raises(ServiceError, match="EMBEDDING_MODEL_MISMATCH"):
        service.ask(RECEIPT, "매출")


def test_full_analysis_reads_all_chunks_and_budget_precedes_calls(service):
    service.prepare(RECEIPT)
    service.config.analysis_batch_tokens = 100
    plan = service.analysis_plan(RECEIPT)
    assert plan.model_calls == 2
    with pytest.raises(ServiceError, match="ANALYSIS_CALL_BUDGET_EXCEEDED"):
        service.analyze(RECEIPT, max_calls=1)
    assert not service.provider.generated
    result = service.analyze(RECEIPT, max_calls=2)
    seen = {item.chunk_id for group in service.provider.generated for item in group}
    assert seen == {row[0].chunk_id for row in service.repo.chunks(RECEIPT)}
    assert result.model_calls == 2


def test_failed_embedding_does_not_leave_partial_document(service):
    service.provider.embed = lambda _: [[float("nan"), 1]]
    with pytest.raises(ServiceError, match="EMBEDDING_INVALID_RESPONSE"):
        service.prepare(RECEIPT)
    assert service.repo.document(RECEIPT) is None


def test_busy_analysis_rejects_question_before_paid_embedding(service):
    service.prepare(RECEIPT)
    before = len(service.provider.embedded)
    service.model_lock.acquire()
    try:
        with pytest.raises(ServiceError, match="ANALYSIS_IN_PROGRESS"):
            service.ask(RECEIPT, "매출")
    finally:
        service.model_lock.release()
    assert len(service.provider.embedded) == before


def test_missing_receipt_and_blank_question(service):
    with TestClient(create_app(service.config, service)) as client:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert client.post("/internal/v1/questions", headers=headers, json={
            "question": "매출?",
        }).json()["detail"] == "DISCLOSURE_SELECTION_REQUIRED"
        assert client.post("/internal/v1/questions", headers=headers, json={
            "question": "   ", "rceptNo": RECEIPT,
        }).status_code == 422


def test_basic_analysis_is_persisted_and_shared_between_requests(service):
    service.prepare(RECEIPT, build_embeddings=False)
    assert not service.provider.embedded
    first = service.basic_analyze(RECEIPT)
    assert first.cached is False and first.model_calls == 1
    second_service = ResearchService(service.config, service.repo, service.dart, service.provider)
    second = second_service.basic_analyze(RECEIPT)
    assert second.cached is True and second.model_calls == 0
    assert len(service.provider.generated) == 1
    assert second.answer == first.answer


def test_analysis_model_change_invalidates_saved_summary(service):
    service.prepare(RECEIPT, build_embeddings=False)
    service.basic_analyze(RECEIPT)
    service.config.openai_model = "new-test-model"
    assert service.basic_analyze(RECEIPT).cached is False
    assert len(service.provider.generated) == 2


def test_basic_analysis_lease_prevents_duplicate_calls_across_services(service):
    import time
    from concurrent.futures import ThreadPoolExecutor

    service.prepare(RECEIPT, build_embeddings=False)
    original = service.provider.generate

    def delayed(*args):
        time.sleep(0.05)
        return original(*args)

    service.provider.generate = delayed
    other = ResearchService(service.config, service.repo, service.dart, service.provider)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda agent: agent.basic_analyze(RECEIPT), [service, other]))
    assert len(service.provider.generated) == 1
    assert sorted(result.cached for result in results) == [False, True]


def test_full_analysis_is_cached(service):
    service.prepare(RECEIPT)
    service.analyze(RECEIPT, max_calls=2)
    assert service.analyze(RECEIPT, max_calls=1).cached is True
    assert len(service.provider.generated) == 1


def test_text_only_collection_can_be_indexed_later_without_downloading(service):
    service.collect_original(service.dart.lookup(RECEIPT))
    assert service.repo.document(RECEIPT).chunk_count == 0
    assert not service.provider.embedded
    service.dart.document = lambda _: pytest.fail("must reuse the stored original")
    service.prepare(RECEIPT, build_embeddings=False)
    assert service.repo.document(RECEIPT).chunk_count > 0
    assert not service.provider.embedded
    service.prepare(RECEIPT)
    assert service.provider.embedded
    assert service.ask(RECEIPT, "매출?").status == "answered"


def test_basic_overview_from_original_does_not_build_full_vector_index(service):
    service.collect_original(service.dart.lookup(RECEIPT))
    answer = service.basic_analyze(RECEIPT)
    assert answer.status == "answered" and answer.sources
    assert service.repo.document(RECEIPT).chunk_count == 0
    assert not service.provider.embedded


def test_local_chat_hides_service_token_and_rejects_cross_site_requests(service):
    with TestClient(create_app(service.config, service), base_url="http://127.0.0.1:8000") as client:
        home = client.get("/")
        assert home.status_code == 200 and "공시 질문" in home.text
        assert TOKEN not in home.text
        assert "HttpOnly" in home.headers["set-cookie"]
        assert client.get("/chat-assets/chat.js").status_code == 200
        assert client.post("/chat/api/messages", headers={"Origin": "https://evil.example"},
                           json={"message": "삼성전자 공시"}).status_code == 403
        assert client.get("/chat/api/status", headers={"Host": "evil.example"}).status_code == 403
        assert client.get("/chat/api/tasks/nonexistent").status_code == 404


def test_natural_language_chat_finds_report_and_reuses_basic_analysis(service, monkeypatch):
    import time
    service.repo.save_companies([Company(
        company_id="test-company", corp_code="00126380", corp_name="예시기업", stock_code="005930",
    ), Company(company_id="unlisted", corp_code="00999999", corp_name="주요")])
    monkeypatch.setattr("investlab_ai.agent.chat.regular_reports",
                        lambda *args, **kwargs: [service.dart.lookup(RECEIPT)])
    with TestClient(create_app(service.config, service), base_url="http://127.0.0.1:8000") as client:
        client.get("/")

        def ask(text, **extra):
            response = client.post("/chat/api/messages", headers={"Origin": "http://127.0.0.1:8000"},
                                   json={"message": text, **extra})
            assert response.status_code == 202
            task = response.json()["taskId"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = client.get("/chat/api/tasks/" + task).json()
                if result["status"] != "running":
                    return task, result
                time.sleep(0.02)
            pytest.fail("chat task did not complete")

        identity, first = ask("예시기업 최신 공시 설명해줘")
        assert first["status"] == "completed" and first["result"]["cached"] is False
        _, second = ask("예시기업 최신 공시 설명해줘")
        assert second["result"]["cached"] is True
        assert len(service.provider.generated) == 1
        _, followup = ask("그럼 매출은 얼마야?", rceptNo=RECEIPT, previousTaskId=identity)
        assert followup["result"]["analysisScope"] == "question"
        assert len(service.provider.generated) == 2
        with TestClient(create_app(service.config, service), base_url="http://127.0.0.1:8000") as stranger:
            stranger.get("/")
            assert stranger.get("/chat/api/tasks/" + identity).status_code == 404


def test_typo_selection_analyzes_a_company_with_no_saved_reports(service, monkeypatch):
    from investlab_ai.agent.chat import ChatService
    from investlab_ai.schemas import ChatRequest

    company = Company(company_id="v", corp_code="00126380", corp_name="브이원텍", stock_code="251630")
    service.repo.save_companies([company])
    monkeypatch.setattr("investlab_ai.agent.chat.regular_reports",
                        lambda *args, **kwargs: [service.dart.lookup(RECEIPT)])
    chat = ChatService(service)
    try:
        assert service.repo.counts()["documents"] == 0
        chat._run("typo", "owner", ChatRequest(message="브이원택 공시좀 분석해줘"))
        suggestion = service.repo.task("typo", "owner")
        assert suggestion["status"] == "completed"
        assert suggestion["choices"][0]["corpName"] == "브이원텍"
        assert "혹시" in suggestion["message"]
        assert service.provider.generated == []  # A typo must not trigger a paid analysis.
        chat._run("selected", "owner", ChatRequest(
            message=suggestion["originalMessage"], corp_code=company.corp_code))
        result = service.repo.task("selected", "owner")
        assert result["status"] == "completed" and not result["result"]["cached"]
        assert result["company"]["corpName"] == "브이원텍"
        assert len(service.provider.generated) == 1
        chat._run("again", "owner", ChatRequest(message="251630 공시 설명해줘"))
        assert service.repo.task("again", "owner")["result"]["cached"]
        assert len(service.provider.generated) == 1
    finally:
        chat.close()


def test_library_question_reports_distinct_saved_basic_analyses_without_calling_gpt(service):
    from investlab_ai.agent.chat import ChatService
    from investlab_ai.schemas import ChatRequest

    service.repo.save_companies([Company(
        company_id="test", corp_code="00126380", corp_name="예시기업", stock_code="005930")])
    service.collect_original(service.dart.lookup(RECEIPT))
    basic = service.basic_analyze(RECEIPT)
    service.repo.save_analysis("extra-basic-version", basic)
    service.repo.save_analysis("full-analysis", basic.model_copy(update={"analysis_scope": "full"}))
    assert service.repo.basic_analysis_counts() == {"basicReports": 1, "analyzedCompanies": 1}
    chat = ChatService(service)
    try:
        chat._run("capacity", "owner", ChatRequest(
            message="분석할수 있는기업이 몇개냐고", rcept_no=RECEIPT))
        result = service.repo.task("capacity", "owner")
        assert result["status"] == "completed"
        assert "기업은 1개" in result["message"] and "공시는 1건" in result["message"]
        assert len(service.provider.generated) == 1
        assert not service.provider.embedded
    finally:
        chat.close()


@pytest.mark.parametrize("receipt,filed_on", [(RECEIPT, date(2026, 3, 16)),
                                            ("20190316000001", date(2019, 3, 16))])
def test_packaged_analysis_works_without_dart_or_openai_keys(service, receipt, filed_on):
    from investlab_ai.agent.chat import ChatService
    from investlab_ai.schemas import ChatRequest

    service.repo.save_companies([Company(
        company_id="test", corp_code="00126380", corp_name="예시기업", stock_code="005930")])
    disclosure = service.dart.lookup(receipt).model_copy(update={"filed_on": filed_on})
    service.collect_original(disclosure)
    service.basic_analyze(receipt)
    original_provider = service.provider
    service.dart = service.provider = None
    chat = ChatService(service)
    try:
        chat._run("offline", "owner", ChatRequest(message="예시기업 최신 공시 설명해줘"))
        result = service.repo.task("offline", "owner")
        assert result["status"] == "completed" and result["result"]["cached"]
        assert result["result"]["modelCalls"] == 0
        assert "저장된 자료 중 최신" in result["notice"]
        assert len(original_provider.generated) == 1
        with TestClient(create_app(service.config, service), base_url="http://127.0.0.1:8000") as client:
            client.get("/")
            status = client.get("/chat/api/status").json()
            assert not status["ready"] and status["savedAnalysisAvailable"]
    finally:
        chat.close()


def test_packaged_snapshot_does_not_pretend_to_have_unstored_companies(service):
    from investlab_ai.agent.chat import ChatService
    from investlab_ai.schemas import ChatRequest

    service.repo.save_companies([Company(
        company_id="new", corp_code="00123456", corp_name="새기업", stock_code="123450")])
    service.dart = service.provider = None
    chat = ChatService(service)
    try:
        chat._run("missing", "owner", ChatRequest(message="새기업 최신 공시 설명해줘"))
        result = service.repo.task("missing", "owner")
        assert result["status"] == "error" and result["code"] == "DART_NOT_CONFIGURED"
        assert "DART API 키" in result["message"]
    finally:
        chat.close()


def test_shared_database_reuses_analyses_without_interrupting_another_server(service):
    service.collect_original(service.dart.lookup(RECEIPT))
    service.basic_analyze(RECEIPT)
    other_config = service.config.model_copy(update={"instance_id": "another-team-client"})
    other_repo = Repository(other_config)
    other_service = ResearchService(other_config, other_repo, None, None)
    try:
        assert other_service.basic_analyze(RECEIPT).cached
        assert len(service.provider.generated) == 1
        service.repo.put_task("first-task", "same-owner-for-test", {"status": "running"})
        other_repo.put_task("second-task", "same-owner-for-test", {"status": "running"})
        service.repo.interrupt_chat_tasks()
        assert service.repo.task("first-task", "same-owner-for-test")["code"] == "SERVER_RESTARTED"
        assert other_repo.task("second-task", "same-owner-for-test")["status"] == "running"
        assert service.repo.task("second-task", "same-owner-for-test") is None
        assert other_repo.task("first-task", "same-owner-for-test") is None
    finally:
        other_repo.close()


@pytest.mark.parametrize("path,method", [
    ("/internal/v1/companies/sync", "POST"),
    ("/internal/v1/companies?query=삼성", "GET"),
    (f"/internal/v1/disclosures/{RECEIPT}/prepare", "POST"),
    (f"/internal/v1/disclosures/{RECEIPT}/analysis-plan", "GET"),
    (f"/internal/v1/disclosures/{RECEIPT}/analyze", "POST"),
])
def test_new_endpoints_keep_service_auth(service, path, method):
    with TestClient(create_app(service.config, service)) as client:
        assert client.request(method, path, json={}).status_code == 401
