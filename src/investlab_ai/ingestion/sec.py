"""SEC EDGAR fair-access client and reproducible HTML evidence extraction."""
import hashlib
import json
import re
import time
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx
from bs4 import BeautifulSoup
from filelock import Timeout
from lxml import etree

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.parser import ParsedChunk, normalize, split_text, tokens
from investlab_ai.private_cache import PrivateCache
from investlab_ai.schemas import Evidence, OverseasCompany, SecFiling

SEC_PARSER_VERSION = "sec-html-v1"
REGULAR_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "40-F/A"}
KOREAN_NAMES = {
    "AAPL": ["애플"], "NVDA": ["엔비디아", "엔비디아주식"], "MSFT": ["마이크로소프트", "마소"],
    "GOOG": ["구글", "알파벳"], "GOOGL": ["구글", "알파벳"], "AMZN": ["아마존"],
    "TSLA": ["테슬라"], "META": ["메타", "페이스북"], "AVGO": ["브로드컴"],
    "TSM": ["TSMC", "티에스엠씨", "대만반도체"], "ASML": ["에이에스엠엘"],
    "AMD": ["에이엠디"], "INTC": ["인텔"], "MU": ["마이크론"], "NFLX": ["넷플릭스"],
    "PLTR": ["팔란티어"], "ORCL": ["오라클"], "WMT": ["월마트"], "COST": ["코스트코"],
    "KO": ["코카콜라"], "PEP": ["펩시"], "LLY": ["일라이릴리", "릴리"],
    "BRK-B": ["버크셔해서웨이", "버크셔"], "BRK-A": ["버크셔해서웨이", "버크셔"],
    "V": ["비자"], "MA": ["마스터카드"], "JPM": ["제이피모건", "JP모건"],
    "NVO": ["노보노디스크"], "ARM": ["암홀딩스"], "SPCX": ["스페이스엑스"],
}


def issuer_id(cik: str) -> str:
    return str(uuid5(NAMESPACE_URL, "sec:issuer:" + cik))


