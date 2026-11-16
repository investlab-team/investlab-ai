import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from investlab_ai.agent.chat import ChatService
from investlab_ai.agent.overseas_chat import resolve_overseas
from investlab_ai.agent.sec_service import SecService
from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.sec import SecClient, issuer_id, sec_chunks
from investlab_ai.ingestion.us_ranking import us_top_companies
from investlab_ai.main import create_app
from investlab_ai.market import NEW_YORK, Candle, Quote, indicators
from investlab_ai.providers.toss import TossClient
from investlab_ai.schemas import ChatRequest, Claim, Company, Draft, OverseasCompany, SecFiling
from investlab_ai.storage import Repository

TOKEN = "sec-test-only-service-token-123456789012345"
ISSUER = OverseasCompany(company_id=issuer_id("0000320193"), cik="0000320193", corp_name="Apple Inc.",
                         stock_code="AAPL", aliases=["애플", "AAPL"])
FILING = SecFiling(accession="0000320193-25-000079", cik=ISSUER.cik, symbol="AAPL", issuer_name=ISSUER.corp_name,
                   form="10-K", filed_on="2025-10-31", report_date="2025-09-27", primary_document="aapl.htm",
                   source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl.htm")
HTML = b'''<html><body><div style="display:none">SECRET_HIDDEN</div><h1>Item 1. Business</h1>
<p>Apple offers products and services to customers.</p><h2>Item 7. Management discussion</h2>
<p>Amounts in millions of US dollars, except per share amounts. Years ended September 27, 2025 and September 28, 2024.</p>
<table><tr><th>USD millions</th><th>2025</th><th>2024</th></tr><tr><td>Net sales</td><td>100</td><td>90</td></tr></table>
<h2>Item 1A. Risk Factors</h2><p>Demand and competition may affect results.</p></body></html>'''


def config(tmp_path):
    return Settings(service_token=TOKEN, local_sqlite_path=tmp_path / "sec.db", openai_model="fake-model",
                    sec_user_agent="UnitTest contact=contact@unit-test.invalid", private_cache_dir=tmp_path / "cache")


def test_html_removes_hidden_content_and_preserves_table_context():
    chunks = sec_chunks(HTML, FILING.accession)
    text = "\n".join(c.text for c in chunks)
    assert "SECRET_HIDDEN" not in text and "Net sales | 100 | 90" in text
    table = next(c for c in chunks if "Net sales" in c.text)
    assert "USD millions | 2025 | 2024" in table.text and "per share" in table.text
    assert [c.chunk_id for c in chunks] == [c.chunk_id for c in sec_chunks(HTML, FILING.accession)]


def test_sec_html_comments_are_not_visible_evidence():
    text = b"<html><body><!-- invisible SEC comment --><p>Visible disclosure.</p></body></html>"
    chunks = sec_chunks(text, FILING.accession)
    assert "Visible disclosure" in chunks[0].text
    assert "invisible" not in " ".join(c.text for c in chunks)


def test_filings_include_historical_pages_and_amendments_only_in_range(tmp_path):
    def group(forms, years, names):
        return {"form": forms, "filingDate": [f"{y}-10-31" for y in years],
                "accessionNumber": names, "primaryDocument": ["aapl.htm"] * len(forms),
                "reportDate": [f"{y}-09-27" for y in years]}
    recent = group(["10-K", "8-K"], [2025, 2025], [FILING.accession, "0000320193-25-000080"])
    old = group(["10-K/A", "10-Q", "20-F"], [2024, 2021, 2024],
                ["0000320193-24-000079", "0000320193-21-000001", "0000320193-24-000080"])
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith("submissions-001.json"):
            return httpx.Response(200, json=old)
        return httpx.Response(200, json={"filings": {"recent": recent, "files": [
            {"name": "CIK0000320193-submissions-001.json", "filingFrom": "2021-01-01", "filingTo": "2024-12-31"}]}})
    client = SecClient(config(tmp_path), httpx.Client(transport=httpx.MockTransport(respond)))
    result = client.filings(ISSUER, date(2023, 11, 1), date(2025, 11, 1))
    assert {f.form for f in result} == {"10-K", "10-K/A", "20-F"} and len(result) == 3
    assert len(calls) == 2 and all(f.source_url.startswith("https://www.sec.gov/Archives/") for f in result)
    client.close()


def test_sec_rejects_bad_contact_and_non_sec_urls(tmp_path):
    with pytest.raises(ServiceError, match="SEC_CONTACT_REQUIRED"):
        SecClient(config(tmp_path).model_copy(update={"sec_user_agent": "InvestLab no-contact"}))
    client = SecClient(config(tmp_path), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    with pytest.raises(ServiceError, match="SEC_INVALID_URL"):
        client.get("https://example.org/report.htm")
    with pytest.raises(ServiceError, match="SEC_ACCESS_DENIED"):
        client.get(FILING.source_url)
    client.close()


def test_sec_catalog_combines_share_classes_by_cik(tmp_path):
    payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
        [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"], [1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"],
        [99, "Unlisted issuer", "UNKNOWN", None]]}
    client = SecClient(config(tmp_path), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))))
    rows = client.companies()
    assert len(rows) == 1 and {"GOOG", "GOOGL", "구글"}.issubset(rows[0].aliases)
    client.close()


