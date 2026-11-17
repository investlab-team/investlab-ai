from datetime import UTC, datetime

import httpx

from investlab_ai.errors import ServiceError
from investlab_ai.schemas import Company

RANKING_SOURCE = "https://stock.naver.com/market/stock/kr/stocklist/marketValue"


def top_companies(companies: list[Company], limit: int = 100) -> dict:
    """Rank ordinary shares only, KOSPI+KOSDAQ, and map to DART corporate identities."""
    mapping = {company.stock_code: company for company in companies if company.stock_code}
    found = {}
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for market in ("KOSPI", "KOSDAQ"):
            # 200 per market covers preferred shares and unmapped/non-company securities.
            for page in (1, 2):
                try:
                    response = client.get(
                        f"https://m.stock.naver.com/api/stocks/marketValue/{market}",
                        params={"page": page, "pageSize": 100},
                    )
                    response.raise_for_status()
                    entries = response.json()["stocks"]
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    raise ServiceError("MARKET_RANKING_UNAVAILABLE", 503) from exc
                for item in entries:
                    code = item.get("itemCode", "")
                    if (code not in mapping or not code.endswith("0")
                            or item.get("stockEndType") != "stock"):
                        continue
                    company = mapping[code]
                    raw = item.get("marketValueRaw")
                    if raw is None or not str(raw).isdigit():
                        raise ServiceError("MARKET_CAP_UNIT_UNKNOWN", 502)
                    found[company.corp_code] = {
                        "company": company.model_dump(mode="json", by_alias=True),
                        "market": market, "marketCapKrw": str(raw),
                        "quoteAt": item.get("localTradedAt"),
                    }
    ranked = sorted(found.values(), key=lambda item: int(item["marketCapKrw"]), reverse=True)
    if len(ranked) < limit:
        raise ServiceError("MARKET_RANKING_INCOMPLETE", 502)
    return {"source": RANKING_SOURCE, "fetchedAt": datetime.now(UTC).isoformat(),
            "basis": "KOSPI+KOSDAQ ordinary-share market capitalization; one DART company each",
            "companies": [dict(item, rank=index + 1) for index, item in enumerate(ranked[:limit])]}