class SecClient:
    def __init__(self, config: Settings, client: httpx.Client | None = None):
        agent = config.sec_user_agent or ""
        if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", agent) or any(
            s in agent for s in ("YOUR_CONTACT", "example.com", "\n", "\r")):
            raise ServiceError("SEC_CONTACT_REQUIRED", 503)
        self.config = config
        self.cache = PrivateCache(config.private_cache_dir / "sec", "public-edgar-http", 512)
        self.client = client or httpx.Client(
            headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
            timeout=45, follow_redirects=False, trust_env=False,
        )

    def close(self):
        self.client.close()

    def get(self, url: str, *, cache_seconds=0) -> bytes:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host not in {"www.sec.gov", "data.sec.gov"} or parsed.port not in (None, 443):
            raise ServiceError("SEC_INVALID_URL", 422)
        key = "http:" + url
        try:
            if cache_seconds and (cached := self.cache.get(key)) is not None:
                return cached.encode()
            for attempt in range(3):
                with self.cache.lock("rate"):
                    next_at = self.cache.get("rate") or 0
                    wait = max(0, next_at - time.time())
                    if wait > 15:
                        raise ServiceError("SEC_RATE_LIMITED", 429)
                    time.sleep(wait)
                    self.cache.put("rate", time.time() + 1 / self.config.sec_requests_per_second, 3600)
                    try:
                        with self.client.stream("GET", url) as response:
                            status = response.status_code
                            if status == 429:
                                delay = min(300, max(2**attempt, float(response.headers.get("Retry-After", 2))))
                                self.cache.put("rate", time.time() + delay, 3600)
                                continue
                            if status == 403:
                                raise ServiceError("SEC_ACCESS_DENIED", 403)
                            if status == 404:
                                raise ServiceError("SEC_DOCUMENT_NOT_FOUND", 404)
                            if status != 200:
                                raise ServiceError("SEC_UNAVAILABLE", 502)
                            pieces, size = [], 0
                            for piece in response.iter_bytes():
                                size += len(piece)
                                if size > 80 * 1024 * 1024:
                                    raise ServiceError("SEC_DOCUMENT_TOO_LARGE", 413)
                                pieces.append(piece)
                            data = b"".join(pieces)
                    except httpx.TimeoutException:
                        raise ServiceError("SEC_TIMEOUT", 504) from None
                    except httpx.HTTPError:
                        raise ServiceError("SEC_UNAVAILABLE", 502) from None
                if cache_seconds:
                    self.cache.put(key, data.decode("utf-8"), cache_seconds)
                return data
            raise ServiceError("SEC_RATE_LIMITED", 429)
        except Timeout:
            raise ServiceError("SEC_BUSY", 503) from None

    def json(self, path: str, seconds=900):
        try:
            return json.loads(self.get(path, cache_seconds=seconds))
        except (ValueError, UnicodeError):
            raise ServiceError("SEC_INVALID_RESPONSE", 502) from None

    def companies(self) -> list[OverseasCompany]:
        value = self.json("https://www.sec.gov/files/company_tickers_exchange.json", 86400)
        keys = value["fields"]
        found = {}
        for values in value["data"]:
            item = dict(zip(keys, values, strict=True))
            if str(item.get("exchange", "")).upper() not in {"NYSE", "NASDAQ", "AMEX"}:
                continue
            cik, symbol = str(item["cik"]).zfill(10), item["ticker"].upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol):
                continue
            if cik not in found:
                found[cik] = OverseasCompany(company_id=issuer_id(cik), cik=cik,
                                             corp_name=item["name"], stock_code=symbol)
            found[cik].aliases.extend([symbol, item["name"], *KOREAN_NAMES.get(symbol, [])])
        return list(found.values())

    def filings(self, issuer: OverseasCompany, start: date, end: date) -> list[SecFiling]:
        if not issuer.cik:
            raise ServiceError("SEC_COMPANY_NOT_FOUND", 404)
        main = self.json(f"https://data.sec.gov/submissions/CIK{issuer.cik}.json")
        groups = [main["filings"]["recent"]]
        for page in main["filings"].get("files", []):
            if page.get("filingTo", "") < start.isoformat() or page.get("filingFrom", "9999") > end.isoformat():
                continue
            filename = page["name"]
            if not re.fullmatch(r"CIK\d+-submissions-\d+\.json", filename):
                raise ServiceError("SEC_INVALID_RESPONSE", 502)
            groups.append(self.json("https://data.sec.gov/submissions/" + filename, 86400))
        found = {}
        for group in groups:
            for i, form in enumerate(group.get("form", [])):
                if form not in REGULAR_FORMS:
                    continue
                filed = date.fromisoformat(group["filingDate"][i])
                if not start <= filed <= end:
                    continue
                accession = group["accessionNumber"][i]
                primary = group["primaryDocument"][i]
                if PurePosixPath(primary).name != primary or ".." in primary:
                    raise ServiceError("SEC_INVALID_RESPONSE", 502)
                stamp = (group.get("acceptanceDateTime") or [None] * len(group["form"]))[i]
                report = (group.get("reportDate") or [None] * len(group["form"]))[i]
                filing = SecFiling(
                    accession=accession, cik=issuer.cik, symbol=issuer.stock_code,
                    issuer_name=issuer.corp_name, form=form, filed_on=filed,
                    accepted_at=datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None,
                    report_date=date.fromisoformat(report) if report else None,
                    primary_document=primary,
                    source_url=f"https://www.sec.gov/Archives/edgar/data/{int(issuer.cik)}/"
                               f"{accession.replace('-', '')}/{quote(primary, safe='-._')}",
                )
                if filing.accepted_at and filing.accepted_at > datetime.now(UTC):
                    continue
                found.setdefault(accession, filing)
        return sorted(found.values(), key=lambda f: (f.filed_on, f.accession), reverse=True)

    def document(self, filing: SecFiling) -> bytes:
        if not filing.primary_document.lower().endswith((".htm", ".html")):
            raise ServiceError("SEC_DOCUMENT_FORMAT_UNSUPPORTED", 422)
        raw = self.get(filing.source_url)
        if b"<html" not in raw[:10000].lower() and b"<body" not in raw[:10000].lower():
            raise ServiceError("SEC_INVALID_DOCUMENT", 502)
        return raw

    def document_parts(self, filing: SecFiling) -> list[dict]:
        parts = [{"name": filing.primary_document, "sourceUrl": filing.source_url,
                  "raw": self.document(filing)}]
        # Canadian 40-Fs often incorporate the actual annual financials/MD&A as EX-99 HTML.
        if filing.form.startswith("40-F"):
            prefix = filing.source_url.rsplit("/", 1)[0] + "/"
            index = self.get(prefix + filing.accession + "-index.html")
            soup = BeautifulSoup(index, "html.parser")
            for row in soup.select("table.tableFile tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 4 or not cells[3].get_text(strip=True).upper().startswith(("EX-99", "EX-13")):
                    continue
                link = cells[2].find("a", href=True)
                if not link:
                    continue
                path = link["href"].split("?")[0]
                filename = PurePosixPath(path).name
                if (not filename.lower().endswith((".htm", ".html")) or ".." in filename
                        or filename == filing.primary_document):
                    continue
                if len(parts) >= 7:
                    break
                url = prefix + quote(filename, safe="-._")
                parts.append({"name": filename, "sourceUrl": url, "raw": self.get(url)})
        if sum(len(p["raw"]) for p in parts) > 80 * 1024 * 1024:
            raise ServiceError("SEC_DOCUMENT_TOO_LARGE", 413)
        return parts


def sec_chunks(raw: bytes, accession: str, budget=750) -> list[ParsedChunk]:
    root = etree.HTML(raw, parser=etree.HTMLParser(no_network=True, encoding="utf-8"))
    if root is None:
        raise ServiceError("SEC_INVALID_DOCUMENT", 422)
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        tag = str(node.tag).lower()
        style = (node.get("style") or "").lower().replace(" ", "")
        if (tag in {"script", "style", "noscript", "ix:header"} or "display:none" in style
                or "visibility:hidden" in style):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
    blocks = []

    def walk(node):
        if not isinstance(node.tag, str):
            return
        tag = str(node.tag).lower()
        if tag == "table" and not node.xpath(".//table"):
            rows = []
            for row in node.iter("tr"):
                cells = []
                for cell in row:
                    if str(cell.tag).lower() in {"td", "th"}:
                        value = normalize(" ".join(cell.itertext()))
                        span = " ".join(f"{k}={cell.get(k)}" for k in ("rowspan", "colspan") if cell.get(k, "1") != "1")
                        cells.append(value + (f" [{span}]" if span else ""))
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append(("table", "\n".join(rows)))
            return
        if tag in {"p", "h1", "h2", "h3", "h4"} and not node.xpath(".//table"):
            value = normalize(" ".join(node.itertext()))
            if value:
                blocks.append((tag, value))
            return
        if node.text and normalize(node.text):
            blocks.append((tag, normalize(node.text)))
        for child in node:
            walk(child)
            if child.tail and normalize(child.tail):
                blocks.append((tag, normalize(child.tail)))
    walk(root)
    section, pending, chunks, recent_context = "SEC filing", [], [], []

    def emit(content):
        for piece in split_text(content, budget):
            digest = hashlib.sha256(f"{SEC_PARSER_VERSION}:{accession}:{len(chunks)}:{section}:{piece}".encode()).hexdigest()[:32]
            chunks.append(ParsedChunk(digest, section, piece, tokens(piece)))
    for tag, value in blocks:
        heading = len(value) < 220 and (tag.startswith("h") or re.match(
            r"^(?:PART\s+[IVX]+|ITEM\s+\d+[A-Z]?\b|RISK FACTORS|MANAGEMENT.S DISCUSSION|CONSOLIDATED STATEMENTS)", value, re.I))
        if heading:
            if pending:
                emit(" ".join(pending))
                pending = []
            section = value
        elif tag == "table":
            if pending:
                emit(" ".join(pending))
                pending = []
            context = "\n".join(recent_context[-3:])
            emit("[Table; preserve currency/unit/period and merged-cell context]\n" + context + "\n" + value)
        else:
            if len(value) < 500 and re.search(r"in millions|in thousands|per share|unaudited|months ended|years ended|dollars", value, re.I):
                recent_context.append(value)
            pending.append(value)
            if len(" ".join(pending)) >= 1800:
                emit(" ".join(pending))
                pending = []
    if pending:
        emit(" ".join(pending))
    if not chunks:
        raise ServiceError("SEC_NO_SUPPORTED_TEXT", 422)
    return chunks


def select_sec_evidence(records: list[dict], filing: SecFiling, budget: int) -> list[Evidence]:
    patterns = [r"business|overview|products|services", r"management|operations|revenue|net sales|income|cash flows|balance sheet",
                r"risk factors|litigation|uncertainty|liquidity|competition", r"repurchase|dividend|research|outlook|capital"]
    buckets = [sorted(range(len(records)), key=lambda i: (
        -bool(re.search(pattern, records[i]["section"], re.I)),
        -len(re.findall(pattern, records[i]["text"], re.I)), i)) for pattern in patterns]
    selected, seen, used = [], set(), 0
    for offset in range(min(30, len(records))):
        for bucket in buckets:
            i = bucket[offset]
            if i in seen:
                continue
            row = records[i]
            cost = row["token_count"] + 180
            if used + cost > budget:
                continue
            seen.add(i)
            used += cost
            selected.append(Evidence(chunk_id=row["chunk_id"], section=f"{filing.form} · {filing.filed_on} · {row['section']}",
                                     text=row["text"], source_url=row.get("source_url", filing.source_url)))
        if len(selected) >= 24:
            break
    return selected
