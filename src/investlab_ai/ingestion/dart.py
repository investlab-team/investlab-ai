import io
import json
import re
import zipfile
from datetime import date
from uuid import NAMESPACE_URL, uuid5

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from investlab_ai.errors import ServiceError
from investlab_ai.schemas import Company, Disclosure

MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024


def unpack_xml(data: bytes) -> list[tuple[str, bytes]]:
    """Read in memory, never extract untrusted paths onto the filesystem."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ServiceError("DART_INVALID_ARCHIVE")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 500 or sum(item.file_size for item in files) > MAX_EXPANDED:
                raise ServiceError("DART_ARCHIVE_TOO_LARGE", 413)
            result = [
                (item.filename, archive.read(item))
                for item in files
                if item.filename.lower().endswith((".xml", ".html", ".htm"))
            ]
            if not result:
                raise ServiceError("DART_EMPTY_ARCHIVE")
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise ServiceError("DART_INVALID_ARCHIVE") from exc


class DartClient:
    def __init__(self, key: str, timeout: float = 60, client: httpx.Client | None = None):
        self.key = key
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _check(payload: dict) -> None:
        status = payload.get("status")
        if status == "000":
            return
        code, http_status = {
            "013": ("DART_NO_DATA", 404),
            "014": ("DART_NO_DOCUMENT", 404),
            "020": ("DART_RATE_LIMITED", 429),
            "010": ("DART_AUTH_FAILED", 503),
            "011": ("DART_AUTH_FAILED", 503),
            "012": ("DART_AUTH_FAILED", 503),
            "800": ("DART_UNAVAILABLE", 503),
        }.get(status, ("DART_INVALID_RESPONSE", 502))
        raise ServiceError(code, http_status)

    def _get(self, path: str, params: dict | None = None, binary: bool = False):
        try:
            with self.client.stream(
                "GET", f"https://opendart.fss.or.kr/api/{path}",
                params={"crtfc_key": self.key, **(params or {})},
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > MAX_DOWNLOAD:
                        raise ServiceError("DART_RESPONSE_TOO_LARGE", 413)
            if binary:
                if not zipfile.is_zipfile(io.BytesIO(data)):
                    root = ElementTree.fromstring(bytes(data))
                    self._check({"status": root.findtext("status")})
                    raise ServiceError("DART_INVALID_ARCHIVE")
                return bytes(data)
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ServiceError("DART_INVALID_RESPONSE")
            self._check(payload)
            return payload
        except httpx.TimeoutException as exc:
            raise ServiceError("DART_TIMEOUT", 504) from exc
        except httpx.HTTPError as exc:
            raise ServiceError("DART_UNAVAILABLE", 502) from exc
        except ServiceError:
            raise
        except (ValueError, ElementTree.ParseError, DefusedXmlException) as exc:
            raise ServiceError("DART_INVALID_RESPONSE") from exc

    def companies(self) -> list[Company]:
        entries = unpack_xml(self._get("corpCode.xml", binary=True))
        try:
            root = ElementTree.fromstring(entries[0][1])
            return [
                Company(
                    company_id=str(uuid5(NAMESPACE_URL, f"opendart:company:{item.findtext('corp_code')}")),
                    corp_code=item.findtext("corp_code"),
                    corp_name=item.findtext("corp_name"),
                    stock_code=(item.findtext("stock_code") or "").strip() or None,
                )
                for item in root.findall("list")
            ]
        except (ValueError, ElementTree.ParseError, DefusedXmlException) as exc:
            raise ServiceError("DART_INVALID_COMPANIES") from exc

    def disclosures(
        self, corp_code: str, start: date, end: date, page: int = 1,
        report_type: str = "A", final_only: bool = False,
    ) -> dict:
        try:
            payload = self._get("list.json", {
                "corp_code": corp_code, "bgn_de": start.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"), "page_no": page, "page_count": 100,
                "pblntf_ty": report_type, "last_reprt_at": "Y" if final_only else "N",
            })
        except ServiceError as exc:
            if exc.code == "DART_NO_DATA":
                return {"items": [], "page": page, "totalPages": 0}
            raise
        try:
            return {
                "items": [self.to_disclosure(item) for item in payload["list"]],
                "page": page, "totalPages": int(payload["total_page"]),
            }
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceError("DART_INVALID_RESPONSE") from exc

    @staticmethod
    def to_disclosure(item: dict) -> Disclosure:
        receipt = item["rcept_no"]
        return Disclosure(
            rcept_no=receipt, corp_code=item["corp_code"], corp_name=item["corp_name"],
            report_name=item["report_nm"],
            filed_on=date.fromisoformat(item["rcept_dt"]),
            source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            remarks=item.get("rm", ""),
        )

    def lookup(self, receipt: str) -> Disclosure:
        try:
            filed_on = date.fromisoformat(receipt[:8])
        except ValueError as exc:
            raise ServiceError("INVALID_RECEIPT_DATE", 422) from exc
        # Do not trust client-supplied company/title metadata.
        page = 1
        while page <= 100:
            result = self.disclosures("", filed_on, filed_on, page, report_type="")
            for item in result["items"]:
                if item.rcept_no == receipt:
                    return item
            if page >= result["totalPages"]:
                break
            page += 1
        raise ServiceError("DISCLOSURE_NOT_FOUND", 404)

    def document(self, receipt: str) -> bytes:
        try:
            return self._get("document.xml", {"rcept_no": receipt}, binary=True)
        except ServiceError as exc:
            if exc.code != "DART_NO_DOCUMENT":
                raise
            return self._viewer_archive(receipt)

    def _public_page(self, path: str, params: dict) -> bytes:
        try:
            with self.client.stream("GET", "https://dart.fss.or.kr" + path, params=params) as response:
                response.raise_for_status()
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > MAX_DOWNLOAD:
                        raise ServiceError("DART_RESPONSE_TOO_LARGE", 413)
                return bytes(data)
        except httpx.TimeoutException as exc:
            raise ServiceError("DART_TIMEOUT", 504) from exc
        except httpx.HTTPError as exc:
            raise ServiceError("DART_UNAVAILABLE", 502) from exc

    def _viewer_archive(self, receipt: str) -> bytes:
        """Public viewer fallback, pinned to the exact receipt and document IDs.

        Do not execute the viewer's JavaScript or follow arbitrary links. Only bounded,
        numeric document references from the same receipt are sent to the fixed host.
        """
        main = self._public_page("/dsaf001/main.do", {"rcpNo": receipt})
        try:
            text = main.decode("utf-8")
        except UnicodeError as exc:
            raise ServiceError("DART_NO_DOCUMENT", 404) from exc
        sections = []
        for block in re.split(r"var node1\s*=\s*\{\};", text)[1:]:
            block = block.split("var node2")[0]
            values = dict(re.findall(
                r'''node1\['(text|rcpNo|dcmNo|eleId|offset|length|dtd)'\]\s*=\s*"([^"]*)"''', block,
            ))
            if values.get("rcpNo") != receipt:
                continue
            if not all(re.fullmatch(r"\d{1,14}", values.get(key, ""))
                       for key in ("dcmNo", "eleId", "offset", "length")):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", values.get("dtd", "")):
                continue
            sections.append(values)
        if not sections or len(sections) > 100:
            raise ServiceError("DART_NO_DOCUMENT", 404)
        output, total = io.BytesIO(), 0
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, section in enumerate(sections):
                params = {key: value for key, value in section.items() if key != "text"}
                content = self._public_page("/report/viewer.do", params)
                total += len(content)
                if total > MAX_EXPANDED:
                    raise ServiceError("DART_ARCHIVE_TOO_LARGE", 413)
                title = re.sub(r"[^가-힣A-Za-z0-9 _.-]", "", section.get("text", "본문"))[:70]
                archive.writestr(f"{index:03d}_{title}.html", content)
            archive.writestr("viewer-manifest.json", json.dumps({
                "rceptNo": receipt, "source": "DART public document viewer",
                "mainUrl": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                "sections": sections,
            }, ensure_ascii=False))
        return output.getvalue()
