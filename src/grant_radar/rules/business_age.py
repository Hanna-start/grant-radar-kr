"""업력 규칙 (business_age.v1) — 지시서 14.2절.

실제 관찰: biz_enyy는 "예비창업자,1년미만,2년미만,...,10년미만" 형태의
허용 구간 목록이다. "N년미만"은 문자 그대로 업력 < N년으로 해석한다.

API가 업력 산정 기준일을 제공하지 않으므로(기준일이 없으면 임의로 결정하지
않는다는 원칙에 따라) 보수적으로만 판정한다:

- PASS: 접수 종료일 기준 업력이 나열된 최대 상한(max N년미만)보다 작으면,
  접수 종료일 이전의 어떤 기준일(공고일 포함)에서도 미만이 성립한다.
  "기준일이 접수 종료일 이후로 명시된 경우"만 human_checks로 남긴다.
- FAIL: 허용 대상이 예비창업자뿐인데 회사가 이미 설립된 경우 (기준일 무관).
- 그 외(경계 근처, 해석 불가 표현, 설립일·종료일 정보 부족) → REVIEW.
- 조건 없음 → NOT_APPLICABLE (+본문 확인 항목).

경계값 처리: "미만"은 경계일을 포함하지 않는다. 설립일 + N년이 되는 날부터는
"N년미만"이 아니다. "이내", "이하" 등 다른 표현은 같은 의미로 취급하지 않고
REVIEW로 보낸다.
"""

from __future__ import annotations

import re
from datetime import date

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import Confidence, RuleResult, RuleStatus

BUCKET_PATTERN = re.compile(r"^(\d+)\s*년\s*미만$")
PRE_STARTUP_TOKEN = "예비창업자"

REFERENCE_DATE_CHECK = (
    "업력 산정 기준일이 공고문에 접수 종료일 이후(협약 체결일 등)로 명시되어 있는지 확인하세요."
)
RESTART_EXCEPTION_CHECK = (
    "재창업, 개인사업자의 법인 전환 등 업력 산정 예외 요건이 공고문에 있는지 확인하세요."
)


def add_years(base: date, years: int) -> date:
    """정확한 날짜 연산으로 N년 후를 계산한다 (2월 29일 설립은 3월 1일로 처리)."""
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        return date(base.year + years, 3, 1)


class BusinessAgeRule:
    rule_id = "business_age.v1"

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult:
        conditions = announcement.business_age_conditions
        established = company.established_date

        def result(status, reason, confidence=Confidence.HIGH, human_checks=()):
            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                announcement_value=", ".join(conditions) if conditions else None,
                company_value=established.isoformat() if established else None,
                reason=reason,
                evidence_field="biz_enyy",
                confidence=confidence,
                human_checks=list(human_checks),
            )

        if not conditions:
            return result(
                RuleStatus.NOT_APPLICABLE,
                "공고 데이터에 업력 조건이 없습니다.",
                Confidence.MEDIUM,
                human_checks=["공고 본문·첨부의 업력(창업 기간) 조건을 확인하세요."],
            )

        pre_startup_allowed = PRE_STARTUP_TOKEN in conditions
        bounds: list[int] = []
        unknown_tokens: list[str] = []
        for token in conditions:
            if token == PRE_STARTUP_TOKEN:
                continue
            match = BUCKET_PATTERN.match(token)
            if match:
                bounds.append(int(match.group(1)))
            else:
                unknown_tokens.append(token)

        if established is None:
            return result(
                RuleStatus.REVIEW,
                "회사 설립일 정보가 없어 업력을 계산할 수 없습니다.",
                Confidence.MEDIUM,
            )

        if pre_startup_allowed and not bounds and not unknown_tokens:
            return result(
                RuleStatus.FAIL,
                f"허용 대상이 예비창업자뿐이지만 회사는 {established.isoformat()}에 "
                "설립된 기업입니다.",
                Confidence.HIGH,
                human_checks=[RESTART_EXCEPTION_CHECK],
            )

        if bounds:
            max_bound = max(bounds)
            end_reference = announcement.application_end_at.value
            if end_reference is None:
                return result(
                    RuleStatus.REVIEW,
                    "접수 종료일을 확인할 수 없어 업력 판정 기준 시점을 잡을 수 없습니다. "
                    f"(원본 값: {announcement.application_end_at.raw!r})",
                    Confidence.MEDIUM,
                )
            cutoff = add_years(established, max_bound)
            if end_reference < cutoff:
                matched = min(
                    bound for bound in bounds if end_reference < add_years(established, bound)
                )
                return result(
                    RuleStatus.PASS,
                    f"설립일 {established.isoformat()} 기준으로 접수 종료일"
                    f"({end_reference.isoformat()})까지 업력이 {matched}년 미만이므로 "
                    f"허용 구간({matched}년미만)에 해당합니다.",
                    Confidence.HIGH,
                    human_checks=[REFERENCE_DATE_CHECK],
                )
            start_reference = announcement.application_start_at.value
            if start_reference is not None and start_reference < cutoff:
                return result(
                    RuleStatus.REVIEW,
                    f"업력이 접수 기간 중에 허용 상한({max_bound}년)에 도달합니다 "
                    f"(설립일 {established.isoformat()}, 상한 도달일 {cutoff.isoformat()}). "
                    "공고의 업력 산정 기준일 확인이 필요합니다.",
                    Confidence.MEDIUM,
                )
            return result(
                RuleStatus.REVIEW,
                f"접수 기간 기준 업력이 허용 상한({max_bound}년)을 넘는 것으로 보입니다 "
                f"(설립일 {established.isoformat()}). 다만 업력 산정 기준일(공고일 등)이 "
                "데이터에 없어 자동 제외하지 않습니다.",
                Confidence.MEDIUM,
                human_checks=[RESTART_EXCEPTION_CHECK],
            )

        return result(
            RuleStatus.REVIEW,
            "해석할 수 없는 업력 표현이 있습니다: "
            f"{', '.join(unknown_tokens)}. ('미만'과 '이내'·'이하'는 같은 의미로 취급하지 않습니다)",
            Confidence.LOW,
        )
