"""수집 오케스트레이션: 응답 정규화 → 저장 → 변경/마감 상태 판별.

마감 정책 (지시서 14.5절):
- 모집 진행 여부(rcrt_prgs_yn)가 명확히 N이면 마감.
- 접수 종료일이 지났으면 마감. 종료일은 시각 없이 날짜만 제공되므로
  (실제 관찰: "YYYYMMDD") 해당 날짜의 한국 시간(Asia/Seoul, UTC+9)
  하루가 끝날 때까지는 마감으로 보지 않는다.
- 종료일 파싱 실패나 정보 부족은 마감 사유가 아니다 (놓치지 않기 우선).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.normalization.kstartup import normalize_page
from grant_radar.storage.sqlite import AnnouncementStore

# 한국 표준시. DST가 없으므로 고정 오프셋으로 충분하다 (tzdata 의존성 불필요).
KST = timezone(timedelta(hours=9), "KST")


@dataclass(frozen=True)
class IngestOutcome:
    """공고 하나의 수집 결과."""

    announcement: NormalizedAnnouncement
    change: str  # NEW / UPDATED / UNCHANGED / UNKNOWN
    closed: bool

    @property
    def display_status(self) -> str:
        """보고용 상태. 마감이 확인되면 CLOSED가 우선한다 (지시서 12절)."""
        return "CLOSED" if self.closed else self.change


def is_closed(announcement: NormalizedAnnouncement, as_of: datetime) -> bool:
    """명확히 마감으로 판단되는 경우만 True.

    정보 부족(모집 여부 미상, 종료일 없음/파싱 실패)은 마감이 아니다.
    """
    if announcement.recruitment_open is False:
        return True
    end_date = announcement.application_end_at.value
    if end_date is not None:
        return as_of.astimezone(KST).date() > end_date
    return False


def ingest_page(
    store: AnnouncementStore,
    body: Any,
    fetched_at: datetime | None = None,
    as_of: datetime | None = None,
) -> list[IngestOutcome]:
    """응답 한 페이지를 정규화해 저장하고, 공고별 상태를 반환한다."""
    if as_of is None:
        as_of = datetime.now(KST)
    seen_at = fetched_at if fetched_at is not None else as_of

    outcomes: list[IngestOutcome] = []
    for announcement in normalize_page(body, fetched_at):
        change = store.upsert(announcement, seen_at=seen_at)
        outcomes.append(
            IngestOutcome(
                announcement=announcement,
                change=change,
                closed=is_closed(announcement, as_of),
            )
        )
    return outcomes
