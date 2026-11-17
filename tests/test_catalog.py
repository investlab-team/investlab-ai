from datetime import date

import pytest

from investlab_ai.agent.chat import (
    is_library_question,
    is_overview,
    resolve_companies,
    suggest_companies,
)
from investlab_ai.ingestion.catalog import years_ago
from investlab_ai.schemas import Company


def test_company_resolution_alias_and_ambiguous_name():
    companies = [Company(company_id="a", corp_code="00126380", corp_name="삼성전자", stock_code="005930"),
                 Company(company_id="b", corp_code="00123456", corp_name="삼성물산", stock_code="028260")]
    assert resolve_companies("삼전 최신 공시 설명해줘", companies)[0].corp_name == "삼성전자"
    assert len(resolve_companies("삼성 공시 알려줘", companies)) == 2
    assert resolve_companies("알 수 없는 기업 공시", companies) == []


def test_summary_vs_followup_and_three_year_boundary():
    assert is_overview("삼성전자 최신 공시 설명해줘")
    assert not is_overview("그럼 매출 감소 원인은 뭐야?")
    assert years_ago(date(2026, 11, 14)) == date(2023, 11, 14)
    assert years_ago(date(2024, 2, 29)) == date(2021, 2, 28)


def test_short_latin_name_is_not_matched_inside_an_english_word():
    company = Company(company_id="sk", corp_code="00123456", corp_name="SK", stock_code="034730")
    assert resolve_companies("Explain the risk", [company]) == []
    assert resolve_companies("SK 공시 설명해줘", [company]) == [company]


@pytest.mark.parametrize("message", ["브이원택", "브이원택 공시좀 분석해줘", "브이원어텍 공시", "브원텍 공시"])
def test_misspelled_company_is_suggested_but_not_automatically_selected(message):
    company = Company(company_id="v", corp_code="01027794", corp_name="브이원텍", stock_code="251630")
    assert resolve_companies(message, [company]) == []
    assert suggest_companies(message, [company]) == [company]
    assert suggest_companies("그럼 매출 감소의 원인은?", [company]) == []
    assert suggest_companies("251631", [company]) == []


@pytest.mark.parametrize("message", ["분석할수 있는기업이 몇개냐고", "지금너가 분석할수 있는 기업은 몇개야",
                                     "저장된 공시는 몇 건이야?", "어떤 기업 분석 가능해?"])
def test_library_questions_are_understood_without_a_company_name(message):
    assert is_library_question(message)
    assert not is_library_question("브이원텍 공시좀 분석해줘")
