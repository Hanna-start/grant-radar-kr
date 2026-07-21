"""신청자 유형 규칙 (applicant_type.v1) — 지시서 14.3절.

실제 관찰: aply_trgt = "청소년,대학생,일반인,대학,연구기관,일반기업,1인 창조기업"
형태의 허용 대상 범주 목록.

회사 정보와 공고 조건이 명확히 충돌할 때만 제외한다:
- 회사가 해당할 수 있는 범주가 목록에 있으면 PASS
- 목록 전체가 명백히 기업이 아닌 범주(청소년, 대학생, 대학, 연구기관 등)면 FAIL
- 그 외(일반인 등 회사 해당 여부가 불명확한 범주, 알 수 없는 범주) → REVIEW
"""

from __future__ import annotations

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import Confidence, RuleResult, RuleStatus

# 명백히 기업이 아닌 범주. 목록 전체가 여기 속할 때만 FAIL 근거가 된다.
NON_COMPANY_CATEGORIES = frozenset({"청소년", "대학생", "대학", "연구기관", "공공기관"})


def company_categories(company: Company) -> set[str]:
    """회사가 명확히 해당한다고 볼 수 있는 범주."""
    categories: set[str] = set()
    if company.business_type == "corporation":
        categories.update({"일반기업", "법인사업자"})
    elif company.business_type == "individual":
        categories.update({"개인사업자"})
    if company.employee_count == 1 and company.business_type in ("corporation", "individual"):
        categories.add("1인 창조기업")
    return categories


class ApplicantTypeRule:
    rule_id = "applicant_type.v1"

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult:
        allowed = announcement.applicant_categories

        def result(status, reason, confidence=Confidence.HIGH, human_checks=()):
            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                announcement_value=", ".join(allowed) if allowed else None,
                company_value=company.business_type,
                reason=reason,
                evidence_field="aply_trgt",
                confidence=confidence,
                human_checks=list(human_checks),
            )

        if not allowed:
            return result(
                RuleStatus.NOT_APPLICABLE,
                "공고 데이터에 신청 대상 범주가 없습니다.",
                Confidence.MEDIUM,
                human_checks=["공고 본문의 신청 대상(aply_trgt_ctnt)을 확인하세요."],
            )

        if company.business_type is None:
            return result(
                RuleStatus.REVIEW,
                "회사의 사업자 형태 정보가 없어 신청 대상과 비교할 수 없습니다.",
                Confidence.MEDIUM,
            )

        matches = company_categories(company) & set(allowed)
        if matches:
            return result(
                RuleStatus.PASS,
                f"회사가 해당하는 범주({', '.join(sorted(matches))})가 신청 대상에 포함됩니다.",
                Confidence.HIGH,
            )

        if set(allowed) <= NON_COMPANY_CATEGORIES:
            return result(
                RuleStatus.FAIL,
                f"신청 대상({', '.join(allowed)})에 기업이 포함되지 않습니다.",
                Confidence.HIGH,
                human_checks=["공고 본문에서 기업 신청 가능 여부를 최종 확인하세요."],
            )

        uncertain = [category for category in allowed if category not in NON_COMPANY_CATEGORIES]
        return result(
            RuleStatus.REVIEW,
            f"회사 해당 여부가 불명확한 신청 대상 범주가 있습니다: {', '.join(uncertain)}.",
            Confidence.MEDIUM,
        )
