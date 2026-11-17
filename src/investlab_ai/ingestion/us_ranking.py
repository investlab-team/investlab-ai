"""Timestamped public market-cap ranking, intersected with SEC US-exchange issuers."""
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from investlab_ai.errors import ServiceError
from investlab_ai.schemas import OverseasCompany

RANKING_URL = "https://companiesmarketcap.com/"


def us_top_companies(issuers: list[OverseasCompany], limit=100) -> dict:
    by_symbol = {alias.upper().replace(".", "-"): issuer for issuer in issuers
                 for alias in [issuer.stock_code, *issuer.aliases] if alias.isascii() and " " not in alias}
    found, seen_pages = {}, []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "InvestLabAI market-cap research"}) as client:
        for page in range(1, 5):
            url = RANKING_URL if page == 1 else RANKING_URL + f"?page={page}"
            response = client.get(url)
            if response.status_code != 200:
                raise ServiceError("US_RANKING_UNAVAILABLE", 503)
            seen_pages.append(url)
            soup = BeautifulSoup(response.text, "html.parser")
            for row in soup.select("table tbody tr"):
                code = row.select_one(".company-code")
                cells = row.find_all("td", recursive=False)
                if not code or len(cells) < 4:
                    continue
                symbol = code.get_text(strip=True).upper()
                issuer = by_symbol.get(symbol.replace(".", "-"))
                if not issuer or issuer.cik in found:
                    continue
                raw = cells[3].get("data-sort", "")
                if not str(raw).isdigit() or int(raw) <= 0:
                    raise ServiceError("US_MARKET_CAP_INVALID", 502)
                # One company per CIK; preserve the ranked share-class symbol.
                canonical = next((a for a in [issuer.stock_code, *issuer.aliases]
                                  if a.upper().replace(".", "-") == symbol.replace(".", "-")), issuer.stock_code)
                found[issuer.cik] = {"issuer": issuer.model_copy(update={"stock_code": canonical}).model_dump(mode="json", by_alias=True),
                                     "marketCapUsd": str(raw), "rankingSymbol": symbol}
            if len(found) >= limit:
                break
    ranked = sorted(found.values(), key=lambda r: int(r["marketCapUsd"]), reverse=True)
    if len(ranked) < limit:
        raise ServiceError("US_RANKING_INCOMPLETE", 502)
    return {"fetchedAt": datetime.now(UTC).isoformat(), "sources": seen_pages,
            "basis": "Public company-level USD market cap; SEC NYSE/Nasdaq/AMEX issuers including ADR; one CIK per company; no ETFs",
            "companies": [dict(item, rank=i+1) for i, item in enumerate(ranked[:limit])]}
