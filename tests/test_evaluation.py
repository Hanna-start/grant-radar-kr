"""전체 판정 테스트 (지시서 20.5절)."""

from datetime import datetime, timezone
from pathlib import Path

from grant_radar.models.decision import Confidence, Decision, RuleResult, RuleStatus
from grant_radar.rules.age import AgeRule
from grant_radar.rules.applicant_type import ApplicantTypeRule
from grant_radar.rules.business_age import BusinessAgeRule
from grant_radar.rules.region import RegionRule, load_region_mapping
from grant_radar.services.evaluation import decide, evaluate_announcement, evaluate_stored
from grant_radar.services.ingestion import ingest_page
from grant_radar.storage.sqlite import AnnouncementStore

from tests.factories import make_announcement, make_company
from tests.test_normalization import full_item

MAPPING_PATH = Path(__file__).parent.parent / "data" / "reference" / "region_mapping.json"
RULES = [
    RegionRule(load_region_mapping(MAPPING_PATH)),
    BusinessAgeRule(),
    ApplicantTypeRule(),
    AgeRule(),
]
AS_OF = datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc)


def rule_result(rule_id, status, confidence=Confidence.HIGH):
    return RuleResult(
        rule_id=rule_id,
        status=status,
        announcement_value="공고 값",
        company_value="회사 값",
        reason="테스트용 결과",
        evidence_field=None,
        confidence=confidence,
    )


class TestDecide:
    def test_high_confidence_fail_on_auto_exclude_rule_is_ineligible(self):
        results = [
            rule_result("region.v1", RuleStatus.FAIL),
            rule_result("business_age.v1", RuleStatus.PASS),
        ]
        assert decide(results) == Decision.INELIGIBLE

    def test_low_confidence_fail_is_review(self):
        results = [rule_result("region.v1", RuleStatus.FAIL, Confidence.MEDIUM)]
        assert decide(results) == Decision.REVIEW_REQUIRED

    def test_fail_outside_auto_exclude_list_is_review(self):
        # 자동 제외 목록에 없는 규칙의 FAIL은 전체 제외로 이어지지 않는다
        results = [
            rule_result("custom.v1", RuleStatus.FAIL),
            rule_result("region.v1", RuleStatus.PASS),
        ]
        assert decide(results) == Decision.REVIEW_REQUIRED

    def test_all_pass_is_eligible(self):
        results = [
            rule_result("region.v1", RuleStatus.PASS),
            rule_result("business_age.v1", RuleStatus.PASS),
        ]
        assert decide(results) == Decision.ELIGIBLE

    def test_pass_with_review_is_review(self):
        results = [
            rule_result("region.v1", RuleStatus.PASS),
            rule_result("business_age.v1", RuleStatus.REVIEW),
        ]
        assert decide(results) == Decision.REVIEW_REQUIRED

    def test_error_is_review(self):
        results = [
            rule_result("region.v1", RuleStatus.PASS),
            rule_result("business_age.v1", RuleStatus.ERROR),
        ]
        assert decide(results) == Decision.REVIEW_REQUIRED

    def test_pass_with_not_applicable_is_eligible(self):
        results = [
            rule_result("region.v1", RuleStatus.PASS),
            rule_result("business_age.v1", RuleStatus.NOT_APPLICABLE),
        ]
        assert decide(results) == Decision.ELIGIBLE

    def test_no_applicable_rule_is_review(self):
        results = [rule_result("region.v1", RuleStatus.NOT_APPLICABLE)]
        assert decide(results) == Decision.REVIEW_REQUIRED
        assert decide([]) == Decision.REVIEW_REQUIRED


class TestEvaluateAnnouncement:
    def test_all_rules_pass_is_eligible(self):
        announcement = make_announcement(
            supt_regin="전국", biz_enyy="10년미만", aply_trgt="일반기업"
        )
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.decision == Decision.ELIGIBLE
        assert len(evaluation.rule_results) == 4
        assert all(r.reason for r in evaluation.rule_results)  # 근거 없는 결과 금지

    def test_region_mismatch_is_ineligible_with_reason(self):
        announcement = make_announcement(
            supt_regin="부산", biz_enyy="10년미만", aply_trgt="일반기업"
        )
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.decision == Decision.INELIGIBLE
        region = next(r for r in evaluation.rule_results if r.rule_id == "region.v1")
        assert region.status == RuleStatus.FAIL
        assert region.reason  # 제외 결과에도 이유가 있어야 한다

    def test_information_gap_is_review_not_exclusion(self):
        announcement = make_announcement(supt_regin=None, biz_enyy="10년미만", aply_trgt="일반기업")
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.decision == Decision.REVIEW_REQUIRED

    def test_broken_rule_becomes_error_result(self):
        class BrokenRule:
            rule_id = "broken.v1"

            def evaluate(self, announcement, company):
                raise RuntimeError("의도된 테스트 오류")

        announcement = make_announcement(supt_regin="전국")
        evaluation = evaluate_announcement(
            announcement, make_company(), [*RULES, BrokenRule()], AS_OF
        )
        broken = next(r for r in evaluation.rule_results if r.rule_id == "broken.v1")
        assert broken.status == RuleStatus.ERROR
        assert evaluation.decision == Decision.REVIEW_REQUIRED

    def test_closed_announcement_still_evaluated(self):
        announcement = make_announcement(
            supt_regin="전국", biz_enyy="10년미만", aply_trgt="일반기업", rcrt_prgs_yn="N"
        )
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.closed is True
        # 마감은 자격 판정과 별도 표시 (지시서 14.5)
        assert evaluation.decision in (Decision.ELIGIBLE, Decision.REVIEW_REQUIRED)

    def test_human_checks_are_aggregated(self):
        announcement = make_announcement(supt_regin="부산", biz_enyy=None)
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.human_checks

    def test_youth_only_announcement_is_review_not_eligible(self):
        # 표본 검증(2026-07-21)에서 발견된 오판 회귀 테스트:
        # 청년 전용 공고(만 20세 이상 ~ 만 39세 이하)가 대표자 생년월일 정보
        # 없이 ELIGIBLE로 통과되면 안 된다
        announcement = make_announcement(
            supt_regin="전국",
            biz_enyy="10년미만",
            aply_trgt="일반기업",
            biz_trgt_age="만 20세 이상 ~ 만 39세 이하",
        )
        evaluation = evaluate_announcement(announcement, make_company(), RULES, AS_OF)
        assert evaluation.decision == Decision.REVIEW_REQUIRED
        age = next(r for r in evaluation.rule_results if r.rule_id == "age.v1")
        assert age.status == RuleStatus.REVIEW


class TestEvaluateStored:
    def test_round_trip_from_store(self):
        body = {
            "data": [
                full_item(supt_regin="전국", biz_enyy="10년미만", aply_trgt="일반기업"),
                full_item(
                    pbanc_sn=900002, supt_regin="부산", aply_trgt="일반기업", biz_enyy="10년미만"
                ),
            ]
        }
        with AnnouncementStore(":memory:") as store:
            ingest_page(store, body, fetched_at=AS_OF, as_of=AS_OF)
            evaluations = evaluate_stored(store, make_company(), RULES, AS_OF)
        decisions = {e.announcement.source_id: e.decision for e in evaluations}
        assert decisions["900001"] == Decision.ELIGIBLE
        assert decisions["900002"] == Decision.INELIGIBLE
