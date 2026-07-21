"""신청자 유형 규칙 테스트 (지시서 14.3절)."""

from grant_radar.models.decision import Confidence, RuleStatus
from grant_radar.rules.applicant_type import ApplicantTypeRule

from tests.factories import make_announcement, make_company

RULE = ApplicantTypeRule()


def evaluate(categories, **company_overrides):
    announcement = make_announcement(aply_trgt=categories)
    return RULE.evaluate(announcement, make_company(**company_overrides))


class TestPass:
    def test_corporation_matches_general_company(self):
        result = evaluate("청소년,대학생,일반인,일반기업")
        assert result.status == RuleStatus.PASS
        assert "일반기업" in result.reason

    def test_single_person_company_category(self):
        result = evaluate("1인 창조기업", employee_count=1)
        assert result.status == RuleStatus.PASS


class TestFail:
    def test_clearly_non_company_targets_fail(self):
        result = evaluate("청소년,대학생")
        assert result.status == RuleStatus.FAIL
        assert result.confidence == Confidence.HIGH
        assert result.human_checks  # 본문 최종 확인 항목 포함

    def test_university_and_research_only_fail(self):
        result = evaluate("대학,연구기관")
        assert result.status == RuleStatus.FAIL


class TestReview:
    def test_ambiguous_target_is_review(self):
        # "일반인"만 허용 — 법인이 해당하는지 불명확하므로 제외하지 않는다
        result = evaluate("일반인")
        assert result.status == RuleStatus.REVIEW
        assert "일반인" in result.reason

    def test_one_person_category_without_matching_headcount_is_review(self):
        result = evaluate("1인 창조기업", employee_count=12)
        assert result.status == RuleStatus.REVIEW

    def test_missing_company_type_is_review(self):
        result = evaluate("일반기업", business_type=None)
        assert result.status == RuleStatus.REVIEW


class TestNotApplicable:
    def test_no_categories_is_not_applicable(self):
        result = evaluate(None)
        assert result.status == RuleStatus.NOT_APPLICABLE
        assert result.human_checks
