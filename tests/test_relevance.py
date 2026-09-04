"""관련도 층 테스트 — 공통 기본값과 회사별 선택 설정을 분리한다."""

from pathlib import Path

import pytest
from tests.factories import make_announcement, make_company
from tests.test_normalization_bizinfo import REGION_TOKENS, full_bizinfo_item

from grant_radar.normalization.bizinfo import normalize_bizinfo_item
from grant_radar.relevance import RelevanceConfigError, RelevanceScorer, load_relevance_config

CONFIG_PATH = Path(__file__).parent.parent / "data" / "reference" / "relevance.json"
SCORER = RelevanceScorer(load_relevance_config(CONFIG_PATH))


def biz(**overrides):
    return normalize_bizinfo_item(full_bizinfo_item(**overrides), region_tokens=REGION_TOKENS)


def test_generic_startup_event_is_not_excluded_without_company_policy():
    result = SCORER.score(make_announcement(biz_pbanc_nm="스타트업 IR 경진대회"))
    assert result.tier != "C"


def test_company_can_exclude_ir_programs():
    company = make_company(exclude_keywords=["경진대회", "IR"])
    result = SCORER.score(make_announcement(biz_pbanc_nm="스타트업 IR 경진대회"), company=company)
    assert result.tier == "C"
    assert any("비관심 프로그램" in reason for reason in result.reasons)


def test_company_matching_keyword_is_tier_a():
    company = make_company(matching_keywords=["소프트웨어"])
    result = SCORER.score(biz(pblancNm="소프트웨어 기업 판로 지원"), company=company)
    assert result.tier == "A"


def test_company_interest_keywords_are_used():
    company = make_company(interest_keywords={"수출": ["해외진출", "바이어"]})
    result = SCORER.score(
        biz(pblancNm="해외진출 바이어 상담 지원", pldirSportRealmLclasCodeNm="수출"),
        company=company,
    )
    assert result.tier == "A"
    assert any("수출" in reason for reason in result.reasons)


def test_company_excluded_category_is_tier_c():
    company = make_company(exclude_categories=["창업"])
    result = SCORER.score(biz(pldirSportRealmLclasCodeNm="창업"), company=company)
    assert result.tier == "C"


def test_company_demote_keyword_caps_tier_b():
    company = make_company(demote_keywords=["의료기기"])
    result = SCORER.score(
        biz(pblancNm="소프트웨어 의료기기 고용 지원", pldirSportRealmLclasCodeNm="인력"),
        company=company,
    )
    assert result.tier == "B"


def test_employment_grant_is_tier_a_for_every_company():
    result = SCORER.score(
        biz(
            pblancNm="고용창출장려금 사업 공고",
            pldirSportRealmLclasCodeNm="인력",
            bsnsSumryCn="<p>근로자 채용 시 인건비 지원</p>",
        )
    )
    assert result.tier == "A"


def test_industry_flag_caps_tier_b():
    result = SCORER.score(
        biz(pblancNm="고용 우수기업 콘텐츠 분야 채용 지원", pldirSportRealmLclasCodeNm="인력"),
        industry_flagged=True,
    )
    assert result.tier == "B"


def test_category_rank_uses_generic_priority():
    assert SCORER.category_rank(biz(pldirSportRealmLclasCodeNm="금융")) < SCORER.category_rank(
        biz(pldirSportRealmLclasCodeNm="기타")
    )


def test_missing_config_raises_clear_error(tmp_path):
    with pytest.raises(RelevanceConfigError):
        load_relevance_config(tmp_path / "none.json")


HOME = make_company(headquarters_region="서울특별시", business_districts=["강남구"])


def test_other_district_is_hidden_only_when_company_location_known():
    announcement = biz(pblancNm="성동구 일자리사업", pldirSportRealmLclasCodeNm="인력")
    assert SCORER.score(announcement, company=HOME).hidden is True
    assert SCORER.score(announcement).hidden is False


def test_home_district_is_recorded():
    result = SCORER.score(
        biz(pblancNm="강남구 중소기업 융자지원", pldirSportRealmLclasCodeNm="금융"),
        company=HOME,
    )
    assert any("사업장 자치구" in reason for reason in result.reasons)
