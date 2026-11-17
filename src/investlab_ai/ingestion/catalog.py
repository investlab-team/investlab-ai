import re
from datetime import date

from investlab_ai.ingestion.dart import DartClient
from investlab_ai.schemas import Disclosure


def regular_reports(dart: DartClient, corp_code: str, start: date, end: date) -> list[Disclosure]:
    items = {}
    page = 1
    while True:
        result = dart.disclosures(corp_code, start, end, page, report_type="A", final_only=False)
        for item in result["items"]:
            if re.search(r"사업보고서|반기보고서|분기보고서", item.report_name):
                items[item.rcept_no] = item
        if page >= result["totalPages"]:
            break
        page += 1
    return sorted(items.values(), key=lambda item: (item.filed_on, item.rcept_no), reverse=True)


def years_ago(today: date, years: int = 3) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)
