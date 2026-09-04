"""업종·분야 규칙 (industry.v1) 테스트.

키워드 감지는 자동 제외(FAIL)를 내지 않는다 — REVIEW/PASS/NOT_APPLICABLE만.
"""

from pathlib import Path

import pytest
from tests.factories import make_announcement, make_company

from grant_radar.models.decision import RuleStatus
from grant_radar.rules.industry import (
    IndustryKeywordError,
    IndustryRule,
    load_industry_keywords,
)

KEYWORDS_PATH = Path(__file__).parent.parent / "data" / "reference" / "industry_keywords.json"


def make_rule() -> IndustryRule:
    return IndustryRule(load_industry_keywords(KEYWORDS_PATH))


def evaluate(**overrides):
    return make_rule().evaluate(make_announcement(**overrides), make_company())


def test_no_sector_signal_is_not_applicable():
    # 기본 픽스처 제목·대상에는 업종 키워드가 없다
    result = evaluate()
    assert result.status == RuleStatus.NOT_APPLICABLE
    assert result.human_checks  # 본문 확인 안내는 항상 남긴다


def test_restricted_sector_in_title_reviews():
    result = evaluate(biz_pbanc_nm="2026 뷰티 스타트업 오디션")
    assert result.status == RuleStatus.REVIEW
    assert "뷰티" in (result.announcement_value or "")
    assert "뷰티" in result.reason


def test_restricted_sector_in_target_text_reviews():
    result = evaluate(aply_trgt_ctnt="핀테크 서비스를 보유한 창업기업")
    assert result.status == RuleStatus.REVIEW
    assert "핀테크" in result.reason


def test_company_sector_passes():
    result = evaluate(biz_pbanc_nm="소프트웨어기업 판로개척 지원사업")
    assert result.status == RuleStatus.PASS
    assert "소프트웨어" in (result.announcement_value or "")


def test_company_sector_wins_over_restricted_with_check():
    # 놓침 방지: 회사 업종 신호가 있으면 다른 분야 신호가 있어도 PASS + 확인 요청
    result = evaluate(biz_pbanc_nm="AI 소프트웨어 기업 지원")
    assert result.status == RuleStatus.PASS
    assert any("함께 감지" in check for check in result.human_checks)


def test_latin_keyword_requires_word_boundary():
    # "MAIN" 안의 AI, "SUMMIT" 안의 IT는 오탐하지 않는다
    result = evaluate(biz_pbanc_nm="MAIN STREET SUMMIT 참여기업 모집")
    assert result.status == RuleStatus.NOT_APPLICABLE

    result = evaluate(biz_pbanc_nm="AI 융합 지원사업")
    assert result.status == RuleStatus.REVIEW


def test_target_restriction_keyword_reviews():
    result = evaluate(biz_pbanc_nm="여성 기업인 성장 지원")
    assert result.status == RuleStatus.REVIEW
    assert "여성" in (result.announcement_value or "")


def test_no_text_is_not_applicable_with_reason():
    result = evaluate(biz_pbanc_nm=None, aply_trgt_ctnt=None)
    assert result.status == RuleStatus.NOT_APPLICABLE
    assert "텍스트가 없어" in result.reason


def test_never_fails():
    # FAIL 금지 원칙의 단순 회귀 방어 — 대표적 제한 키워드에서도 REVIEW까지만
    for title in ("바이오 전용", "콘텐츠 전용", "여성 전용", "AI 전용"):
        result = evaluate(biz_pbanc_nm=title)
        assert result.status in (RuleStatus.REVIEW, RuleStatus.PASS)


def test_missing_keywords_file_raises_clear_error(tmp_path):
    with pytest.raises(IndustryKeywordError):
        load_industry_keywords(tmp_path / "none.json")
