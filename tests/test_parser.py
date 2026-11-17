import pytest

from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.parser import parse_document, split_text, tokens


def test_sections_tables_units_and_stable_ids(archive):
    raw = archive("""<DOCUMENT><SECTION-1><TITLE>재무에 관한 사항</TITLE>
    <P>매출 현황</P><P>(단위: 백만원)</P><TABLE>
    <TR><TD rowspan="2">구분</TD><TD>당기</TD><TD>전기</TD></TR>
    <TR><TD>매출액</TD><TD>100</TD><TD>90</TD></TR>
    </TABLE><P>해외 수요가 증가했습니다.</P></SECTION-1></DOCUMENT>""")
    chunks = parse_document(raw, "20260316000001", 150)
    assert any("재무에 관한 사항" in chunk.section for chunk in chunks)
    table = next(chunk.text for chunk in chunks if "[표:" in chunk.text)
    assert "백만원" in table and "rowspan=2" in table
    assert "매출액 | 100 | 90" in table
    assert [c.chunk_id for c in chunks] == [
        c.chunk_id for c in parse_document(raw, "20260316000001", 150)
    ]


def test_unicode_and_numbers_survive_chunk_boundaries():
    text = "대한민국 사업보고서 매출액 123,456,789원. " * 150
    chunks = split_text(text, 100)
    assert "".join(chunks) == text
    assert all(tokens(chunk) <= 100 and "�" not in chunk for chunk in chunks)


def test_large_table_repeats_unit_and_header(archive):
    rows = "".join(f"<tr><td>사업부{i}</td><td>{i}</td></tr>" for i in range(100))
    raw = archive("<h1>매출</h1><p>단위: 억원</p><table>"
                  "<tr><th>사업부</th><th>매출액</th></tr>" + rows + "</table>")
    chunks = parse_document(raw, "20260316000001", 100)
    table_chunks = [chunk for chunk in chunks if "[표:" in chunk.text]
    assert len(table_chunks) > 1
    assert all("단위: 억원" in c.text and "사업부 | 매출액" in c.text for c in table_chunks)


def test_no_supported_text_is_explicit_error(archive):
    with pytest.raises(ServiceError, match="DOCUMENT_HAS_NO_SUPPORTED_TEXT"):
        parse_document(archive("<document><img src='x'/></document>"), "20260316000001", 100)


def test_long_paragraph_does_not_displace_table_header(archive):
    raw = archive("<h1>실적</h1><p>" + "사업 현황 설명. " * 100 + "</p>"
                  "<p>(단위: 백만원)</p><table><tr><th>계정</th><th>당기</th></tr>"
                  "<tr><td>매출액</td><td>1234</td></tr></table>")
    table = [c.text for c in parse_document(raw, "20260316000001", 150) if "[표:" in c.text]
    assert table and all("계정 | 당기" in text and "백만원" in text for text in table)


def test_empty_layout_table_rows_do_not_break_real_report_parsing(archive):
    raw = archive("<h1>사업 개요</h1><table><tr><td></td></tr></table>"
                  "<p>매출액은 100억원입니다.</p>")
    result = parse_document(raw, "20260316000001", 100)
    assert any("100억원" in chunk.text for chunk in result)
