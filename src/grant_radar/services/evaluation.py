"""판정 오케스트레이션 (지시서 16절).

전체 판정 규칙:
1. 자동 제외가 허용된 규칙(AUTO_EXCLUDE_RULE_IDS)에서 신뢰도 HIGH의
   FAIL이 하나 이상 → INELIGIBLE
2. 그 외 FAIL, REVIEW, ERROR가 하나라도 있으면 → REVIEW_REQUIRED
3. 적용된 규칙(PASS)이 하나 이상이고 나머지가 전부 NOT_APPLICABLE → ELIGIBLE
4. 적용 가능한 규칙이 없으면 → REVIEW_REQUIRED

모든 규칙의 FAIL이 자동 제외를 의미하지 않는다. 자동 제외 목록은 별도로
관리하며, 목록 밖 규칙의 FAIL은 REVIEW_REQUIRED로 낮춰 사람이 확인한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import (
    Confidence,
    Decision,
    EvaluationResult,
    RuleResult,
    RuleStatus,
)
from grant_radar.normalization.kstartup import normalize_announcement
from grant_radar.rules.age import AgeRule
from grant_radar.rules.applicant_type import ApplicantTypeRule
from grant_radar.rules.base import Rule
from grant_radar.rules.business_age import BusinessAgeRule
from grant_radar.rules.region import RegionRule, load_region_mapping
from grant_radar.services.ingestion import KST, is_closed
from grant_radar.storage.sqlite import AnnouncementStore

# FAIL(HIGH)이 전체 INELIGIBLE로 이어질 수 있는 규칙 (docs/eligibility-rules.md 참고).
# age.v1은 기준일 부재로 FAIL을 내지 않으므로 목록에 없다.
AUTO_EXCLUDE_RULE_IDS = frozenset({"region.v1", "business_age.v1", "applicant_type.v1"})


def default_rules() -> list[Rule]:
    return [RegionRule(load_region_mapping()), BusinessAgeRule(), ApplicantTypeRule(), AgeRule()]


def decide(rule_results: Sequence[RuleResult]) -> Decision:
    hard_fail = any(
        result.status == RuleStatus.FAIL
        and result.confidence == Confidence.HIGH
        and result.rule_id in AUTO_EXCLUDE_RULE_IDS
        for result in rule_results
    )
    if hard_fail:
        return Decision.INELIGIBLE
    needs_review = any(
        result.status in (RuleStatus.FAIL, RuleStatus.REVIEW, RuleStatus.ERROR)
        for result in rule_results
    )
    if needs_review:
        return Decision.REVIEW_REQUIRED
    if any(result.status == RuleStatus.PASS for result in rule_results):
        return Decision.ELIGIBLE
    return Decision.REVIEW_REQUIRED


def evaluate_announcement(
    announcement: NormalizedAnnouncement,
    company: Company,
    rules: Sequence[Rule] | None = None,
    as_of: datetime | None = None,
) -> EvaluationResult:
    if rules is None:
        rules = default_rules()
    if as_of is None:
        as_of = datetime.now(KST)

    results: list[RuleResult] = []
    for rule in rules:
        try:
            results.append(rule.evaluate(announcement, company))
        except Exception as exc:  # 규칙 하나의 오류로 전체 판정이 중단되지 않는다
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    status=RuleStatus.ERROR,
                    announcement_value=None,
                    company_value=None,
                    reason=f"규칙 실행 오류: {type(exc).__name__}: {exc}",
                    evidence_field=None,
                    confidence=Confidence.LOW,
                    human_checks=["규칙 오류로 판정하지 못했습니다. 공고를 직접 확인하세요."],
                )
            )

    return EvaluationResult(
        announcement=announcement,
        company_id=company.company_id,
        decision=decide(results),
        rule_results=results,
        closed=is_closed(announcement, as_of),
        evaluated_at=as_of,
    )


def evaluate_stored(
    store: AnnouncementStore,
    company: Company,
    rules: Sequence[Rule] | None = None,
    as_of: datetime | None = None,
) -> list[EvaluationResult]:
    """저장소의 모든 공고를 raw_json에서 재정규화해 판정한다."""
    if rules is None:
        rules = default_rules()
    evaluations: list[EvaluationResult] = []
    for row in store.rows():
        raw = json.loads(row["raw_json"])
        fetched_at = datetime.fromisoformat(row["last_seen_at"])
        announcement = normalize_announcement(raw, fetched_at)
        evaluations.append(evaluate_announcement(announcement, company, rules, as_of))
    return evaluations
