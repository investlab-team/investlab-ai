from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import Session

from investlab_ai.agent.chat import ChatService, is_overview
from investlab_ai.agent.market_service import MarketService
from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.market import Candle, Quote, horizon, indicators, stock_intent
from investlab_ai.providers.toss import TossClient
from investlab_ai.schemas import ChatRequest, Claim, Company, Draft
from investlab_ai.storage import Repository, TaskRow

TOKEN = "market-test-token-only-123456789012345"
COMPANY = Company(company_id="test-company", corp_code="00126380", corp_name="삼성전자", stock_code="005930")


def bars(count=300, flat=False):
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return [Candle(timestamp=today - timedelta(days=count - i), openPrice=str(100 if flat else 100 + i),
                   highPrice=str(101 if flat else 101 + i), lowPrice=str(99 if flat else 99 + i),
                   closePrice=str(100 if flat else 100 + i), volume="1000", currency="KRW")
            for i in range(count)]


@pytest.mark.parametrize(('message', 'intent'), [
    ("삼성전자 주가는 오를까?", "outlook"), ("삼성전자 내일 오를까", "outlook"),
    ("삼성전자 현재 주가 얼마야?", "quote"), ("삼성전자 차트 분석해줘", "outlook"),
    ("삼성전자 최신 공시 설명해줘", None), ("매출이 오를까?", None),
    ("주식회사 삼성전자 공시 설명", None), ("삼성전자 매출 증가 원인", None),
])
def test_market_routing(message, intent):
    assert stock_intent(message) == intent
    if intent:
        assert not is_overview(message)


def test_indicators_exclude_today_and_have_known_values():
    rows = bars(80)
    today = Candle(timestamp=datetime.now(UTC), openPrice="99999", highPrice="99999", lowPrice="99999",
                   closePrice="99999", volume="999999", currency="KRW")
    result = indicators([*rows, today])
    assert result['observations'] == 80
    assert result['sma20'] == pytest.approx(sum(range(160, 180)) / 20)
    assert result['return20SessionsPct'] == pytest.approx((179 / 159 - 1) * 100)
    assert result['rsi14Wilder'] == 100
    assert result['lastCompletedVolumeVsPrior20Mean'] == 1
    assert indicators(bars(80, flat=True))['rsi14Wilder'] == 50
    with pytest.raises(ServiceError, match="MARKET_HISTORY_INSUFFICIENT"):
        indicators(rows[:30])
    assert horizon('내일 오를까') == '다음 1거래일'
    assert horizon('한 달 동안') == '향후 1개월'


def config():
    return Settings(service_token=TOKEN, toss_client_id="fake-id", toss_client_secret="fake-secret")


def token_response():
    return httpx.Response(200, json={'access_token': 'fake-token', 'token_type': 'Bearer', 'expires_in': 86400})


def test_token_and_price_cache_shared_across_client_instances():
    settings, calls = config(), []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/oauth2/token':
            assert b'grant_type=client_credentials' in request.content
            return token_response()
        assert request.headers['Authorization'] == 'Bearer fake-token'
        return httpx.Response(200, json={'result': [{'symbol': '005930', 'timestamp': datetime.now(UTC).isoformat(),
                                                   'lastPrice': '70000', 'currency': 'KRW'}]})
    for _ in range(2):
        client = TossClient(settings, httpx.Client(transport=httpx.MockTransport(respond)))
        assert client.quote('005930').last_price == Decimal('70000')
        assert client._token() == 'fake-token'
        client.close()
    assert calls == ['/oauth2/token', '/api/v1/prices']


def test_candle_pagination_uses_cursor_and_deduplicates_boundary():
    rows, pages = list(reversed(bars(300))), []

    def respond(request):
        if request.url.path == '/oauth2/token':
            return token_response()
        assert request.url.params['adjusted'] == 'true' and request.url.params['interval'] == '1d'
        before = request.url.params.get('before')
        pages.append(before)
        selected = rows[:200] if not before else rows[199:]
        return httpx.Response(200, json={'result': {
            'candles': [r.model_dump(mode='json', by_alias=True) for r in selected],
            'nextBefore': selected[-1].timestamp.isoformat() if not before else None}})
    client = TossClient(config(), httpx.Client(transport=httpx.MockTransport(respond)))
    result = client.candles('005930')
    assert len(result) == len({r.timestamp for r in result}) == 261
    assert result[-1].timestamp == rows[0].timestamp
    assert pages == [None, rows[199].timestamp.isoformat()]
    assert len(client.candles('005930')) == 261 and len(pages) == 2
    client.close()


