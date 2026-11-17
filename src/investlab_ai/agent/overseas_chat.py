"""US issuer resolution and SEC chat, independent of the DART catalog."""
import re
from datetime import datetime

from investlab_ai.agent.sec_service import SecService
from investlab_ai.ingestion.catalog import years_ago
from investlab_ai.ingestion.sec import KOREAN_NAMES
from investlab_ai.market import NEW_YORK, stock_intent
from investlab_ai.schemas import OverseasCompany, SecFiling


def normalized(text: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", text.casefold())


def resolve_overseas(message: str, issuers: list[OverseasCompany]) -> list[OverseasCompany]:
    compact = normalized(message)
    found = []
    ignored = {"AI", "API", "RSI", "ETF", "PER", "PBR", "USD", "KRW", "SEC", "DART", "US"}
    for issuer in issuers:
        short_name = re.sub(r"\b(?:incorporated|inc\.?|corporation|corp\.?|limited|ltd\.?|plc)\b.*$", "", issuer.corp_name, flags=re.I).strip(" .,")
        candidates = [issuer.stock_code, issuer.corp_name, short_name, *issuer.aliases]
        names = {issuer.corp_name, short_name, *KOREAN_NAMES.get(issuer.stock_code, [])}
        score, explicit_symbol = 0, None
        for name in candidates:
            if not name:
                continue
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", name) and (name == issuer.stock_code or name not in names):
                if name in ignored:
                    continue
                if re.search(r"(?<![A-Za-z0-9])\$?" + re.escape(name) + r"(?![A-Za-z0-9])", message, re.I):
                    score, explicit_symbol = max(score, 100 + len(name)), name
            elif len(normalized(name)) >= 2 and normalized(name) in compact:
                score = max(score, len(normalized(name)))
        if score:
            found.append((score, issuer.model_copy(update={"stock_code": explicit_symbol}) if explicit_symbol else issuer))
    if not found:
        return []
    best = max(score for score, _ in found)
    return [issuer for score, issuer in found if score == best]


class OverseasChat:
    def __init__(self, research, market):
        self.research, self.market = research, market
        self.sec = SecService(research)

    def close(self):
        self.sec.close()

    def candidates(self, message: str, symbol: str | None = None) -> list[OverseasCompany]:
        query = symbol or message
        issuers = self.sec.repo.issuers()
        found = resolve_overseas(query, issuers)
        # Stored public identities support cached answers even without a SEC contact setting.
        if found or not self.research.config.sec_user_agent:
            return found
        issuers = self.sec.require_client().companies()
        found = resolve_overseas(query, issuers)
        if found:
            return found
        if self.research.config.toss_client_id and re.search(r"[가-힣]{2,}", query):
            # Korean labels supplied by the personal broker API stay local, never in Neon.
            rows = self.market._client().us_catalog()
            text = normalized(query)
            hint = normalized(re.split(r"주가|주식|공시|최신|최근|설명|분석|현재|보고서|알려", query)[0])
            names = [(len(normalized(r.get("name", ""))), r) for r in rows
                     if len(normalized(r.get("name", ""))) >= 2 and (normalized(r["name"]) in text
                         or (len(hint) >= 2 and normalized(r["name"]).startswith(hint)))]
            if names:
                longest = max(size for size, _ in names)
                matched = []
                for size, row in names:
                    if size == longest:
                        matched.extend(resolve_overseas(row["symbol"].replace(".", "-"), issuers))
                return list({c.cik: c for c in matched}.values())
        return []

    def answer(self, task_id, owner, body, company, previous_task, progress):
        repo = self.research.repo
        intent = stock_intent(body.message)
        same = bool(previous_task and previous_task.get("company", {}).get("cik") == company.cik)
        if (not intent and same and previous_task.get("market")
                and not re.search(r"공시|보고서|매출|이익|부채|현금|배당|실적", body.message)
                and re.search(r"그럼|그러면|근거|왜|위험|리스크|개월|한\s*달|주일|내일|장기|중기|단기", body.message)):
            intent = "outlook"
        if intent:
            question = body.message
            if same and previous_task.get("market"):
                question = f"이전 질문(대화 문맥): {previous_task.get('question', '')[:1000]}\n현재 질문: {question}"
            market = self.market.analyze(company, question, intent, owner, progress)
            repo.put_private_task(task_id, owner, {"status": "completed", "company": company.model_dump(mode="json", by_alias=True),
                                                 "question": body.message, "market": market})
            return
        progress(f"{company.corp_name}의 SEC 정기보고서를 찾고 있어요")
        previous = previous_task.get("secFiling") if same else None
        refresh = bool(re.search(r"최신|최근|20\d{2}|10-K|10-Q|20-F|40-F|사업보고서|분기보고서|연차보고서", body.message, re.I))
        if previous and not refresh:
            filing = SecFiling.model_validate(previous)
        else:
            today = datetime.now(NEW_YORK).date()
            if self.research.config.sec_user_agent:
                filings = self.sec.require_client().filings(company, years_ago(today), today)
            else:
                filings = self.sec.repo.filings(company.cik)
            year = re.search(r"20\d{2}", body.message)
            if year:
                filings = [f for f in filings if f.report_date and f.report_date.year == int(year.group())]
            form = re.search(r"10-K|10-Q|20-F|40-F", body.message, re.I)
            if form:
                filings = [f for f in filings if f.form.startswith(form.group().upper())]
            elif re.search(r"사업보고서|연차보고서", body.message):
                filings = [f for f in filings if f.form.split('/')[0] in {"10-K", "20-F", "40-F"}]
            elif "분기보고서" in body.message:
                filings = [f for f in filings if f.form.startswith("10-Q")]
            if not filings:
                repo.put_task(task_id, owner, {"status": "completed", "message":
                    "최근 3년에서 조건에 맞는 SEC 정기보고서를 찾지 못했어요. 미국 상장 티커와 보고서 기간을 확인해 주세요. 수시공시는 현재 수집 범위에 포함되지 않아요."})
                return
            filing = filings[0]
        progress(f"{company.corp_name} · {filing.form} 원문을 준비하고 있어요")
        self.sec.collect(company, filing)
        from investlab_ai.agent.chat import is_overview
        if is_overview(body.message):
            progress("저장된 SEC 기본 분석을 확인하고 있어요. 처음 보는 공시는 분석 후 저장해요")
            result = self.sec.basic(filing)
        else:
            progress("질문에 필요한 SEC 원문 근거를 검색하고 있어요")
            question = body.message
            if previous and previous.get("accession") == filing.accession:
                question = f"이전 질문(문맥): {previous_task.get('question', '')[:1000]}\n현재 질문: {question}"
            result = self.sec.ask(filing, question)
        repo.put_private_task(task_id, owner, {"status": "completed", "company": company.model_dump(mode="json", by_alias=True),
            "secFiling": filing.model_dump(mode="json", by_alias=True), "question": body.message, "result": result,
            "notice": "미국 상장기업의 SEC 정기보고서를 한국어로 설명해요. 보고대상일과 공시일을 구분해 주세요."})
