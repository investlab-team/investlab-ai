"""Personal read-only connection check: never prints credentials or raw brokerage data."""
from pathlib import Path

from dotenv import load_dotenv

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.market import indicators
from investlab_ai.providers.toss import TossClient


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)
    load_dotenv(root / ".env.toss", override=True)
    client = None
    try:
        client = TossClient(Settings())
        quote = client.quote("005930")
        metrics = indicators(client.candles("005930"))
        print("토스 인증·현재가·일봉·지표 계산 확인 완료")
        print("종목코드 일치:", quote.symbol == "005930", "완료 일봉:", metrics["observations"])
        print("키·시세 원문은 출력하지 않았으며 주문·계좌·GPT 호출은 하지 않았습니다.")
        return 0
    except ServiceError as exc:
        print("토스 연결 확인 필요:", exc.code)
        return 1
    except Exception as exc:
        print("연결 확인 실패:", type(exc).__name__)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
