"""Fast, bounded excerpts for a basic overview; the original archive stays intact."""
import hashlib
import re

from lxml import etree

from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.dart import unpack_xml
from investlab_ai.ingestion.parser import normalize, split_text, tokens
from investlab_ai.schemas import Evidence

GROUPS = [r"사업의 내용|사업.*개요|주요.*제품|매출.*수주",
          r"재무.*사항|요약.*재무|손익|현금흐름|영업이익",
          r"위험|소송|우발|불확실|감사.*의견|유동성|채무",
          r"연구개발|투자|배당|주주|정정|제재"]


def overview_evidence(raw: bytes, receipt: str, budget: int) -> list[Evidence]:
    blocks = []
    for filename, data in sorted(unpack_xml(raw)):
        root = etree.HTML(data, parser=etree.HTMLParser(encoding="utf-8", no_network=True))
        if root is None:
            continue
        for unwanted in root.xpath("//script|//style|//noscript"):
            unwanted.getparent().remove(unwanted)
        section, recent = filename, []
        for element in root.iter():
            tag = element.tag
            if tag not in {"title", "h1", "h2", "h3", "h4", "p", "table"}:
                continue
            if any(parent.tag in {"table", "p", "title"} for parent in element.iterancestors()):
                continue
            text = normalize(" ".join(element.itertext()))
            if tag in {"title", "h1", "h2", "h3", "h4"}:
                if text:
                    section = f"{filename} / {text}"
                recent = []
                continue
            if tag == "table":
                rows = []
                for row in element.iter("tr"):
                    if next(row.iterancestors("table"), None) is not element:
                        continue
                    cells = []
                    for cell in row:
                        if cell.tag not in {"td", "th", "te", "tu"}:
                            continue
                        value = normalize(" ".join(cell.itertext()))
                        spans = " ".join(f"{key}={cell.get(key)}" for key in ("rowspan", "colspan")
                                         if cell.get(key, "1") != "1")
                        cells.append(value + (f" [{spans}]" if spans else ""))
                    if any(cells):
                        rows.append(" | ".join(cells))
                if not rows:
                    continue
                chosen = rows[:5]
                for row in rows[5:]:
                    if re.search(r"매출|영업이익|순이익|자산총계|부채총계|현금흐름|위험|당기", row):
                        chosen.append(row)
                    if len(chosen) >= 20:
                        break
                context = [p for p in recent if "단위" in p or len(p) < 120]
                text = "\n".join(["[표 발췌: 모든 행을 포함하지 않음]", *context, *chosen])
            elif text:
                recent = [*recent[-1:], text]
            if text:
                # Each candidate is a contiguous paragraph, or explicitly selected table rows.
                blocks.append((section, text[:8000]))
    if not blocks:
        raise ServiceError("DOCUMENT_HAS_NO_SUPPORTED_TEXT", 422)
    buckets = []
    for pattern in GROUPS:
        ranked = sorted(range(len(blocks)), key=lambda index: (
            -len(re.findall(pattern, blocks[index][0] + " " + blocks[index][1])), index,
        ))
        buckets.append([i for i in ranked if re.search(pattern, blocks[i][0] + " " + blocks[i][1])])
    selected, positions = [], [0] * len(buckets)
    while len(selected) < 40:
        before = len(selected)
        for index, bucket in enumerate(buckets):
            while positions[index] < len(bucket):
                candidate = bucket[positions[index]]
                positions[index] += 1
                if candidate not in selected:
                    selected.append(candidate)
                    break
        if len(selected) == before:
            break
    if not selected:
        selected = list(range(min(len(blocks), 20)))
    evidence, used = [], 0
    for index in selected:
        section, text = blocks[index]
        # Cap an individual block so one long table cannot displace the other categories.
        text = split_text(text, min(700, budget // 8))[0]
        cost = tokens(text) + 150
        if used + cost > budget:
            continue
        identity = hashlib.sha256(f"overview:{receipt}:{index}:{section}:{text}".encode()).hexdigest()[:32]
        evidence.append(Evidence(chunk_id=identity, section=section, text=text,
                                 source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"))
        used += cost
    return evidence
