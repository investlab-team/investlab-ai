"""Validated KRW/USD quotes and Python-computed indicators."""
import math
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from investlab_ai.errors import ServiceError

KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")


class Quote(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    symbol: str
    timestamp: AwareDatetime | None = None
    last_price: Decimal = Field(alias="lastPrice", gt=0, allow_inf_nan=False, max_digits=28, decimal_places=8)
    currency: Literal["KRW", "USD"]


class Candle(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    timestamp: AwareDatetime
    open: Decimal = Field(alias="openPrice", gt=0, allow_inf_nan=False)
    high: Decimal = Field(alias="highPrice", gt=0, allow_inf_nan=False)
    low: Decimal = Field(alias="lowPrice", gt=0, allow_inf_nan=False)
    close: Decimal = Field(alias="closePrice", gt=0, allow_inf_nan=False)
    volume: Decimal = Field(ge=0, allow_inf_nan=False, max_digits=28, decimal_places=8)
    currency: Literal["KRW", "USD"]

    @model_validator(mode="after")
    def valid_range(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLC range")
        return self


def stock_intent(message: str) -> Literal["quote", "outlook"] | None:
    compact = re.sub(r"\s", "", message).replace("주식회사", "")
    market = bool(re.search(r"주가|주식|현재가|차트|이동평균|RSI|변동성|거래량|매수|매도|살까|팔까", compact, re.I))
    direction = bool(re.search(r"오를|오르|올라|올랐|상승|떨어|하락|전망|예측|살까|팔까|사도|매수|매도", compact))
    # A report's revenue/expense change is a disclosure question unless price is explicit.
    if not market and re.search(r"공시|보고서|매출|이익|부채|현금|배당|사업|실적", compact):
        return None
    if market and re.search(r"얼마|몇원|현재가|현재주가|지금주가", compact) and not direction:
        return "quote"
    return "outlook" if market or direction else None


def horizon(message: str) -> str:
    if re.search(r"오늘|내일|당일", message):
        return "다음 1거래일"
    if re.search(r"일주일|1\s*주|이번\s*주|다음\s*주", message):
        return "향후 1주"
    if re.search(r"6\s*개월|반년|중장기", message):
        return "향후 6개월"
    if re.search(r"3\s*개월|분기|중기", message):
        return "향후 3개월"
    if re.search(r"1\s*년|일년|장기", message):
        return "향후 1년"
    if re.search(r"1\s*개월|한\s*달|1\s*달|단기", message):
        return "향후 1개월"
    return "향후 1개월 (기간 미지정 시 기본값)"


def indicators(candles: list[Candle], now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    currencies = {c.currency for c in candles}
    if len(currencies) != 1:
        raise ServiceError("TOSS_INVALID_RESPONSE", 502)
    currency = next(iter(currencies))
    zone = NEW_YORK if currency == "USD" else KST
    # Conservatively omit the current market-local date, including extended-hours bars.
    rows = sorted((c for c in candles if c.timestamp.astimezone(zone).date()
                   < now.astimezone(zone).date()), key=lambda c: c.timestamp)
    if len({c.timestamp for c in rows}) != len(rows) or len(rows) < 61:
        raise ServiceError("MARKET_HISTORY_INSUFFICIENT", 422)
    close = np.array([float(c.close) for c in rows])
    if not np.isfinite(close).all() or (close <= 0).any():
        raise ServiceError("TOSS_INVALID_RESPONSE", 502)
    change = np.diff(close)
    up, down = np.maximum(change, 0), np.maximum(-change, 0)
    avg_up, avg_down = float(up[:14].mean()), float(down[:14].mean())
    for gain, loss in zip(up[14:], down[14:], strict=True):
        avg_up, avg_down = (avg_up * 13 + gain) / 14, (avg_down * 13 + loss) / 14
    rsi = (50.0 if avg_up == avg_down == 0 else 100.0 if avg_down == 0
           else 100 - 100 / (1 + avg_up / avg_down))
    volume_average = sum(float(c.volume) for c in rows[-21:-1]) / 20
    result = {
        "completedThrough": rows[-1].timestamp.isoformat(), "observations": len(rows),
        "currency": currency, "marketTimeZone": str(zone),
        "completedSessionDate": rows[-1].timestamp.astimezone(zone).date().isoformat(),
        "timestampMeaning": "daily bar label, not an exchange closing timestamp",
        "adjustedPrices": True, "lastCompletedClose": float(close[-1]),
        "sma20": float(close[-20:].mean()), "sma60": float(close[-60:].mean()),
        "return20SessionsPct": float((close[-1] / close[-21] - 1) * 100),
        "return60SessionsPct": float((close[-1] / close[-61] - 1) * 100),
        "rsi14Wilder": rsi,
        "annualizedVolatility20SessionsPct": float(np.std(np.diff(np.log(close[-21:])), ddof=1)
                                                   * math.sqrt(252) * 100),
        "lastCompletedVolumeVsPrior20Mean": (float(rows[-1].volume) / volume_average
                                             if volume_average else None),
    }
    if any(isinstance(v, float) and not math.isfinite(v) for v in result.values()):
        raise ServiceError("TOSS_INVALID_RESPONSE", 502)
    return result
