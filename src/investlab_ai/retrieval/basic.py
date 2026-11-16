import re

from investlab_ai.schemas import Evidence


def select_basic_evidence(
    rows: list[tuple[Evidence, list[float], int]], budget: int,
) -> list[Evidence]:
    """Bounded, diverse excerpts for a basic overview, explicitly not exhaustive analysis."""
    groups = {
        "business": r"사업의 내용|사업.*개요|주요.*제품|매출.*수주|사업부문",
        "performance": r"재무.*사항|요약.*재무|손익계산|현금흐름|경영.*분석",
        "risk": r"위험|소송|우발|불확실|감사.*의견|계속기업|채무|담보",
        "other": r"연구개발|투자|배당|주주|정정|제재",
    }
    selected: dict[str, Evidence] = {}
    used = 0
    buckets = []
    for pattern in groups.values():
        ranked = sorted(rows, key=lambda row: (
            -len(re.findall(pattern, row[0].section + " " + row[0].text)),
            row[0].chunk_id,
        ))
        buckets.append([row for row in ranked if re.search(
            pattern, row[0].section + " " + row[0].text,
        )])
    # Round-robin prevents a long financial section from consuming the whole context.
    offsets = [0] * len(buckets)
    progress = True
    while progress:
        progress = False
        for index, bucket in enumerate(buckets):
            while offsets[index] < len(bucket):
                evidence, _, count = bucket[offsets[index]]
                offsets[index] += 1
                if evidence.chunk_id in selected or used + count + 150 > budget:
                    continue
                selected[evidence.chunk_id] = evidence
                used += count + 150
                progress = True
                break
    # Sample throughout sections even if headings don't match a known template.
    order = sorted(range(len(rows)), key=lambda i: (i % 7, i))
    for index in order:
        evidence, _, count = rows[index]
        if evidence.chunk_id not in selected and used + count + 150 <= budget:
            selected[evidence.chunk_id] = evidence
            used += count + 150
    return list(selected.values())
