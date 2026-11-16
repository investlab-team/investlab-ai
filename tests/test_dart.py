from datetime import date

import httpx
import pytest

from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.dart import DartClient, unpack_xml


def dart(handler):
    return DartClient("test-secret-never-return", client=httpx.Client(
        transport=httpx.MockTransport(handler),
    ))


def test_disclosures_preserve_corrections_and_paginate():
    def handler(request):
        assert request.url.params["last_reprt_at"] == "N"
        assert request.url.params["page_no"] == "2"
        return httpx.Response(200, json={
            "status": "000", "total_page": 3, "list": [{
                "rcept_no": "20260316000001", "corp_code": "00126380",
                "corp_name": "예시기업", "report_nm": "[기재정정]사업보고서",
                "rcept_dt": "20260316", "rm": "정",
            }],
        })
    result = dart(handler).disclosures("00126380", date(2026, 1, 1), date(2026, 12, 31), 2)
    assert result["totalPages"] == 3
    assert result["items"][0].filed_on == date(2026, 3, 16)
    assert result["items"][0].remarks == "정"


@pytest.mark.parametrize("status,code,http_status", [
    ("020", "DART_RATE_LIMITED", 429), ("010", "DART_AUTH_FAILED", 503),
    ("800", "DART_UNAVAILABLE", 503),
])
def test_provider_errors_are_sanitized(status, code, http_status):
    client = dart(lambda _: httpx.Response(200, json={"status": status, "message": "secret"}))
    with pytest.raises(ServiceError) as result:
        client.disclosures("00126380", date(2026, 1, 1), date(2026, 2, 1))
    assert result.value.code == code
    assert result.value.status == http_status
    assert "secret" not in str(result.value)


def test_binary_endpoint_xml_error_is_not_treated_as_zip():
    client = dart(lambda _: httpx.Response(200, content=b"<result><status>014</status></result>"))
    with pytest.raises(ServiceError, match="DART_NO_DOCUMENT"):
        client.document("20260316000001")


def test_empty_results_are_a_valid_empty_list():
    client = dart(lambda _: httpx.Response(200, json={"status": "013"}))
    assert client.disclosures("00126380", date(2026, 1, 1), date(2026, 2, 1))["items"] == []


def test_company_mapping_handles_nonlisted_stock_code(archive):
    payload = archive("""<result><list><corp_code>00126380</corp_code>
        <corp_name>예시기업</corp_name><stock_code> </stock_code></list></result>""")
    item = dart(lambda _: httpx.Response(200, content=payload)).companies()[0]
    assert item.stock_code is None
    assert len(item.company_id) == 36


def test_invalid_zip_rejected():
    with pytest.raises(ServiceError, match="DART_INVALID_ARCHIVE"):
        unpack_xml(b"not a zip")


def test_external_xml_entities_rejected():
    payload = b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><result>&a;</result>'
    with pytest.raises(ServiceError, match="DART_INVALID_RESPONSE"):
        dart(lambda _: httpx.Response(200, content=payload)).document("20260316000001")


def test_missing_api_file_uses_same_receipt_public_viewer_snapshot():
    receipt = "20260316000001"

    def handler(request):
        if request.url.path == "/api/document.xml":
            return httpx.Response(200, content=b"<result><status>014</status></result>")
        assert "crtfc_key" not in request.url.params
        if request.url.path == "/dsaf001/main.do":
            assert request.url.params["rcpNo"] == receipt
            return httpx.Response(200, text="""var node1 = {};
                node1['text'] = "사업의 내용"; node1['rcpNo'] = "20260316000001";
                node1['dcmNo'] = "123456"; node1['eleId'] = "3";
                node1['offset'] = "100"; node1['length'] = "2000";
                node1['dtd'] = "dart4.xsd";""")
        assert request.url.path == "/report/viewer.do"
        assert request.url.params["rcpNo"] == receipt
        assert request.url.params["dcmNo"] == "123456"
        return httpx.Response(200, text="<html><p>공시 원문 본문입니다.</p></html>")
    files = unpack_xml(dart(handler).document(receipt))
    assert len(files) == 1 and "사업의 내용" in files[0][0]


def test_viewer_fallback_never_substitutes_a_different_receipt():
    def handler(request):
        if request.url.path == "/api/document.xml":
            return httpx.Response(200, content=b"<result><status>014</status></result>")
        assert request.url.path == "/dsaf001/main.do"
        return httpx.Response(200, text="""var node1 = {};
            node1['rcpNo'] = "20260316000002"; node1['dcmNo'] = "123";
            node1['eleId'] = "1"; node1['offset'] = "1"; node1['length'] = "20";
            node1['dtd'] = "dart4.xsd";""")
    with pytest.raises(ServiceError, match="DART_NO_DOCUMENT"):
        dart(handler).document("20260316000001")
