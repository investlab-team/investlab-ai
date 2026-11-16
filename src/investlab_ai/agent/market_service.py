"""On-demand personal market research, independent of the team's future market gateway."""
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from threading import Lock

from filelock import Timeout

from investlab_ai.agent.sec_service import SecService
from investlab_ai.agent.service import ResearchService
from investlab_ai.errors import ServiceError
from investlab_ai.market import KST, NEW_YORK, horizon, indicators
from investlab_ai.private_cache import PrivateCache
from investlab_ai.providers.toss import TossClient
from investlab_ai.schemas import Company, Evidence, OverseasCompany


class MarketService:
    def __init__(self, research: ResearchService, toss: TossClient | None = None):
        self.research = research
        self.toss = toss
        self.sec = SecService(research)
        self.client_lock = Lock()
        config = research.config
        self.cache = PrivateCache(config.private_cache_dir / "outlooks",
                                   research.repo.task_namespace, max_entries=256)

    def close(self):
        if self.toss:
            self.toss.close()
        self.sec.close()

    def _client(self):
        with self.client_lock:
            if self.toss is None:
                self.toss = TossClient(self.research.config)
        return self.toss

    def analyze(self, company: Company | OverseasCompany, question: str, kind: str, owner: str,
                progress=lambda stage: None) -> dict:
        symbol = company.stock_code
        if not symbol:
            raise ServiceError("MARKET_DOMESTIC_STOCK_REQUIRED", 422)
        progress(f"{company.corp_name}의 토스 시세를 확인하고 있어요")
        client = self._client()
        quote = client.quote(symbol)
        overseas = isinstance(company, OverseasCompany)
        if quote.currency != ("USD" if overseas else "KRW"):
            raise ServiceError("TOSS_INVALID_RESPONSE", 502)
        zone = NEW_YORK if overseas else KST
        now = datetime.now(UTC)
        warnings = []
        if overseas:
            warnings.append("미국 시세는 토스가 제공하는 일부 거래소 체결 기준이며 미국 전체 통합 최우선 호가(NBBO)가 아닙니다.")
        if quote.timestamp is None:
            warnings.append("토스 응답에 시세 시각이 없어 최신 체결인지 확인할 수 없습니다.")
        elif now - quote.timestamp > timedelta(minutes=15):
            warnings.append("마지막 체결 시각이 15분 이상 이전입니다. 휴장·거래 없음·지연 여부를 확인하세요.")
        result = {"kind": kind, "symbol": quote.symbol, "quote": quote.model_dump(mode="json", by_alias=True),
                  "retrievedAt": now.isoformat(), "horizon": None, "indicators": None,
                  "claims": [], "sources": [], "warnings": warnings, "cached": False,
                  "modelCalls": 0, "model": None, "disclosureDate": None}
        if kind == "quote":
            return result
        progress("수정주가 일봉으로 추세·변동성·거래량을 계산하고 있어요")
        metrics = indicators(client.candles(symbol), now)
        if metrics["currency"] != quote.currency:
            raise ServiceError("TOSS_INVALID_RESPONSE", 502)
        period = horizon(question.rsplit("현재 질문:", 1)[-1])
        result.update(indicators=metrics, horizon=period)
        warnings.append("기술적 지표는 당일 진행 중 일봉을 제외한 완료 거래일 기준입니다.")
        if now.astimezone(zone).date() - datetime.fromisoformat(metrics["completedThrough"]).astimezone(zone).date() > timedelta(days=7):
            warnings.append("가격 지표의 마지막 거래일이 7일 이상 이전입니다. 거래정지·자료 누락 등을 확인하세요.")
        evidence = [Evidence(
            chunk_id="market-quote", section="토스 개인 시세 응답",
            text=json.dumps(result["quote"], ensure_ascii=False),
            source_url="https://developers.tossinvest.com/docs/market-data",
        ), Evidence(
            chunk_id="market-indicators", section="Python 계산 · 수정주가 일봉 · 당일 봉 제외",
            text=json.dumps(metrics, ensure_ascii=False, allow_nan=False),
            source_url="https://developers.tossinvest.com/docs/market-data",
        )]
        # Reuse original source excerpts already saved with basic analyses; no embeddings or
        # duplicate basic-summary GPT call is needed for market outlooks.
        reports = [] if overseas else self.research.repo.stored_disclosures(company.corp_code)
        reports = [r for r in reports if r.filed_on <= now.astimezone(KST).date() and "철" not in r.remarks][:2]
        for report in reports:
            basic = self.research.repo.basic_analysis_for_report(report.rcept_no)
            if not basic:
                continue
            for item in basic.sources[:4]:
                evidence.append(item.model_copy(update={
                    "text": item.text[:3000],
                    "section": f"{report.report_name} · 공시일 {report.filed_on} · {item.section}",
                }))
            if basic.sources and result["disclosureDate"] is None:
                result["disclosureDate"] = report.filed_on.isoformat()
        if overseas:
            reports = self.sec.repo.filings(company.cik)
            if not reports and self.research.config.sec_user_agent:
                from investlab_ai.ingestion.catalog import years_ago
                today = now.astimezone(NEW_YORK).date()
                reports = self.sec.require_client().filings(company, years_ago(today), today)[:1]
                if reports:
                    self.sec.collect(company, reports[0])
            for report in reports[:2]:
                basic = self.sec.repo.latest_basic(report.accession)
                if basic:
                    items = [Evidence.model_validate(e) for e in basic.get("sources", [])][:4]
                else:
                    from investlab_ai.ingestion.sec import select_sec_evidence
                    items = select_sec_evidence(self.sec.records(report), report, 4000)[:5]
                for item in items:
                    evidence.append(item.model_copy(update={"text": item.text[:3000],
                        "section": f"SEC {report.form} · 보고대상일 {report.report_date} · 공시일 {report.filed_on} · {item.section}"}))
                if items and result["disclosureDate"] is None:
                    result["disclosureDate"] = report.filed_on.isoformat()
        if result["disclosureDate"] and overseas:
            warnings.append("SEC 공시 원문 발췌를 참고했습니다. 저장된 공시가 있으면 최신 SEC 목록은 새로 조회하지 않습니다.")
        elif result["disclosureDate"]:
            warnings.append("공시는 저장된 기본 분석의 원문 발췌를 재사용했으며 최신 DART 목록을 새로 조회하지 않았습니다.")
        else:
            warnings.append("저장된 공시 근거가 없어 이번 전망은 시세·기술적 지표에 한정됩니다.")
        identity = json.dumps({"version": "market-outlook-v2", "symbol": symbol, "question": question,
                               "owner": owner, "horizon": period, "model": self.research.config.openai_model,
                               "evidence": [e.model_dump() for e in evidence], "warnings": warnings},
                              sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        try:
            with self.cache.lock(key):
                cached = self.cache.get(key)
                if cached:
                    return {**cached, "cached": True, "modelCalls": 0,
                            "inputTokens": 0, "outputTokens": 0,
                            "retrievedAt": now.isoformat()}
                provider = self.research.require_ai()
                progress("시세와 공시를 함께 읽고 상승·하락 시나리오를 정리하고 있어요")
                with self.research.model_lock:
                    draft = provider.generate_market(
                        f"기업: {company.corp_name} ({symbol})\n분석 기간: {period}\n"
                        f"사용자 질문: {question}\n자료 한계: {' / '.join(warnings)}", evidence,
                    )
                    usage = getattr(provider, "last_usage", {})
                    self.research.validate_draft(draft, evidence)
                    for claim in draft.claims:
                        if re.search(r"(?:확률|승률|적중률)[^。.!?\n]{0,12}\d+(?:\.\d+)?\s*%|"
                                     r"\d+(?:\.\d+)?\s*%\s*(?:의\s*)?(?:상승|하락)?\s*확률", claim.text):
                            raise ServiceError("MARKET_UNSUPPORTED_PROBABILITY", 502)
                used = {key for claim in draft.claims for key in claim.evidence_ids}
                result.update(
                    status=draft.status, claims=[c.model_dump(by_alias=True) for c in draft.claims],
                    sources=[e.model_dump(by_alias=True) for e in evidence if e.chunk_id in used],
                    model=self.research.config.openai_model, modelCalls=1,
                    generatedAt=datetime.now(UTC).isoformat(),
                    inputTokens=usage.get("input_tokens", 0),
                    outputTokens=usage.get("output_tokens", 0),
                )
                self.cache.put(key, result, 86400)
                return result
        except Timeout:
            raise ServiceError("ANALYSIS_IN_PROGRESS", 409) from None