def test_retry_after_does_not_hammer_or_return_stale_quote():
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return token_response() if request.url.path == '/oauth2/token' else httpx.Response(429, headers={'Retry-After': '30'})
    client = TossClient(config(), httpx.Client(transport=httpx.MockTransport(respond)))
    for _ in range(2):
        with pytest.raises(ServiceError, match='TOSS_RATE_LIMITED'):
            client.quote('005930')
    assert calls.count('/api/v1/prices') == 1 and client.data.get('quote:005930') is None
    client.close()


def test_masked_secrets_rejected_before_request():
    with pytest.raises(ServiceError, match='TOSS_CREDENTIAL_MASKED'):
        TossClient(Settings(service_token=TOKEN, toss_client_id="tsck_fake", toss_client_secret="tssk_****"))


@pytest.mark.parametrize('price', ['NaN', '-1', '0', '1e999'])
def test_invalid_price_never_reaches_the_model(price):
    def respond(request):
        if request.url.path == '/oauth2/token':
            return token_response()
        return httpx.Response(200, json={'result': [{'symbol': '005930', 'timestamp': None,
                                                   'lastPrice': price, 'currency': 'KRW'}]})
    client = TossClient(config(), httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ServiceError, match='TOSS_INVALID_RESPONSE'):
        client.quote('005930')
    assert client.data.get('quote:005930') is None
    client.close()


class MarketProvider:
    def __init__(self):
        self.calls = 0

    def generate_market(self, question, evidence):
        self.calls += 1
        return Draft(status="answered", claims=[Claim(category="summary",
                     text="가격 흐름은 상승 추세지만 이후 방향은 조건에 따라 달라집니다.",
                     evidence_ids=[evidence[1].chunk_id])])


class FakeToss:
    def __init__(self):
        self.value = Quote(symbol='005930', timestamp=datetime.now(UTC), lastPrice='70000', currency='KRW')

    def quote(self, symbol):
        return self.value

    def candles(self, symbol):
        return bars(80)

    def close(self):
        pass


def test_outlook_cache_changes_with_price_and_owner(tmp_path):
    cfg = Settings(service_token=TOKEN, local_sqlite_path=tmp_path/'db.sqlite', openai_model="fake-model")
    repo = Repository(cfg)
    repo.initialize_local()
    provider, toss = MarketProvider(), FakeToss()
    service = MarketService(ResearchService(cfg, repo, None, provider), toss)
    assert service.analyze(COMPANY, '주가 오를까', 'outlook', 'owner')['modelCalls'] == 1
    assert service.analyze(COMPANY, '주가 오를까', 'outlook', 'owner')['cached']
    toss.value = toss.value.model_copy(update={'last_price': Decimal('71000')})
    assert not service.analyze(COMPANY, '주가 오를까', 'outlook', 'owner')['cached']
    service.analyze(COMPANY, '주가 오를까', 'outlook', 'other-owner')
    assert provider.calls == 3
    assert service.analyze(COMPANY, '현재 주가 얼마', 'quote', 'owner')['modelCalls'] == 0
    assert provider.calls == 3
    repo.close()


def test_private_market_results_survive_restart_without_shared_values(tmp_path):
    cfg = Settings(service_token=TOKEN, local_sqlite_path=tmp_path/'db.sqlite')
    repo = Repository(cfg)
    repo.initialize_local()
    data = {'status': 'completed', 'market': {'quote': {'lastPrice': '70000'}}}
    repo.put_private_task('task-1', 'owner', data)
    with Session(repo.engine) as session:
        public = session.get(TaskRow, 'task-1').data
        assert 'market' not in public and '70000' not in str(public)
    repo.close()
    repo = Repository(cfg)
    assert repo.task('task-1', 'owner') == data and repo.task('task-1', 'stranger') is None
    repo.close()


def test_price_question_does_not_fall_back_to_basic_report(tmp_path):
    cfg = Settings(service_token=TOKEN, local_sqlite_path=tmp_path/'db.sqlite')
    repo = Repository(cfg)
    repo.initialize_local()
    repo.save_companies([COMPANY])
    chat = ChatService(ResearchService(cfg, repo, None, None))
    chat._run('test-task', 'owner', ChatRequest(message='삼성전자 주가 오를까?'))
    assert repo.task('test-task', 'owner')['code'] == 'TOSS_NOT_CONFIGURED'
    assert repo.counts() == {'documents': 0, 'analyses': 0}
    chat.close()
    repo.close()