def test_market_cap_ranking_uses_exact_usd_and_unique_issuer(monkeypatch):
    alphabet = ISSUER.model_copy(update={"cik": "0001652044", "stock_code": "GOOG", "aliases": ["GOOG", "GOOGL"]})
    html = '''<table><tbody>
    <tr><td>1</td><td><span class="company-code">GOOG</span></td><td>price</td><td data-sort="4000000000001">4T</td></tr>
    <tr><td>2</td><td><span class="company-code">GOOGL</span></td><td>price</td><td data-sort="4000000000001">4T</td></tr>
    <tr><td>3</td><td><span class="company-code">AAPL</span></td><td>price</td><td data-sort="3000000000002">3T</td></tr>
    </tbody></table>'''
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    monkeypatch.setattr("investlab_ai.ingestion.us_ranking.httpx.Client", lambda **kwargs: client)
    result = us_top_companies([ISSUER, alphabet], limit=2)
    assert [r["marketCapUsd"] for r in result["companies"]] == ["4000000000001", "3000000000002"]
    assert len({r["issuer"]["cik"] for r in result["companies"]}) == 2


def test_40f_adds_html_financial_exhibits_and_skips_pdf(tmp_path):
    filing = FILING.model_copy(update={"form": "40-F"})
    index = '''<table class="tableFile">
    <tr><td>2</td><td>Annual financials</td><td><a href="/Archives/ex99.htm">ex99.htm</a></td><td>EX-99.1</td></tr>
    <tr><td>3</td><td>PDF exhibit</td><td><a href="/Archives/ex99.pdf">ex99.pdf</a></td><td>EX-99.2</td></tr>
    </table>'''
    def respond(request):
        assert not request.url.path.endswith(".pdf")
        return httpx.Response(200, content=index.encode() if request.url.path.endswith("-index.html") else HTML)
    client = SecClient(config(tmp_path), httpx.Client(transport=httpx.MockTransport(respond)))
    parts = client.document_parts(filing)
    assert [p["name"] for p in parts] == ["aapl.htm", "ex99.htm"]
    assert parts[1]["sourceUrl"].endswith("/ex99.htm")
    client.close()


class Provider:
    def __init__(self):
        self.calls = 0

    def generate_sec(self, question, evidence):
        self.calls += 1
        return Draft(status="answered", claims=[Claim(category="business", text="제품과 서비스를 제공합니다.",
                     evidence_ids=[evidence[0].chunk_id])])


class Client:
    def __init__(self):
        self.calls = 0

    def document_parts(self, filing):
        self.calls += 1
        return [{"name": filing.primary_document, "raw": HTML, "sourceUrl": filing.source_url}]

    def close(self):
        pass


def test_immutable_original_zip_and_basic_cache_across_restarts(tmp_path):
    cfg, provider, client = config(tmp_path), Provider(), Client()
    repo = Repository(cfg)
    repo.initialize_local()
    sec = SecService(ResearchService(cfg, repo, None, provider), client)
    sec.collect(ISSUER, FILING)
    sec.collect(ISSUER, FILING)
    assert client.calls == 1 and sec.repo.counts()["documents"] == 1
    with zipfile.ZipFile(io.BytesIO(sec.original(FILING))) as archive:
        assert archive.read("aapl.htm") == HTML and "manifest.json" in archive.namelist()
    first = sec.basic(FILING)
    assert first["modelCalls"] == 1 and not first["cached"] and provider.calls == 1
    sec.close()
    repo.close()
    repo = Repository(cfg)
    sec = SecService(ResearchService(cfg, repo, None, provider), Client())
    assert sec.basic(FILING)["modelCalls"] == 0 and provider.calls == 1
    assert sec.repo.counts()["analyzedCompanies"] == 1
    # Another model's cached result must not inflate the number of analyzed reports.
    sec.repo.save_analysis("a" * 64, FILING.accession, first)
    assert sec.repo.counts()["analyses"] == 1
    repo.close()


