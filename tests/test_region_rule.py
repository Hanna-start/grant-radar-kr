"""지역 규칙 테스트 (지시서 20.3절)."""

from pathlib import Path

from grant_radar.models.decision import Confidence, RuleStatus
from grant_radar.rules.region import RegionRule, load_region_mapping

from tests.factories import make_announcement, make_company

MAPPING_PATH = Path(__file__).parent.parent / "data" / "reference" / "region_mapping.json"
RULE = RegionRule(load_region_mapping(MAPPING_PATH))


def evaluate(region, **company_overrides):
    return RULE.evaluate(make_announcement(supt_regin=region), make_company(**company_overrides))


class TestPass:
    def test_exact_match_via_alias(self):
        # 회사는 "서울특별시", 공고는 실제 관찰 형태인 "서울"
        result = evaluate("서울")
        assert result.status == RuleStatus.PASS
        assert result.evidence_field == "supt_regin"
        assert "서울" in result.reason

    def test_nationwide(self):
        result = evaluate("전국")
        assert result.status == RuleStatus.PASS
        assert "전국" in result.reason

    def test_multiple_regions(self):
        result = evaluate("서울,부산")
        assert result.status == RuleStatus.PASS

    def test_metropolitan_group(self):
        result = evaluate("수도권")
        assert result.status == RuleStatus.PASS

    def test_combined_region_token_resolves_for_matching_company(self):
        # 실제 관찰(표본 100건): supt_regin="전남광주" 결합 표현
        result = evaluate("전남광주", headquarters_region="광주광역시")
        assert result.status == RuleStatus.PASS

    def test_pass_even_with_extra_unresolved_token(self):
        # 해석 불가 토큰은 허용 범위를 넓힐 뿐이므로 본점 일치가 확인되면 PASS
        result = evaluate("서울,해외거점")
        assert result.status == RuleStatus.PASS


class TestFail:
    def test_clear_mismatch(self):
        result = evaluate("부산")
        assert result.status == RuleStatus.FAIL
        assert result.confidence == Confidence.HIGH
        assert result.announcement_value == "부산"
        assert result.company_value == "서울특별시"
        assert any("이전" in check for check in result.human_checks)

    def test_non_metropolitan_group_mismatch(self):
        result = evaluate("비수도권")
        assert result.status == RuleStatus.FAIL

    def test_combined_region_token_fails_for_seoul_company(self):
        # 표본 검증 전에는 매핑 누락으로 REVIEW였던 사례 — 매핑 추가 후 명확 판정
        result = evaluate("전남광주")
        assert result.status == RuleStatus.FAIL


class TestReview:
    def test_missing_region_is_review(self):
        result = evaluate(None)
        assert result.status == RuleStatus.REVIEW
        assert result.status != RuleStatus.FAIL

    def test_unmapped_expression_is_review(self):
        result = evaluate("해외")
        assert result.status == RuleStatus.REVIEW
        assert "해외" in result.reason

    def test_missing_company_region_is_review(self):
        result = evaluate("서울", headquarters_region=None)
        assert result.status == RuleStatus.REVIEW

    def test_unmapped_company_region_is_review(self):
        result = evaluate("서울", headquarters_region="알수없는곳")
        assert result.status == RuleStatus.REVIEW

    def test_branch_office_match_is_review_not_fail(self):
        result = evaluate(
            "서울", headquarters_region="부산광역시", business_locations=["서울특별시"]
        )
        assert result.status == RuleStatus.REVIEW
        assert "사업장" in result.reason
