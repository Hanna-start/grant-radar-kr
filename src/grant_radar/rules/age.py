"""대표자 연령 규칙 (age.v1) — 지시서 14.4절.

실제 관찰 (2026-07-21, 표본 100건): biz_trgt_age는 고정된 3개 구간 토큰의
쉼표 목록이다.
  "만 20세 미만" / "만 20세 이상 ~ 만 39세 이하" / "만 40세 이상"
부분 목록(예: "만 20세 이상 ~ 만 39세 이하"만)이 청년 전용 등 연령 제한을
표현한다.

원칙:
- 연령 조건이 있고 대표자 생년월일이 있을 때만 계산한다.
  실질적 제한이 있는데 생년월일이 없으면 REVIEW.
- 허용 구간이 성인 전 연령(만 20세 이상 전부)을 포함하면 법인 대표 기준
  실질적 제한이 없다고 보고 PASS 한다 (미성년 대표는 상정하지 않는다).
- 연령 산정 기준일이 데이터에 없으므로 자동 제외(FAIL)하지 않는다
  (14.4: 공고가 연령 기준일을 명시하지 않으면 자동 제외하지 않는다).
"""

from __future__ import annotations

import re
from datetime import date

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import Confidence, RuleResult, RuleStatus

UNDER_PATTERN = re.compile(r"^만\s*(\d+)세\s*미만$")
BETWEEN_PATTERN = re.compile(r"^만\s*(\d+)세\s*이상\s*~\s*만\s*(\d+)세\s*이하$")
OVER_PATTERN = re.compile(r"^만\s*(\d+)세\s*이상$")

ADULT_MIN_AGE = 20
_UNBOUNDED = 10**9

REFERENCE_DATE_CHECK = (
    "연령 산정 기준일이 공고문에 별도로 명시되어 있는지 확인하세요 (본 판정은 접수 기간 기준)."
)


def parse_age_band(token: str) -> tuple[int, int] | None:
    """연령 구간 토큰을 (최소, 최대) 나이로 해석한다. 해석 불가면 None."""
    match = UNDER_PATTERN.match(token)
    if match:
        return (0, int(match.group(1)) - 1)
    match = BETWEEN_PATTERN.match(token)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    match = OVER_PATTERN.match(token)
    if match:
        return (int(match.group(1)), _UNBOUNDED)
    return None


def _covers_all_adults(bands: list[tuple[int, int]]) -> bool:
    """허용 구간의 합집합이 만 20세 이상 전 연령을 포함하는가."""
    age = ADULT_MIN_AGE
    # 구간들이 정수 나이 기준으로 이어지는지 순차 확인
    while age <= 120:
        covering = [band for band in bands if band[0] <= age <= band[1]]
        if not covering:
            return False
        highest = max(band[1] for band in covering)
        if highest >= _UNBOUNDED:
            return True
        age = highest + 1
    return True


def age_on(birth: date, on: date) -> int:
    """만 나이 (정확한 날짜 비교)."""
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


class AgeRule:
    rule_id = "age.v1"

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult:
        conditions = announcement.applicant_age_conditions
        birth = company.representative_birth_date

        def result(status, reason, confidence=Confidence.HIGH, human_checks=()):
            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                announcement_value=", ".join(conditions) if conditions else None,
                company_value=birth.isoformat() if birth else None,
                reason=reason,
                evidence_field="biz_trgt_age",
                confidence=confidence,
                human_checks=list(human_checks),
            )

        if not conditions:
            return result(
                RuleStatus.NOT_APPLICABLE,
                "공고 데이터에 연령 조건이 없습니다.",
                Confidence.MEDIUM,
                human_checks=["공고 본문의 연령 조건을 확인하세요."],
            )

        bands: list[tuple[int, int]] = []
        unknown_tokens: list[str] = []
        for token in conditions:
            band = parse_age_band(token)
            if band is None:
                unknown_tokens.append(token)
            else:
                bands.append(band)

        if bands and _covers_all_adults(bands):
            return result(
                RuleStatus.PASS,
                "허용 연령이 성인 전 연령(만 20세 이상)을 포함하므로 법인 대표 기준 "
                "실질적 제한이 없습니다.",
                Confidence.HIGH,
            )

        if unknown_tokens:
            return result(
                RuleStatus.REVIEW,
                f"해석할 수 없는 연령 표현이 있습니다: {', '.join(unknown_tokens)}.",
                Confidence.LOW,
            )

        # 실질적 연령 제한 존재
        if birth is None:
            return result(
                RuleStatus.REVIEW,
                "공고에 연령 제한이 있으나 대표자 생년월일 정보가 없어 비교할 수 없습니다.",
                Confidence.MEDIUM,
            )

        references = [
            value
            for value in (
                announcement.application_start_at.value,
                announcement.application_end_at.value,
            )
            if value is not None
        ]
        if not references:
            return result(
                RuleStatus.REVIEW,
                "접수 기간을 확인할 수 없어 연령 계산 기준 시점을 잡을 수 없습니다.",
                Confidence.MEDIUM,
            )

        ages = {age_on(birth, reference) for reference in references}
        all_in = all(any(low <= age <= high for low, high in bands) for age in ages)
        if all_in:
            age_text = f"{min(ages)}세" if len(ages) == 1 else f"{min(ages)}~{max(ages)}세"
            return result(
                RuleStatus.PASS,
                f"대표자 연령({age_text}, 접수 기간 기준)이 허용 구간에 포함됩니다.",
                Confidence.HIGH,
                human_checks=[REFERENCE_DATE_CHECK],
            )
        return result(
            RuleStatus.REVIEW,
            "대표자 연령이 허용 구간 밖이거나 접수 기간 중 경계를 지납니다. "
            "연령 산정 기준일이 데이터에 없어 자동 제외하지 않습니다.",
            Confidence.MEDIUM,
            human_checks=[REFERENCE_DATE_CHECK],
        )