def test_sec_shared_lease_prevents_two_paid_generations(tmp_path):
    cfg, provider, started, release = config(tmp_path), Provider(), Event(), Event()
    original = provider.generate_sec
    def delayed(question, evidence):
        started.set()
        assert release.wait(5)
        return original(question, evidence)
    provider.generate_sec = delayed
    repo = Repository(cfg)
    repo.initialize_local()
    sec = SecService(ResearchService(cfg, repo, None, provider), Client())
    sec.collect(ISSUER, FILING)
    other_repo = Repository(cfg)
    other_provider = Provider()
    other = SecService(ResearchService(cfg, other_repo, None, other_provider), Client())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sec.basic, FILING)
        assert started.wait(5)
        try:
            with pytest.raises(ServiceError, match="ANALYSIS_IN_PROGRESS"):
                other.basic(FILING)
            assert other_provider.calls == 0
        finally:
            release.set()
        assert future.result()["modelCalls"] == 1
    assert other.basic(FILING)["modelCalls"] == 0 and provider.calls == 1
    other_repo.close()
    repo.close()


def test_new_run_does_not_count_another_runs_cached_analysis_as_completed(tmp_path):
    cfg, provider = config(tmp_path), Provider()
    repo = Repository(cfg)
    repo.initialize_local()
    sec = SecService(ResearchService(cfg, repo, None, provider), Client())
    sec.collect(ISSUER, FILING)
    sec.basic(FILING)
    sec.repo.save_run("new-run", {"runId": "new-run"})
    sec.repo.add_items("new-run", [FILING])
    assert sec.repo.run_counts("new-run") == {"collected": 1, "analyzed": 0, "analyzedCompanies": 0}
    sec.repo.item_status("new-run", FILING.accession, "analyzed")
    assert sec.repo.run_counts("new-run") == {"collected": 1, "analyzed": 1, "analyzedCompanies": 1}
    repo.close()


def test_korean_and_explicit_share_class_resolution():
    alphabet = ISSUER.model_copy(update={"stock_code": "GOOG", "aliases": ["GOOG", "GOOGL", "구글"]})
    assert resolve_overseas("애플 공시 설명해줘", [ISSUER])[0].cik == ISSUER.cik
    assert resolve_overseas("$GOOGL 주가 오를까", [alphabet])[0].stock_code == "GOOGL"
    assert not resolve_overseas("API 분석해줘", [ISSUER.model_copy(update={"stock_code": "API", "aliases": []})])
    tsm = ISSUER.model_copy(update={"stock_code": "TSM", "corp_name": "Taiwan Semiconductor Manufacturing Co Ltd",
                                   "aliases": ["TSM", "TSMC", "티에스엠씨"]})
    assert resolve_overseas("TSMC 주가 오를까", [tsm])[0].stock_code == "TSM"


def test_us_fractional_price_and_currency_rejection(tmp_path):
    cfg = config(tmp_path).model_copy(update={"toss_client_id": "fake", "toss_client_secret": "fake"})
    from pydantic import SecretStr
    cfg.toss_client_id = SecretStr("fake")
    cfg.toss_client_secret = SecretStr("fake")
    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600})
        return httpx.Response(200, json={"result": [{"symbol": "AAPL", "currency": "USD", "lastPrice": "212.1234"}]})
    client = TossClient(cfg, httpx.Client(transport=httpx.MockTransport(respond)))
    assert client.quote("AAPL").last_price == Decimal("212.1234")
    assert Quote(symbol="AAPL", lastPrice="212.1", currency="USD").currency == "USD"
    client.close()


def test_compact_us_catalog_needs_only_symbol_and_name(tmp_path):
    from pydantic import SecretStr
    cfg = config(tmp_path).model_copy(update={"toss_client_id": SecretStr("fake"), "toss_client_secret": SecretStr("fake")})
    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600})
        assert request.url.path == "/api/v1/stocks/all"
        return httpx.Response(200, json={"result": [{"symbol": "RIVN", "name": "리비안"}]})
    client = TossClient(cfg, httpx.Client(transport=httpx.MockTransport(respond)))
    assert all(r["symbol"] == "RIVN" for r in client.us_catalog())
    assert len(client.us_catalog()) == 3
    client.close()


def test_sec_share_class_ticker_resolves_to_broker_symbol(tmp_path):
    from pydantic import SecretStr
    cfg = config(tmp_path).model_copy(update={"toss_client_id": SecretStr("fake"), "toss_client_secret": SecretStr("fake")})
    def respond(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "fake-token", "token_type": "Bearer", "expires_in": 3600})
        if request.url.path == "/api/v1/stocks/all":
            return httpx.Response(200, json={"result": [{"symbol": "BRK.B", "name": "버크셔 해서웨이 B"}]})
        assert request.url.params["symbols"] == "BRK.B"
        return httpx.Response(200, json={"result": [{"symbol": "BRK.B", "currency": "USD", "lastPrice": "500.25"}]})
    client = TossClient(cfg, httpx.Client(transport=httpx.MockTransport(respond)))
    assert client.quote("BRK-B").symbol == "BRK.B"
    client.close()


