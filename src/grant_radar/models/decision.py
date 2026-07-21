"""판정 결과 모델 (지시서 16절).

결론만 반환하고 근거를 생략하는 기능은 만들지 않는다. 모든 규칙 결과는
적용 규칙, 공고 값, 회사 값, 이유, 근거 필드를 포함한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from grant_radar.models.announcement import NormalizedAnnouncement


class RuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Decision(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True)
class RuleResult:
    """개별 규칙의 판정 결과."""

    rule_id: str
    status: RuleStatus
    announcement_value: str | None  # 공고에서 확인한 조건
    company_value: str | None  # 회사 정보에서 사용한 값
    reason: str  # 판정 이유 (사람이 읽을 수 있는 문장)
    evidence_field: str | None  # 근거가 위치한 원본 필드명
    confidence: Confidence
    human_checks: list[str] = field(default_factory=list)  # 사람이 추가로 확인할 사항


@dataclass
class EvaluationResult:
    """공고 하나에 대한 전체 판정."""

    announcement: NormalizedAnnouncement
    company_id: str
    decision: Decision
    rule_results: list[RuleResult]
    closed: bool  # 모집 종료 여부 (자격 판정과 별도 표시, 지시서 14.5)
    evaluated_at: datetime

    @property
    def detail_url(self) -> str | None:
        return self.announcement.detail_url

    @property
    def human_checks(self) -> list[str]:
        """규칙들이 남긴 확인 사항의 합집합 (순서 유지, 중복 제거)."""
        seen: dict[str, None] = {}
        for result in self.rule_results:
            for check in result.human_checks:
                seen.setdefault(check)
        return list(seen)
