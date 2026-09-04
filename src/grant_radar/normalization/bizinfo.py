"""기업마당(bizinfo) 응답 정규화.

실제 응답 관찰(docs/api-observations.md, 2026-08-21) 기반:
- 항목 필드 20종, 성공 응답은 {response: {body: {items: {item: [...]}}}}.
- 신청기간(reqstBeginEndDe)은 "YYYY-MM-DD ~ YYYY-MM-DD" 또는 자유 텍스트
  ("예산 소진시까지" 등). 자유 텍스트는 해석하지 않고 보존한다.
- 지역: 지역 한정 공고는 해시태그에 해당 시도만, 전국 공고는 17개 시도가
  전부 나열됨 — 해시태그에서 지역 매핑표에 있는 토큰만 추출해 region으로
  쓰면 기존 region.v1 규칙이 그대로 동작한다.

원칙은 kstartup 정규화와 동일: 정보가 없으면 None, 해석 실패는 공고를
버리는 사유가 아니며 원본은 raw_data에 보존한다.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from grant_radar.models.announcement import (
    ApplicationMethod,
    DateField,
    NormalizedAnnouncement,
)

SOURCE = "bizinfo"

DEFAULT_REGION_MAPPING_PATH = Path("data") / "reference" / "region_mapping.json"

_DATE_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$")
_TAG_STRIP = re.compile(r"<[^>]+>")


class BizinfoNormalizationError(Exception):
    """응답 최상위 구조가 예상과 달라 정규화를 진행할 수 없음."""


def load_region_tokens(path: Path | None = None) -> frozenset[str]:
    """지역 매핑표의 모든 표현(정식 시도명·별칭·그룹)을 집합으로 만든다."""
    mapping = json.loads(Path(path or DEFAULT_REGION_MAPPING_PATH).read_text(encoding="utf-8"))
    return frozenset(
        [*mapping.get("canonical", []), *mapping.get("aliases", {}), *mapping.get("groups", {})]
    )


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    stripped = html.unescape(_TAG_STRIP.sub(" ", text))
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed or None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _period_fields(raw: str | None, issues: list[str]) -> tuple[DateField, DateField]:
    if not raw:
        return DateField(), DateField()
    match = _DATE_RANGE.match(raw.strip())
    if match:
        start_raw, end_raw = match.group(1), match.group(2)
        start = _parse_date(start_raw)
        end = _parse_date(end_raw)
        return (
            DateField(raw=start_raw, value=start, error=None if start else "날짜 해석 실패"),
            DateField(raw=end_raw, value=end, error=None if end else "날짜 해석 실패"),
        )
    # "예산 소진시까지", "모집 완료시" 등 — 해석하지 않고 보존 (마감 아님)
    issues.append(f"신청기간 표현 미해석: {raw!r}")
    return DateField(), DateField(raw=raw, value=None, error="기간 표현 미해석")


def _region_from_hashtags(hashtags: str | None, region_tokens: frozenset[str]) -> str | None:
    if not hashtags:
        return None
    seen: list[str] = []
    for token in (part.strip() for part in hashtags.split(",")):
        if token and token in region_tokens and token not in seen:
            seen.append(token)
    return ",".join(seen) or None


def normalize_bizinfo_item(
    item: dict,
    fetched_at: datetime | None = None,
    region_tokens: frozenset[str] | None = None,
) -> NormalizedAnnouncement:
    if region_tokens is None:
        region_tokens = load_region_tokens()
    issues: list[str] = []

    source_id = _clean(item.get("pblancId"))
    if source_id is None:
        issues.append("공고 ID(pblancId) 누락 — 저장·중복 방지 불가")

    start_field, end_field = _period_fields(_clean(item.get("reqstBeginEndDe")), issues)

    methods: list[ApplicationMethod] = []
    apply_url = _clean(item.get("rceptEngnHmpgUrl"))
    if apply_url:
        methods.append(ApplicationMethod(method="online", description=apply_url))
    method_text = _clean(item.get("reqstMthPapersCn"))
    if method_text:
        methods.append(ApplicationMethod(method="etc", description=method_text))

    target = _clean(item.get("trgetNm"))
    applicant_categories = (
        [token.strip() for token in target.split(",") if token.strip()] if target else []
    )

    return NormalizedAnnouncement(
        source=SOURCE,
        source_id=source_id,
        title=_clean(item.get("pblancNm")),
        summary=_strip_html(_clean(item.get("bsnsSumryCn"))),
        support_category=_clean(item.get("pldirSportRealmLclasCodeNm")),
        target_description=target,
        excluded_target_description=None,
        region=_region_from_hashtags(_clean(item.get("hashtags")), region_tokens),
        application_start_at=start_field,
        application_end_at=end_field,
        organization_name=_clean(item.get("excInsttNm")),
        supervising_organization=_clean(item.get("jrsdInsttNm")),
        contact_department=_clean(item.get("refrncNm")),
        contact_phone=None,
        guide_url=_clean(item.get("pblancUrl")),
        detail_url=_clean(item.get("pblancUrl")),
        application_methods=methods,
        business_age_conditions=[],
        applicant_age_conditions=[],
        applicant_categories=applicant_categories,
        preferred_conditions=None,
        recruitment_open=None,  # 모집 여부 필드 없음 — 종료일로만 마감 판정
        integrated_announcement=None,
        raw_data=item,
        fetched_at=fetched_at,
        issues=issues,
    )


def iter_items(body: Any) -> Iterator[dict]:
    """응답 본문에서 공고 항목을 순회한다.

    관찰된 구조: response.body.items.item = [ {...}, ... ].
    item이 단일 객체로 오는 경우(항목 1건)도 목록으로 취급한다.
    """
    if not isinstance(body, dict):
        raise BizinfoNormalizationError(f"응답 최상위가 객체가 아닙니다: {type(body).__name__}")
    response = body.get("response")
    if not isinstance(response, dict):
        raise BizinfoNormalizationError("응답에 response 객체가 없습니다.")
    inner = response.get("body")
    if not isinstance(inner, dict):
        raise BizinfoNormalizationError("응답에 response.body 객체가 없습니다.")
    items = inner.get("items")
    if items in (None, ""):
        return
    if isinstance(items, dict):
        items = items.get("item")
    if items in (None, ""):
        return
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise BizinfoNormalizationError(f"items 형태를 해석할 수 없습니다: {type(items).__name__}")
    for entry in items:
        if isinstance(entry, dict):
            yield entry


def normalize_bizinfo_page(
    body: Any,
    fetched_at: datetime | None = None,
    region_tokens: frozenset[str] | None = None,
) -> list[NormalizedAnnouncement]:
    if region_tokens is None:
        region_tokens = load_region_tokens()
    return [normalize_bizinfo_item(item, fetched_at, region_tokens) for item in iter_items(body)]