def test_new_york_daily_cutoff_is_not_the_korean_date():
    # Seoul is already November 16, but New York is November 15. Omit NY's current bar.
    now = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
    start = datetime(2026, 9, 1, 0, 0, tzinfo=NEW_YORK)
    rows = [Candle(timestamp=start + timedelta(days=i), openPrice="100", highPrice="101", lowPrice="99",
                   closePrice="100", volume="10", currency="USD") for i in range(76)]
    result = indicators(rows, now)
    assert datetime.fromisoformat(result["completedThrough"]).astimezone(NEW_YORK).date() == date(2026, 11, 14)
    assert result["marketTimeZone"] == "America/New_York"
    with pytest.raises(ServiceError, match="TOSS_INVALID_RESPONSE"):
        indicators([*rows, rows[0].model_copy(update={"currency": "KRW"})], now)


def test_overseas_chat_uses_sec_without_dart_catalog(tmp_path):
    cfg, provider = config(tmp_path), Provider()
    repo = Repository(cfg)
    repo.initialize_local()
    chat = ChatService(ResearchService(cfg, repo, None, provider))
    chat.overseas.sec.client = Client()
    chat.overseas.sec.collect(ISSUER, FILING)
    # Cached report selection needs no contact/network access.
    cfg.sec_user_agent = None
    chat._run("sec-task", "owner", ChatRequest(message="애플 공시 설명해줘"))
    result = repo.task("sec-task", "owner")
    assert result["status"] == "completed" and result["secFiling"]["accession"] == FILING.accession
    assert not repo.has_companies() and provider.calls == 1
    assert repo.task("sec-task", "stranger") is None
    chat.close()
    repo.close()


@pytest.mark.parametrize(("alias", "symbol", "domestic_name"), [
    ("메타", "META", "메타랩스"), ("인텔", "INTC", "인텔리안테크"),
])
def test_exact_overseas_alias_wins_over_partial_domestic_name(tmp_path, alias, symbol, domestic_name):
    cfg = config(tmp_path)
    cfg.sec_user_agent = None
    repo = Repository(cfg)
    repo.initialize_local()
    repo.save_companies([Company(company_id="kr-test", corp_code="00100000", corp_name=domestic_name, stock_code="123450")])
    chat = ChatService(ResearchService(cfg, repo, None, None))
    issuer = ISSUER.model_copy(update={"corp_name": "US issuer", "stock_code": symbol, "aliases": [symbol, alias]})
    chat.overseas.sec.repo.save_issuer(issuer)
    def answer(task_id, owner, body, company, previous_task, progress):
        repo.put_task(task_id, owner, {"status": "completed", "matchedSymbol": company.stock_code})
    chat.overseas.answer = answer
    chat._run("alias-task", "owner", ChatRequest(message=alias + " 공시 설명해줘"))
    assert repo.task("alias-task", "owner")["matchedSymbol"] == symbol
    chat.close()
    repo.close()


def test_authenticated_sec_api_and_no_implicit_paid_preparation(tmp_path):
    cfg, provider = config(tmp_path), Provider()
    cfg.sec_user_agent = None
    repo = Repository(cfg)
    repo.initialize_local()
    research = ResearchService(cfg, repo, None, provider)
    sec = SecService(research, Client())
    sec.collect(ISSUER, FILING)
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_app(cfg, research), base_url="http://127.0.0.1") as client:
        path = f"/internal/v1/sec-disclosures/{FILING.accession}"
        assert client.post(path + "/basic-analysis").status_code == 401
        unknown = "/internal/v1/sec-disclosures/0000320193-25-000080/basic-analysis"
        assert client.post(unknown, headers=headers).status_code == 409
        assert provider.calls == 0
        companies = client.get("/internal/v1/overseas-companies", params={"query": "애플"}, headers=headers).json()
        assert companies[0]["cik"] == ISSUER.cik and companies[0]["corpCode"] is None
        prepared = client.post(path + "/prepare", params={"cik": ISSUER.cik}, headers=headers)
        assert prepared.status_code == 200 and prepared.json()["chunkCount"] > 0
        assert client.post(path + "/prepare", params={"cik": "0000000001"}, headers=headers).status_code == 422
        answer = client.post(path + "/basic-analysis", headers=headers)
        assert answer.status_code == 200 and answer.json()["result"]["modelCalls"] == 1
        assert client.post(path + "/basic-analysis", headers=headers).json()["result"]["cached"]
        assert provider.calls == 1
        client.get("/")
        cfg.sec_user_agent = "UnitTest contact=contact@unit-test.invalid"
        status = client.get("/chat/api/status").json()
        assert status["savedAnalysisAvailable"] and status["ready"]
        assert research.dart is None
        question = client.post(path + "/questions", json={"question": "사업이 뭐야?"}, headers=headers)
        assert question.status_code == 200 and question.json()["filing"]["accession"] == FILING.accession
        assert provider.calls == 2
    repo.close()
