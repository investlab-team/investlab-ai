import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from investlab_ai.agent.market_service import MarketService
from investlab_ai.agent.overseas_chat import OverseasChat
from investlab_ai.agent.service import ResearchService
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.catalog import regular_reports, years_ago
from investlab_ai.market import stock_intent
from investlab_ai.schemas import ChatRequest, Company, OverseasCompany

ALIASES = {"삼전": "삼성전자", "하닉": "SK하이닉스", "하이닉스": "SK하이닉스",
           "네이버": "NAVER", "엘지전자": "LG전자", "엘지화학": "LG화학",
           "엘지에너지솔루션": "LG에너지솔루션", "현대차": "현대자동차"}
ERRORS = {
    "SEC_CONTACT_REQUIRED": "SEC 공시 수집용 연락처 설정이 필요해요. 서버의 .env.sec 설정을 확인해 주세요.",
    "SEC_ACCESS_DENIED": "SEC가 공시 접근을 거절했어요. 서버의 요청자 연락처·접속 제한을 확인해야 해요.",
    "SEC_RATE_LIMITED": "SEC 공시 조회 한도에 도달했어요. 잠시 후 다시 질문해 주세요.",
    "SEC_UNAVAILABLE": "SEC 공시를 가져오지 못했어요. 잠시 후 다시 질문해 주세요.",
    "SEC_TIMEOUT": "SEC 공시 응답이 늦어지고 있어요. 잠시 후 다시 질문해 주세요.",
    "SEC_DOCUMENT_FORMAT_UNSUPPORTED": "이 SEC 보고서는 현재 지원하는 HTML 형식이 아니에요. 다른 보고서를 선택해 주세요.",
    "TOSS_NOT_CONFIGURED": "토스 시세 연결 설정이 필요해요. 서버의 Client ID와 Client Secret을 입력한 뒤 다시 실행해 주세요.",
    "TOSS_CREDENTIAL_MASKED": "별표로 가려진 값은 사용할 수 없어요. 토스에서 발급받은 전체 Client ID와 Secret을 서버에 입력해 주세요.",
    "TOSS_AUTH_FAILED": "토스 인증에 실패했어요. 발급 키를 확인해 주세요. 같은 키로 다른 서버가 토큰을 발급했다면 기존 토큰이 무효화될 수 있어요.",
    "TOSS_IP_NOT_ALLOWED": "토스가 접속을 허용하지 않았어요. Open API의 허용 IP에 이 서버의 공인 IP가 등록됐는지 확인해 주세요.",
    "TOSS_RATE_LIMITED": "토스 조회 한도에 도달했어요. 잠시 후 다시 질문해 주세요.",
    "TOSS_BUSY": "다른 토스 조회를 처리 중이에요. 잠시 후 다시 질문해 주세요.",
    "TOSS_TIMEOUT": "토스 시세 응답이 늦어지고 있어요. 잠시 후 다시 질문해 주세요.",
    "TOSS_UNAVAILABLE": "토스 시세를 가져오지 못했어요. 이전 가격을 최신 가격처럼 보여드리지 않으니 잠시 후 다시 질문해 주세요.",
    "TOSS_INVALID_RESPONSE": "토스 시세 데이터의 형식·가격·시각을 확인하지 못했어요. 잠시 후 다시 질문해 주세요.",
    "TOSS_REQUEST_FAILED": "토스 시세 조회 요청이 실패했어요. 서버 연결 설정을 확인해 주세요.",
    "TOSS_SYMBOL_NOT_FOUND": "토스에서 이 종목의 시세를 찾지 못했어요. 종목코드와 상장 상태를 확인해 주세요.",
    "MARKET_DOMESTIC_STOCK_REQUIRED": "현재 주가 분석은 국내 상장 종목의 시세를 지원해요.",
    "MARKET_HISTORY_INSUFFICIENT": "가격 지표를 계산할 완료 일봉이 부족해요(최소 61거래일). 현재가만 먼저 물어볼 수 있어요.",
    "MARKET_UNSUPPORTED_PROBABILITY": "검증되지 않은 상승 확률이 답변에 포함되어 표시하지 않았어요. 조건별 전망으로 다시 질문해 주세요.",
    "OPENAI_SPEND_LIMIT_REACHED": "OpenAI 조직 또는 프로젝트의 사용 금액 한도에 도달했어요. 크레딧 잔액과는 별개이므로 설정된 한도를 확인해 주세요.",
    "OPENAI_USAGE_LIMIT_REACHED": "OpenAI 조직의 API 사용 한도에 도달했어요. 조직의 사용 한도를 확인해 주세요.",
    "OPENAI_QUOTA_EXHAUSTED": "OpenAI API 크레딧이 소진됐어요. API 결제 잔액을 충전한 뒤 다시 질문해 주세요. 공시 원문은 그대로 보관돼요.",
    "AI_NOT_CONFIGURED": "서버의 OpenAI 키와 모델 설정을 확인해 주세요.",
    "DART_NOT_CONFIGURED": "이 기업의 저장된 공시를 찾지 못했어요. 새로운 공시를 가져오려면 .env에 본인의 DART API 키를 설정해 주세요.",
    "OPENAI_RATE_LIMITED": "OpenAI 사용 한도에 도달했어요. 결제·한도를 확인한 뒤 다시 질문해 주세요.",
    "OPENAI_REQUEST_FAILED": "OpenAI 분석 요청이 실패했어요. 모델 권한과 API 설정을 확인해 주세요.",
    "DART_RATE_LIMITED": "DART 조회 한도에 도달했어요. 잠시 후 다시 질문해 주세요.",
    "PREPARATION_IN_PROGRESS": "다른 공시를 준비 중이에요. 잠시 후 다시 질문해 주세요.",
    "ANALYSIS_IN_PROGRESS": "다른 분석이 진행 중이에요. 잠시 후 다시 질문해 주세요.",
    "DOCUMENT_TOO_LARGE": "이 보고서는 현재 처리 한도보다 커요. 다른 보고서를 선택해 주세요.",
    "LLM_INVALID_CITATIONS": "분석의 원문 근거를 검증하지 못했어요. 다시 질문해 주세요.",
}


