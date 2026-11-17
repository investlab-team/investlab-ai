import hashlib
import re
import warnings
from dataclasses import dataclass

import tiktoken
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning

from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.dart import unpack_xml


@dataclass
class ParsedChunk:
    chunk_id: str
    section: str
    text: str
    token_count: int


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> int:
    return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))


def split_text(text: str, budget: int) -> list[str]:
    """Split on Unicode character boundaries, never in the bytes of a Korean character."""
    if not text:
        return []
    if tokens(text) <= budget:
        return [text]
    parts = []
    while text:
        low, high = 1, min(len(text), budget * 8)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if tokens(text[:middle]) <= budget:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ServiceError("CHUNK_TOKEN_BUDGET_TOO_SMALL", 422)
        parts.append(text[:best])
        text = text[best:]
    return parts


def parse_document(data: bytes, receipt: str, chunk_tokens: int) -> list[ParsedChunk]:
    """Preserve section names, table cells, row/col spans, and nearby unit labels."""
    chunks: list[ParsedChunk] = []

    def emit(section: str, text: str) -> None:
        for content in split_text(text, chunk_tokens):
            identity = f"{receipt}:{len(chunks)}:{section}:{content}"
            chunks.append(ParsedChunk(
                hashlib.sha256(identity.encode()).hexdigest()[:32], section,
                content, tokens(content),
            ))

    def flush(section: str, pending: list[str]) -> None:
        if pending:
            emit(section, "\n".join(pending))
            pending.clear()

    for filename, raw in sorted(unpack_xml(data)):
        # DART specifies UTF-8; tolerate a declared Korean legacy encoding in old files.
        declaration = re.search(br'encoding=["\']([\w-]+)', raw[:200], re.IGNORECASE)
        codec = declaration.group(1).decode("ascii") if declaration else "utf-8"
        try:
            markup = raw.decode(codec)
        except (UnicodeError, LookupError) as exc:
            raise ServiceError("DOCUMENT_ENCODING_UNSUPPORTED", 422) from exc
        # DART's XML contains HTML-like custom table elements. html.parser keeps them
        # without resolving external entities, and is deliberately used for both formats.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(markup, "lxml")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        section = filename
        recent: list[str] = []
        pending: list[str] = []

        for node in soup.find_all(["title", "h1", "h2", "h3", "h4", "p", "table"]):
            if node.find_parent("table") or node.find_parent(["p", "title"]):
                continue
            if node.name in {"title", "h1", "h2", "h3", "h4"}:
                flush(section, pending)
                heading = normalize(node.get_text(" ", strip=True))
                if heading:
                    section = f"{filename} / {heading}"
                recent.clear()
                continue
            if node.name == "table":
                flush(section, pending)
                rows = []
                for row in node.find_all("tr"):
                    if row.find_parent("table") is not node:
                        continue
                    cells = []
                    for cell in row.find_all(["th", "td", "te", "tu"], recursive=False):
                        if not isinstance(cell, Tag):
                            continue
                        span = " ".join(
                            f"{key}={cell[key]}" for key in ("rowspan", "colspan")
                            if str(cell.get(key, "1")) != "1"
                        )
                        cells.append(normalize(cell.get_text(" ", strip=True)) +
                                     (f" [{span}]" if span else ""))
                    if cells and any(cell.strip() for cell in cells):
                        rows.append(" | ".join(cells))
                if rows:
                    # Keep the header even when an earlier paragraph is very long.
                    context = []
                    for paragraph in recent[-2:]:
                        units = re.findall(r"\([^)]*단위[^)]*\)", paragraph)
                        context.extend(units or [split_text(paragraph, max(8, chunk_tokens // 8))[0]])
                    header = split_text(rows[0], max(15, chunk_tokens // 3))[0]
                    prefix = "\n".join([header, "[표: 병합 셀은 span으로 표시]", *context])
                    if tokens(prefix) > chunk_tokens * 3 // 4:
                        # Header comes first so bounded context cannot displace it.
                        prefix = split_text(prefix, chunk_tokens * 3 // 4)[0]
                    batch = prefix
                    for row in rows[1:]:
                        if tokens(prefix + "\n" + row) > chunk_tokens:
                            emit(section, batch)
                            # Repeating header/unit context for every fragment, including long rows.
                            width = max(1, chunk_tokens - tokens(prefix) - 8)
                            for part in split_text(row, width):
                                emit(section, prefix + "\n" + part)
                            batch = prefix
                        elif tokens(batch + "\n" + row) > chunk_tokens:
                            emit(section, batch)
                            batch = prefix + "\n" + row
                        else:
                            batch += "\n" + row
                    if batch != prefix or len(rows) == 1:
                        emit(section, batch)
                continue
            text = normalize(node.get_text(" ", strip=True))
            if not text:
                continue
            if tokens("\n".join([*pending, text])) > chunk_tokens:
                flush(section, pending)
            pending.append(text)
            recent = [*recent[-1:], text]
        flush(section, pending)
    if not chunks:
        raise ServiceError("DOCUMENT_HAS_NO_SUPPORTED_TEXT", 422)
    return chunks
