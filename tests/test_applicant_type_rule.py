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

    def test_small_business_category_passes_only_when_declared(self):
        # 소상공인 지위는 회사 JSON의 확인된 값(small_business)에서만 온다
        result = evaluate("소상공인", business_type="individual", small_business=True)
        assert result.status == RuleStatus.PASS
        assert "소상공인" in result.reason
        # 자기 선언값이므로 증빙 확인 항목이 함께 나온다
        assert any("소상공인 확인서" in check for check in result.human_checks)

    def test_small_business_not_declared_is_review_not_fail(self):
        # 값이 없거나 false면 범주를 더하지 않되, 제외하지 않고 REVIEW로 남긴다
        for declared in (None, False):
            result = evaluate("소상공인", business_type="individual", small_business=declared)
            assert result.status == RuleStatus.REVIEW

    def test_sme_category_passes(self):
        # 중소기업은 회사 프로필에서 명시적으로 확인된 경우에만 통과한다.
        result = evaluate("중소기업")
        assert result.status == RuleStatus.PASS
        result = evaluate("중소기업", business_type="individual", sme=True)
        assert result.status == RuleStatus.PASS

    def test_sme_category_without_declaration_is_review(self):
        result = evaluate("중소기업", sme=None)
        assert result.status == RuleStatus.REVIEW


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
