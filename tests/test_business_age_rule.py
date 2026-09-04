"""업력 규칙 테스트 (지시서 20.4절).

기본 가상 공고의 접수기간: 20260701 ~ 20260731.
"""

from datetime import date

from grant_radar.models.decision import Confidence, RuleStatus
from grant_radar.rules.business_age import BusinessAgeRule, add_years

from tests.factories import make_announcement, make_company

RULE = BusinessAgeRule()


def evaluate(conditions, established, **announcement_overrides):
    announcement = make_announcement(biz_enyy=conditions, **announcement_overrides)
    company = make_company(established_date=established)
    return RULE.evaluate(announcement, company)


class TestAddYears:
    def test_exact_date_arithmetic(self):
        assert add_years(date(2022, 3, 15), 3) == date(2025, 3, 15)

    def test_leap_day_becomes_march_first(self):
        assert add_years(date(2024, 2, 29), 1) == date(2025, 3, 1)


class TestBoundaries:
    """경계값: 설립일 + N년이 되는 날부터 'N년미만'이 아니다."""

    def test_just_before_boundary_passes(self):
        # 상한 도달일 2026-08-01 > 접수 종료일 2026-07-31 → 종료일까지 3년미만
        result = evaluate("3년미만", date(2023, 8, 1))
        assert result.status == RuleStatus.PASS
        assert result.confidence == Confidence.HIGH
        assert "3년 미만" in result.reason

    def test_exact_boundary_on_end_date_is_review(self):
        # 상한 도달일 = 접수 종료일 2026-07-31 → 종료일 당일에는 이미 3년미만이 아님
        result = evaluate("3년미만", date(2023, 7, 31))
        assert result.status == RuleStatus.REVIEW
        assert "기준일" in result.reason

    def test_just_after_boundary_within_window_is_review(self):
        # 접수기간(7/1~7/31) 중간(7/15)에 상한 도달 → 기준일에 따라 갈림
        result = evaluate("3년미만", date(2023, 7, 15))
        assert result.status == RuleStatus.REVIEW

    def test_clearly_over_is_review_not_fail(self):
        # 기준일(공고일 등)이 데이터에 없으므로 자동 제외하지 않는다 (지시서 14.2)
        result = evaluate("3년미만", date(2020, 1, 1))
        assert result.status == RuleStatus.REVIEW
        assert result.status != RuleStatus.FAIL
        assert "자동 제외하지 않습니다" in result.reason


class TestConditions:
    def test_widest_listed_bucket_applies(self):
        # 관찰된 실제 형태: 누적 구간 목록. 업력 4년 회사도 "10년미만"에 해당
        result = evaluate("1년미만,3년미만,10년미만", date(2022, 3, 15))
        assert result.status == RuleStatus.PASS

    def test_pre_startup_only_fails_for_established_company(self):
        result = evaluate("예비창업자", date(2022, 3, 15))
        assert result.status == RuleStatus.FAIL
        assert result.confidence == Confidence.HIGH
        assert any("전환" in check or "재창업" in check for check in result.human_checks)

    def test_pre_startup_plus_buckets_uses_buckets(self):
        result = evaluate("예비창업자,10년미만", date(2022, 3, 15))
        assert result.status == RuleStatus.PASS

    def test_unknown_expression_is_review(self):
        # "이내"는 "미만"과 같은 의미로 취급하지 않는다
        result = evaluate("3년이내", date(2022, 3, 15))
        assert result.status == RuleStatus.REVIEW
        assert "3년이내" in result.reason

    def test_unknown_token_does_not_block_clear_pass(self):
        result = evaluate("3년미만,7년이상", date(2024, 6, 1))
        assert result.status == RuleStatus.PASS

    def test_no_condition_is_not_applicable(self):
        result = evaluate(None, date(2022, 3, 15))
        assert result.status == RuleStatus.NOT_APPLICABLE
        assert result.human_checks


class TestMissingInformation:
    def test_missing_established_date_is_review(self):
        result = evaluate("3년미만", None)
        assert result.status == RuleStatus.REVIEW
        assert "설립일" in result.reason

    def test_missing_established_date_with_pre_startup_only_is_review(self):
        # 설립일 없음 = 정보 부족이지 예비창업자 확정이 아니다
        result = evaluate("예비창업자", None)
        assert result.status == RuleStatus.REVIEW

    def test_unparseable_end_date_is_review(self):
        result = evaluate("3년미만", date(2023, 8, 1), pbanc_rcpt_end_dt="미정")
        assert result.status == RuleStatus.REVIEW
        assert "접수 종료일" in result.reason

    def test_pass_notes_reference_date_assumption(self):
        result = evaluate("3년미만", date(2023, 8, 1))
        assert any("기준일" in check for check in result.human_checks)