def normalized(text: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", text.casefold().replace("주식회사", ""))


def company_hint(message: str) -> str:
    hint = re.split(r"최신|최근|공시|보고서|설명|분석|알려|요약|20\d{2}", message)[0].strip()
    return normalized(hint).removesuffix("의")


def resolve_companies(message: str, companies: list[Company], *, exact_only: bool = False) -> list[Company]:
    text = normalized(message)
    for alias, target in ALIASES.items():
        if normalized(alias) in text:
            text = text.replace(normalized(alias), normalized(target))
    def mentioned(company: Company) -> bool:
        name = normalized(company.corp_name)
        if re.fullmatch(r"[a-z0-9]{1,3}", name):
            named = bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(company.corp_name)
                                   + r"(?![A-Za-z0-9])", message, re.IGNORECASE))
        else:
            named = name in text
        ticker = bool(company.stock_code and re.search(
            r"(?<!\d)" + re.escape(company.stock_code) + r"(?!\d)", message,
        ))
        return named or ticker

    exact = [company for company in companies if mentioned(company)]
    if exact:
        longest = max(len(normalized(c.corp_name)) for c in exact)
        return [c for c in exact if len(normalized(c.corp_name)) == longest]
    if exact_only:
        return []
    key = company_hint(message)
    return [c for c in companies if len(key) >= 2 and key in normalized(c.corp_name)][:8]


