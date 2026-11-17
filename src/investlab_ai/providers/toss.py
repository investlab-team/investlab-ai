"""Read-only Toss Open API 1.2.28 adapter. No account or order endpoints."""
import hashlib
import math
import random
import re
import time
from datetime import UTC, datetime, timedelta

import httpx
from filelock import Timeout
from pydantic import ValidationError

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.market import Candle, Quote
from investlab_ai.private_cache import PrivateCache

BASE = "https://openapi.tossinvest.com"


class TossClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.toss_client_id or not settings.toss_client_secret:
            raise ServiceError("TOSS_NOT_CONFIGURED", 503)
        self.settings = settings
        self.client_id = settings.toss_client_id.get_secret_value().strip()
        self.client_secret = settings.toss_client_secret.get_secret_value().strip()
        if not self.client_id or not self.client_secret:
            raise ServiceError("TOSS_NOT_CONFIGURED", 503)
        if "*" in self.client_id or "*" in self.client_secret:
            raise ServiceError("TOSS_CREDENTIAL_MASKED", 503)
        identity = hashlib.sha256((self.client_id + ":" + self.client_secret).encode()).hexdigest()
        root = settings.private_cache_dir / "toss"
        # Tokens/rate state have a separate cache, never evicted by market symbol traffic.
        self.auth = PrivateCache(root, "auth:" + identity, max_entries=8)
        self.data = PrivateCache(root, "data:" + identity)
        self.client = client or httpx.Client(timeout=20, trust_env=False, follow_redirects=False)

    def close(self):
        self.client.close()

    @staticmethod
    def _seconds(value, default: float) -> float:
        try:
            number = float(value)
            return min(3600, max(0, number)) if math.isfinite(number) else default
        except (ValueError, TypeError):
            return default

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(3):
            try:
                with self.auth.lock("network"):
                    state = self.auth.get("rate") or {}
                    delay = max(0, state.get("next", 0) - time.time())
                    if delay > 5:
                        raise ServiceError("TOSS_RATE_LIMITED", 429)
                    time.sleep(delay)
                    try:
                        response = self.client.request(method, BASE + path, **kwargs)
                    except httpx.TimeoutException:
                        raise ServiceError("TOSS_TIMEOUT", 504) from None
                    except httpx.HTTPError:
                        raise ServiceError("TOSS_UNAVAILABLE", 502) from None
                    limit = max(1, self._seconds(response.headers.get("X-RateLimit-Limit"),
                                                  self.settings.toss_requests_per_second))
                    interval = 1 / min(limit, self.settings.toss_requests_per_second)
                    if response.headers.get("X-RateLimit-Remaining") == "0":
                        interval = max(interval, self._seconds(
                            response.headers.get("X-RateLimit-Reset"), 1))
                    if response.status_code == 429:
                        interval = max(interval, 2**attempt + random.uniform(0, 0.2), self._seconds(
                            response.headers.get("Retry-After"), 2**attempt))
                    self.auth.put("rate", {"next": time.time() + interval}, 7200)
            except Timeout:
                raise ServiceError("TOSS_BUSY", 503) from None
            if response.status_code == 429:
                if attempt == 2 or interval > 5:
                    raise ServiceError("TOSS_RATE_LIMITED", 429)
                continue
            if response.status_code == 403:
                raise ServiceError("TOSS_IP_NOT_ALLOWED", 403)
            if response.status_code == 401:
                # Do not blindly reissue: a second process/PC may have revoked this token.
                raise ServiceError("TOSS_AUTH_FAILED", 401)
            if response.status_code == 404:
                raise ServiceError("TOSS_SYMBOL_NOT_FOUND", 404)
            if response.status_code >= 500:
                raise ServiceError("TOSS_UNAVAILABLE", 502)
            if response.status_code != 200:
                raise ServiceError("TOSS_REQUEST_FAILED", 502)
            return response
        raise ServiceError("TOSS_RATE_LIMITED", 429)

    def _token(self) -> str:
        try:
            with self.auth.lock("token"):
                cached = self.auth.get("token")
                if cached:
                    return cached
                response = self._request("POST", "/oauth2/token", data={
                    "grant_type": "client_credentials", "client_id": self.client_id,
                    "client_secret": self.client_secret,
                })
                value = response.json()
                duration = int(value["expires_in"])
                token = value["access_token"]
                if not isinstance(token, str) or not token or duration <= 0 or value["token_type"] != "Bearer":
                    raise ValueError("invalid token response")
                self.auth.put("token", token, max(1, duration - min(60, duration / 10)))
                return token
        except Timeout:
            raise ServiceError("TOSS_BUSY", 503) from None
        except (KeyError, ValueError, TypeError):
            raise ServiceError("TOSS_INVALID_RESPONSE", 502) from None

    def _get(self, path: str, params: dict):
        response = self._request("GET", path, params=params,
                                 headers={"Authorization": "Bearer " + self._token()})
        try:
            return response.json()["result"]
        except (ValueError, KeyError, TypeError):
            raise ServiceError("TOSS_INVALID_RESPONSE", 502) from None

    @staticmethod
    def _symbol(symbol: str):
        if not re.fullmatch(r"(?:[0-9][0-9A-Z]{5}|[A-Z][A-Z0-9.\-]{0,14})", symbol):
            raise ServiceError("TOSS_SYMBOL_NOT_FOUND", 422)

    @staticmethod
    def _currency(symbol: str) -> str:
        return "KRW" if re.fullmatch(r"[0-9][0-9A-Z]{5}", symbol) else "USD"

    def us_catalog(self) -> list[dict]:
        """Broker-sourced names stay in the per-credential local cache."""
        key = "us-listed-stocks-v2"
        try:
            with self.data.lock(key):
                cached = self.data.get(key)
                if cached is not None:
                    return cached
                rows = []
                for market in ("NYSE", "NASDAQ", "AMEX"):
                    page = self._get("/api/v1/stocks/all", {"market": market})
                    if not isinstance(page, list):
                        raise ServiceError("TOSS_INVALID_RESPONSE", 502)
                    # This endpoint intentionally returns compact symbol/name records.
                    rows.extend(r for r in page if isinstance(r, dict) and isinstance(r.get("name"), str)
                                and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", str(r.get("symbol", ""))))
                self.data.put(key, rows, 86400)
                return rows
        except Timeout:
            raise ServiceError("TOSS_BUSY", 503) from None

    def quote(self, symbol: str) -> Quote:
        symbol = self.canonical_symbol(symbol)
        key = "quote:" + symbol
        try:
            with self.data.lock(key):
                value = self.data.get(key)
                if value is None:
                    rows = self._get("/api/v1/prices", {"symbols": symbol})
                    if not isinstance(rows, list) or len(rows) != 1:
                        raise ValueError("invalid quote list")
                    quote = Quote.model_validate(rows[0])
                    if quote.currency != self._currency(symbol) or quote.symbol != symbol or (quote.timestamp and quote.timestamp > datetime.now(UTC)
                                                   + timedelta(minutes=5)):
                        raise ValueError("invalid quote identity/time")
                    value = quote.model_dump(mode="json", by_alias=True)
                    self.data.put(key, value, self.settings.toss_quote_cache_seconds)
                return Quote.model_validate(value)
        except Timeout:
            raise ServiceError("TOSS_BUSY", 503) from None
        except (ValueError, TypeError, ValidationError):
            raise ServiceError("TOSS_INVALID_RESPONSE", 502) from None

    def candles(self, symbol: str) -> list[Candle]:
        symbol = self.canonical_symbol(symbol)
        key = "daily-adjusted:" + symbol
        try:
            with self.data.lock(key):
                value = self.data.get(key)
                if value is None:
                    rows, before = {}, None
                    for _ in range(3):
                        params = {"symbol": symbol, "interval": "1d", "count": 200, "adjusted": "true"}
                        if before:
                            params["before"] = before
                        page = self._get("/api/v1/candles", params)
                        for item in page["candles"]:
                            candle = Candle.model_validate(item)
                            if candle.currency != self._currency(symbol):
                                raise ValueError("unexpected candle currency")
                            if candle.timestamp > datetime.now(UTC):
                                raise ValueError("future candle")
                            rows.setdefault(candle.timestamp, candle)
                        next_before = page.get("nextBefore")
                        if len(rows) >= 261 or not next_before or next_before == before:
                            break
                        before = next_before
                    parsed = sorted(rows.values(), key=lambda row: row.timestamp)[-261:]
                    value = [row.model_dump(mode="json", by_alias=True) for row in parsed]
                    self.data.put(key, value, self.settings.toss_candle_cache_seconds)
                return [Candle.model_validate(row) for row in value]
        except Timeout:
            raise ServiceError("TOSS_BUSY", 503) from None
        except (ValueError, TypeError, KeyError, ValidationError):
            raise ServiceError("TOSS_INVALID_RESPONSE", 502) from None

    def canonical_symbol(self, symbol: str) -> str:
        self._symbol(symbol)
        if "." in symbol or "-" in symbol:
            # SEC uses BRK-B, while Toss's listed share-class symbol is BRK.B.
            matching = {r["symbol"] for r in self.us_catalog()
                        if r["symbol"].replace(".", "-") == symbol.replace(".", "-")}
            if len(matching) == 1:
                return next(iter(matching))
        return symbol
