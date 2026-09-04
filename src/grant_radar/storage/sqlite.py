"""SQLite 저장 및 신규·변경 공고 감지.

원칙 (지시서 12절):
- 신규 감지 키는 `source + source_id` 조합이다.
- 변경 감지는 정규화된 주요 필드의 해시로 한다.
- 공고가 수정되면 덮어쓰기만 하지 않고 직전 해시(previous_hash)와
  변경 확인 시각(last_changed_at)을 남긴다.
- source_id가 없는 공고는 저장하지 않고 UNKNOWN으로 보고한다
  (중복 방지가 불가능하므로). 해당 공고 자체는 수집 결과에서 유지된다.

저장 형식: 정규화 JSON은 사람 확인용이며, 재처리의 원천은 raw_json이다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from grant_radar.models.announcement import NormalizedAnnouncement

# 변경 감지 대상 주요 필드 (지시서 12절의 해시 대상 후보를 그대로 사용).
# 날짜는 파싱 결과가 아닌 원본 문자열(raw)을 사용해 원천 데이터의 변화를 그대로 감지한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS announcements (
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    raw_json        TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    last_changed_at TEXT,
    previous_hash   TEXT,
    PRIMARY KEY (source, source_id)
)
"""


def content_hash(announcement: NormalizedAnnouncement) -> str:
    """정규화된 주요 필드의 SHA-256 해시."""
    payload = {
        "title": announcement.title,
        "summary": announcement.summary,
        "target_description": announcement.target_description,
        "excluded_target_description": announcement.excluded_target_description,
        "region": announcement.region,
        "application_start_raw": announcement.application_start_at.raw,
        "application_end_raw": announcement.application_end_at.raw,
        "detail_url": announcement.detail_url,
        "recruitment_open": announcement.recruitment_open,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serialize_normalized(announcement: NormalizedAnnouncement) -> str:
    # date/datetime은 ISO 문자열로 직렬화된다. 재처리 원천은 raw_json이므로
    # 이 JSON을 다시 모델로 복원할 필요는 없다.
    return json.dumps(
        dataclasses.asdict(announcement), ensure_ascii=False, sort_keys=True, default=str
    )


class AnnouncementStore:
    """공고 저장소. `with` 문으로 사용한다."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AnnouncementStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def upsert(self, announcement: NormalizedAnnouncement, seen_at: datetime) -> str:
        """공고를 저장하고 변경 상태를 반환한다.

        반환: "NEW" | "UPDATED" | "UNCHANGED" | "UNKNOWN"
        (모집 종료 여부는 저장과 별개 관심사이므로 services.ingestion에서 판별한다)
        """
        if announcement.source_id is None:
            return "UNKNOWN"

        new_hash = content_hash(announcement)
        normalized_json = _serialize_normalized(announcement)
        raw_json = json.dumps(announcement.raw_data, ensure_ascii=False, sort_keys=True)
        seen = seen_at.isoformat()
        key = (announcement.source, announcement.source_id)

        row = self._conn.execute(
            "SELECT content_hash FROM announcements WHERE source = ? AND source_id = ?",
            key,
        ).fetchone()

        if row is None:
            self._conn.execute(
                "INSERT INTO announcements (source, source_id, content_hash,"
                " normalized_json, raw_json, first_seen_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, new_hash, normalized_json, raw_json, seen, seen),
            )
            self._conn.commit()
            return "NEW"

        if row["content_hash"] == new_hash:
            # 주요 필드는 동일 — 최신 확인 시각과 본문만 갱신한다
            self._conn.execute(
                "UPDATE announcements SET last_seen_at = ?, normalized_json = ?,"
                " raw_json = ? WHERE source = ? AND source_id = ?",
                (seen, normalized_json, raw_json, *key),
            )
            self._conn.commit()
            return "UNCHANGED"

        self._conn.execute(
            "UPDATE announcements SET previous_hash = content_hash, content_hash = ?,"
            " last_changed_at = ?, last_seen_at = ?, normalized_json = ?, raw_json = ?"
            " WHERE source = ? AND source_id = ?",
            (new_hash, seen, seen, normalized_json, raw_json, *key),
        )
        self._conn.commit()
        return "UPDATED"

    def get(self, source: str, source_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM announcements WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def count(self) -> int:
        (value,) = self._conn.execute("SELECT COUNT(*) FROM announcements").fetchone()
        return value

    def rows(self) -> list[dict]:
        """저장된 모든 공고 행. 이후 판정(evaluate) 단계의 입력이 된다."""
        return [dict(row) for row in self._conn.execute("SELECT * FROM announcements")]