def suggest_companies(message: str, companies: list[Company]) -> list[Company]:
    """Suggest a one-character typo; never choose a different issuer automatically."""
    key = company_hint(message)
    if len(key) < 3 or len(key) > 30 or key.isdigit():
        return []

    def near(name: str) -> bool:
        if abs(len(key) - len(name)) > 1:
            return False
        if len(key) == len(name):
            different = [i for i, (left, right) in enumerate(zip(key, name, strict=True))
                         if left != right]
            return len(different) == 1 or (
                len(different) == 2 and different[1] == different[0] + 1
                and key[different[0]] == name[different[1]]
                and key[different[1]] == name[different[0]]
            )
        short, long = sorted((key, name), key=len)
        index = next((i for i, (left, right) in enumerate(zip(short, long, strict=False))
                      if left != right), len(short))
        return short[index:] == long[index + 1:]

    matches = [c for c in companies if near(normalized(c.corp_name))]
    return sorted(matches, key=lambda c: c.corp_name)[:5]


def is_library_question(message: str) -> bool:
    text = normalized(message)
    subject = any(word in text for word in ("기업", "회사", "공시", "보고서", "분석"))
    count = any(word in text for word in ("몇개", "몇곳", "몇건", "몇종목", "개수", "건수", "기업수", "회사수"))
    availability = "분석" in text and "가능" in text and any(
        word in text for word in ("기업", "회사"))
    return subject and (count or availability)


def is_overview(message: str) -> bool:
    if stock_intent(message):
        return False
    specific = r"왜|원인|얼마|매출|이익|부채|현금|위험|리스크|배당|투자|비교|증가|감소|영업|자세|언제|구체"
    return not re.search(specific, message) or bool(re.search(r"전체.*(?:설명|분석|요약)", message))


