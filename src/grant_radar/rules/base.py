"""규칙 공통 인터페이스.

모든 규칙은 다음 원칙을 따른다 (지시서 2.1, 14절):
- 정보 부족·모호함은 FAIL이 아니라 REVIEW다.
- FAIL은 명확하고 객관적인 불일치에만 사용한다.
- 모든 결과에 이유와 근거 필드를 포함한다.
"""

from __future__ import annotations

from typing import Protocol

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import RuleResult


class Rule(Protocol):
    rule_id: str

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult: ...
