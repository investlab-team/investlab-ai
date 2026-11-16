from investlab_ai.ingestion.overview import overview_evidence
from investlab_ai.ingestion.parser import tokens


def test_overview_preserves_business_risk_table_units_and_budget(archive):
    raw = archive("<h1>사업의 내용</h1><p>주요 제품은 반도체입니다.</p>"
                  "<h1>재무에 관한 사항</h1><p>(단위: 억원)</p>"
                  "<table><tr><th>계정</th><th>당기</th></tr>"
                  "<tr><td>매출액</td><td>100</td></tr></table>"
                  "<h1>위험요인</h1><p>환율 위험이 있습니다.</p>")
    evidence = overview_evidence(raw, "20260316000001", 2000)
    text = "\n".join(e.text for e in evidence)
    assert "반도체" in text and "환율 위험" in text and "억원" in text and "매출액" in text
    assert sum(tokens(e.text) + 150 for e in evidence) <= 2000
    assert all("rcpNo=20260316000001" in e.source_url for e in evidence)