class ChatService:
    def __init__(self, research: ResearchService):
        self.research = research
        self.market = MarketService(research)
        self.overseas = OverseasChat(research, self.market)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="investlab-chat")

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.market.close()
        self.overseas.close()

    def submit(self, owner: str, body: ChatRequest) -> str:
        identity = str(uuid4())
        self.research.repo.put_task(identity, owner, {"status": "running", "stage": "기업을 찾고 있어요"})
        self.executor.submit(self._run, identity, owner, body)
        return identity

    def _run(self, task_id: str, owner: str, body: ChatRequest):
        repo = self.research.repo

        def progress(stage: str):
            repo.put_task(task_id, owner, {"status": "running", "stage": stage})

        try:
            previous_task = (repo.task(str(body.previous_task_id), owner)
                             if body.previous_task_id else None)
            stored_companies = repo.all_companies(listed_only=True) if repo.has_companies() else []
            # A partial KR match such as 메타랩스 must not hide the exact US alias 메타.
            domestic = resolve_companies(body.message, stored_companies, exact_only=True)
            foreign = []
            if not body.corp_code and not domestic and not is_library_question(body.message):
                foreign = self.overseas.candidates(body.message, body.stock_symbol)
                if (not foreign and previous_task and previous_task.get("company", {}).get("cik")
                        and re.match(r"\s*(?:그럼|그러면|이\s*(?:종목|기업|회사)|주가|오를|왜|매출|현금|이익|위험|부채|얼마|장기|앞으로|다음|오늘|내일|이번|현재|지금|자세|설명|근거)", body.message)):
                    foreign = [OverseasCompany.model_validate(previous_task["company"])]
            if len(foreign) == 1:
                self.overseas.answer(task_id, owner, body, foreign[0], previous_task, progress)
                return
            if len(foreign) > 1:
                repo.put_task(task_id, owner, {"status": "completed", "message": "살펴볼 미국 상장기업을 선택해 주세요.",
                    "choices": [c.model_dump(mode="json", by_alias=True) for c in foreign], "originalMessage": body.message})
                return
            if not repo.has_companies():
                progress("회사 목록을 준비하고 있어요. 처음 한 번만 필요해요")
                repo.save_companies(self.research.require_dart().companies())
            # DART also lists private firms with short everyday-word names. A phrase like
            # "주요 위험요인" must not silently switch to an unrelated private company.
            companies = [company for company in repo.all_companies(listed_only=True)
                         if company.stock_code and company.stock_code != "000000"]
            candidates = ([repo.company(body.corp_code)] if body.corp_code
                          else resolve_companies(body.message, companies))
            candidates = [c for c in candidates if c]
            if not candidates and is_library_question(body.message):
                counts = repo.basic_analysis_counts()
                overseas_counts = self.overseas.sec.repo.counts()
                repo.put_task(task_id, owner, {"status": "completed", "message":
                    f"현재 기본 분석이 저장된 기업은 {counts['analyzedCompanies']:,}개이고, "
                    f"공시는 {counts['basicReports']:,}건이에요.\n"
                    f"미국 상장기업은 {overseas_counts['analyzedCompanies']:,}개, SEC 공시는 {overseas_counts['analyses']:,}건의 기본 분석이 저장돼 있어요.\n"
                    "저장된 기업만 분석할 수 있는 것은 아니에요. 다른 상장기업도 기업명이나 종목코드로 "
                    "요청하면 최근 3년의 DART 정기보고서를 찾아 새로 분석해요. "
                    "처음 분석할 때는 시간이 걸리고 API 사용료가 발생해요.\n"
                    "예: 브이원텍 공시 설명해줘 / 엔비디아 공시 설명해줘 / AAPL 주가 오를까?"})
                return
            suggestions = suggest_companies(body.message, companies) if not candidates else []
            if suggestions:
                repo.put_task(task_id, owner, {"status": "completed", "message":
                    "입력한 이름과 정확히 일치하는 기업을 찾지 못했어요. 혹시 아래 기업인가요? "
                    "선택하면 공시를 찾아 분석할게요.",
                    "choices": [c.model_dump(mode="json", by_alias=True) for c in suggestions],
                    "originalMessage": body.message})
                return
            previous = repo.document(body.rcept_no) if body.rcept_no else None
            previous_task = (repo.task(str(body.previous_task_id), owner)
                             if body.previous_task_id else None)
            if not candidates and previous_task and previous_task.get("company", {}).get("corpCode") and re.search(
                r"그럼|그러면|이\s*(?:종목|기업|회사)|주가|오를|오르|매수|매도|얼마|내일|이번|다음", body.message,
            ):
                candidates = [repo.company(previous_task["company"]["corpCode"])]
            if not candidates and previous:
                if re.search(r"어느|그|이 공시|위|매출|이익|위험|부채|현금|배당|투자|왜|얼마", body.message):
                    candidates = [repo.company(previous.disclosure.corp_code)]
            if len(candidates) != 1:
                repo.put_task(task_id, owner, {
                    "status": "completed", "message":
                    "분석할 기업을 찾지 못했어요. 기업명·국내 6자리 종목코드·미국 티커를 알려 주세요. "
                    "예: 삼성전자 / 엔비디아 / AAPL. 저장되지 않은 기업도 DART·SEC 정기보고서가 있으면 새로 분석해요."
                    if not candidates else "이름이 비슷한 기업이 있어요. 살펴볼 기업을 선택해 주세요.",
                    "choices": [c.model_dump(mode="json", by_alias=True) for c in candidates],
                    "originalMessage": body.message,
                })
                return
            company = candidates[0]
            intent = stock_intent(body.message)
            if (not intent and previous_task and previous_task.get("market")
                    and not re.search(r"공시|보고서|매출|이익|부채|현금|배당|실적", body.message)
                    and re.search(r"그럼|그러면|근거|왜|위험|리스크|개월|한\s*달|주일|내일|장기|중기|단기", body.message)):
                intent = "outlook"
            if intent:
                question = body.message
                if previous_task and previous_task.get("market") and previous_task.get(
                    "company", {}).get("corpCode") == company.corp_code:
                    question = f"이전 질문(대화 문맥): {previous_task.get('question', '')[:1000]}\n현재 질문: {body.message}"
                market = self.market.analyze(company, question, intent, owner, progress)
                repo.put_private_task(task_id, owner, {"status": "completed",
                    "company": company.model_dump(mode="json", by_alias=True),
                    "question": body.message, "market": market})
                return
            progress(f"{company.corp_name}의 공시를 찾고 있어요")
            same_context = (previous and previous.disclosure.corp_code == company.corp_code
                            and not re.search(r"최신|최근|20\d{2}|사업보고서|분기보고서|반기보고서", body.message))
            if same_context:
                disclosure = previous.disclosure
            else:
                today = datetime.now(ZoneInfo("Asia/Seoul")).date()
                if self.research.dart:
                    reports = regular_reports(self.research.dart, company.corp_code,
                                              years_ago(today), today)
                else:
                    reports = repo.stored_disclosures(company.corp_code)
                    if not reports:
                        raise ServiceError("DART_NOT_CONFIGURED", 503)
                year = re.search(r"20\d{2}", body.message)
                kind = next((k for k in ("사업보고서", "반기보고서", "분기보고서")
                             if k in body.message), None)
                if year:
                    reports = [r for r in reports if year.group() in r.report_name]
                if kind:
                    reports = [r for r in reports if kind in r.report_name]
                if not reports:
                    repo.put_task(task_id, owner, {"status": "completed", "message":
                        "최근 3년에서 조건에 맞는 정기보고서를 찾지 못했어요. 기업명과 보고서 연도를 확인해 주세요."})
                    return
                disclosure = next((r for r in reports if "철" not in r.remarks), reports[0])
            progress(f"{company.corp_name} · {disclosure.report_name} 원문을 준비하고 있어요")
            self.research.collect_original(disclosure)
            if is_overview(body.message):
                progress("기본 분석을 확인하고 있어요. 처음 보는 공시는 분석 후 저장해요")
                answer = self.research.basic_analyze(disclosure.rcept_no)
            else:
                progress("질문에 필요한 원문 근거를 찾고 있어요")
                self.research.prepare(disclosure.rcept_no, build_embeddings=True, disclosure=disclosure)
                question = body.message
                previous_task = (repo.task(str(body.previous_task_id), owner)
                                 if body.previous_task_id else None)
                if previous_task and previous_task.get("disclosure", {}).get("rceptNo") == disclosure.rcept_no:
                    previous_text = previous_task.get("question", "")
                    previous_answer = previous_task.get("result", {}).get("answer", "")[:1200]
                    question = (f"이전 질문: {previous_text}\n이전 답변(검증할 대화 문맥): {previous_answer}"
                                f"\n현재 질문: {body.message}")
                answer = self.research.ask(disclosure.rcept_no, question)
            repo.put_task(task_id, owner, {"status": "completed",
                "notice": ("저장된 공시를 사용했어요. ‘최신’은 저장된 자료 중 최신이며, "
                           "새 공시 조회에는 DART API 키가 필요해요.") if not self.research.dart else None,
                "company": company.model_dump(mode="json", by_alias=True),
                "disclosure": disclosure.model_dump(mode="json", by_alias=True),
                "question": body.message,
                "result": answer.model_dump(mode="json", by_alias=True)})
        except ServiceError as exc:
            repo.put_task(task_id, owner, {"status": "error", "code": exc.code,
                "message": ERRORS.get(exc.code, "공시를 처리하지 못했어요. 잠시 후 다시 질문해 주세요.")})
        except Exception:
            # Never serialize exception text: provider URLs can contain a DART credential.
            repo.put_task(task_id, owner, {"status": "error", "code": "CHAT_PROCESSING_FAILED",
                "message": "처리 중 문제가 생겼어요. 서버 상태를 확인한 뒤 다시 질문해 주세요."})
