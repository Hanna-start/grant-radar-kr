"""정규화된 공고 모델.

실제 API 응답 관찰(docs/api-observations.md, 2026-07-21)을 기반으로 설계했다.

원칙:
- 정보가 없으면 None 또는 빈 목록으로 표현한다. 빈 문자열로 바꾸지 않는다.
- 날짜 파싱 실패는 공고를 버리는 사유가 아니다. 원본 문자열과 오류를 보존한다.
- 원본 응답 항목은 raw_data에 그대로 보존한다 (알 수 없는 필드 포함).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class DateField:
    """날짜 필드. 파싱에 실패해도 원본(raw)을 보존하고 오류를 기록한다."""

    raw: str | None = None
    value: date | None = None
    error: str | None = None


@dataclass(frozen=True)
class ApplicationMethod:
    """신청 방법 하나.

    method: online / visit / postal / fax / email / etc
    description: API가 제공한 값 그대로. 설명 텍스트일 수도, URL일 수도,
        인코딩된 값(이메일 필드에서 base64 형태 관찰)일 수도 있다. 해석하지 않는다.
    """

    method: str
    description: str


@dataclass
class NormalizedAnnouncement:
    """내부 정규화 공고. 필드 의미는 docs/api-observations.md 참고."""

    source: str
    source_id: str | None
    title: str | None
    summary: str | None
    support_category: str | None
    target_description: str | None
    excluded_target_description: str | None
    region: str | None
    application_start_at: DateField
    application_end_at: DateField
    organization_name: str | None
    supervising_organization: str | None
    contact_department: str | None
    contact_phone: str | None
    guide_url: str | None
    detail_url: str | None
    application_methods: list[ApplicationMethod]
    business_age_conditions: list[str]
    applicant_age_conditions: list[str]
    # 실제 값 형태 미관찰(표본에서 전부 null) — 해석하지 않고 텍스트로 보존한다
    preferred_conditions: str | None
    recruitment_open: bool | None
    integrated_announcement: bool | None
    raw_data: dict
    fetched_at: datetime | None
    # 정규화 과정에서 발견한 특이사항 (누락 식별자, 해석 불가 값, URL 보정 등).
    # 이후 판정 단계에서 REVIEW_REQUIRED 근거로 활용할 수 있다.
    issues: list[str] = field(default_factory=list)
