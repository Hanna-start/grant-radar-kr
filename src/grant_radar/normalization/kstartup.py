"""K-Startup 응답 정규화.

실제 응답 관찰(docs/api-observations.md, 2026-07-21) 기준으로 변환한다.

원칙:
- 정보가 없으면 None/빈 목록으로 표현한다 (빈 문자열로 바꾸지 않는다).
- 날짜 파싱 실패는 공고를 버리는 사유가 아니다. 원본과 오류를 함께 보존한다.
- 항목 하나의 문제로 페이지 전체 처리가 중단되지 않는다.
- 알 수 없는 필드는 raw_data에 그대로 남긴다.
- 필드명 표기 변형(대소문자, 철자)은 소문자 매핑과 별칭 목록으로 흡수한다.
- 텍스트의 HTML 엔티티(&apos; 등)는 해제한다. 원문은 raw_data에 남는다.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

from grant_radar.models.announcement import (
    ApplicationMethod,
    DateField,
    NormalizedAnnouncement,
)

SOURCE = "kstartup"

# (정규화 방법, 우선순위 순 필드명 별칭들). 전부 소문자 기준.
# aply_excl_trgt_ctnt: 실제 관찰 표기. aply_exclt_trgt_ctnt: 공식 가이드 표기 후보.
EXCLUDED_TARGET_ALIASES = ("aply_excl_trgt_ctnt", "aply_exclt_trgt_ctnt")

APPLICATION_METHOD_FIELDS = (
    ("online", "aply_mthd_onli_rcpt_istc"),
    ("visit", "aply_mthd_vst_rcpt_istc"),
    ("postal", "aply_mthd_pssr_rcpt_istc"),
    ("fax", "aply_mthd_fax_rcpt_istc"),
    ("email", "aply_mthd_eml_rcpt_istc"),
    ("etc", "aply_mthd_etc_istc"),
)

# 실제 관찰: "20260720". 하이픈 형식은 방어적으로만 허용.
DATE_PATTERNS = ("%Y%m%d", "%Y-%m-%d")


class NormalizationError(Exception):
    """응답 최상위 구조가 예상과 달라 공고 목록을 찾을 수 없음."""


def _text(value: Any) -> str | None:
    """텍스트 값 정리. 빈 값(None/공백)은 None으로 통일한다."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = html.unescape(value).strip()
    return cleaned if cleaned else None


def _comma_list(value: Any) -> list[str]:
    """쉼표 구분 다중 값(biz_enyy 등 관찰됨)을 목록으로 변환한다."""
    text = _text(value)
    if text is None:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _yes_no(value: Any, field_name: str, issues: list[str]) -> bool | None:
    text = _text(value)
    if text is None:
        return None
    upper = text.upper()
    if upper == "Y":
        return True
    if upper == "N":
        return False
    issues.append(f"{field_name} 값 {text!r}를 Y/N으로 해석할 수 없습니다.")
    return None


def _date_field(value: Any) -> DateField:
    if value is None:
        return DateField()
    raw = str(value).strip()
    if not raw:
        return DateField()
    for pattern in DATE_PATTERNS:
        try:
            parsed = datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
        return DateField(raw=raw, value=parsed)
    return DateField(raw=raw, error=f"날짜 형식을 해석할 수 없습니다: {raw!r}")


def _url(value: Any, field_name: str, issues: list[str]) -> str | None:
    """URL 정리. 스킴이 없으면 https://를 보충한다 (관찰: biz_gdnc_url 등)."""
    text = _text(value)
    if text is None:
        return None
    if re.match(r"^https?://", text, re.IGNORECASE):
        return text
    if "://" in text:
        issues.append(f"{field_name}에 알 수 없는 URL 스킴이 있습니다: {text[:80]!r}")
        return text
    issues.append(f"{field_name}에 프로토콜이 없어 https://를 보충했습니다.")
    return "https://" + text


def _source_id(value: Any, issues: list[str]) -> str | None:
    """공고 일련번호. 실제 응답에서 숫자 타입으로 관찰되어 문자열로 통일한다."""
    if value is None:
        issues.append("공고 일련번호(pbanc_sn)가 없습니다.")
        return None
    text = str(value).strip()
    if not text:
        issues.append("공고 일련번호(pbanc_sn)가 비어 있습니다.")
        return None
    return text


def normalize_announcement(
    item: dict, fetched_at: datetime | None = None
) -> NormalizedAnnouncement:
    """응답 항목 하나를 내부 모델로 변환한다.

    필드가 누락되거나 값이 이상해도 예외를 던지지 않고, 특이사항을
    issues에 기록한 채 변환을 계속한다.
    """
    low = {str(key).lower(): value for key, value in item.items()}

    def get(*names: str) -> Any:
        for name in names:
            if name in low:
                return low[name]
        return None

    issues: list[str] = []

    methods = []
    for method, field_name in APPLICATION_METHOD_FIELDS:
        description = _text(get(field_name))
        if description is not None:
            methods.append(ApplicationMethod(method=method, description=description))

    return NormalizedAnnouncement(
        source=SOURCE,
        source_id=_source_id(get("pbanc_sn"), issues),
        title=_text(get("biz_pbanc_nm")),
        summary=_text(get("pbanc_ctnt")),
        support_category=_text(get("supt_biz_clsfc")),
        target_description=_text(get("aply_trgt_ctnt")),
        excluded_target_description=_text(get(*EXCLUDED_TARGET_ALIASES)),
        region=_text(get("supt_regin")),
        application_start_at=_date_field(get("pbanc_rcpt_bgng_dt")),
        application_end_at=_date_field(get("pbanc_rcpt_end_dt")),
        organization_name=_text(get("pbanc_ntrp_nm")),
        supervising_organization=_text(get("sprv_inst")),
        contact_department=_text(get("biz_prch_dprt_nm")),
        contact_phone=_text(get("prch_cnpl_no")),
        guide_url=_url(get("biz_gdnc_url"), "biz_gdnc_url", issues),
        detail_url=_url(get("detl_pg_url"), "detl_pg_url", issues),
        application_methods=methods,
        business_age_conditions=_comma_list(get("biz_enyy")),
        applicant_age_conditions=_comma_list(get("biz_trgt_age")),
        applicant_categories=_comma_list(get("aply_trgt")),
        preferred_conditions=_text(get("prfn_matr")),
        recruitment_open=_yes_no(get("rcrt_prgs_yn"), "rcrt_prgs_yn", issues),
        integrated_announcement=_yes_no(get("intg_pbanc_yn"), "intg_pbanc_yn", issues),
        raw_data=dict(item),
        fetched_at=fetched_at,
        issues=issues,
    )


def normalize_page(body: Any, fetched_at: datetime | None = None) -> list[NormalizedAnnouncement]:
    """응답 최상위 객체에서 공고 목록(data)을 꺼내 항목 단위로 정규화한다.

    항목 하나가 객체가 아니어도 전체를 중단하지 않고, 해당 항목을
    issues가 기록된 빈 공고로 보존한다.
    """
    if isinstance(body, dict):
        items = body.get("data")
    elif isinstance(body, list):
        items = body
    else:
        items = None
    if not isinstance(items, list):
        raise NormalizationError(
            f"응답에서 공고 목록(data)을 찾을 수 없습니다. 최상위 타입: {type(body).__name__}"
        )

    results: list[NormalizedAnnouncement] = []
    for index, entry in enumerate(items):
        if isinstance(entry, dict):
            results.append(normalize_announcement(entry, fetched_at))
        else:
            results.append(
                NormalizedAnnouncement(
                    source=SOURCE,
                    source_id=None,
                    title=None,
                    summary=None,
                    support_category=None,
                    target_description=None,
                    excluded_target_description=None,
                    region=None,
                    application_start_at=DateField(),
                    application_end_at=DateField(),
                    organization_name=None,
                    supervising_organization=None,
                    contact_department=None,
                    contact_phone=None,
                    guide_url=None,
                    detail_url=None,
                    application_methods=[],
                    business_age_conditions=[],
                    applicant_age_conditions=[],
                    applicant_categories=[],
                    preferred_conditions=None,
                    recruitment_open=None,
                    integrated_announcement=None,
                    raw_data={"_raw": entry},
                    fetched_at=fetched_at,
                    issues=[f"data[{index}] 항목이 객체가 아니어서 해석할 수 없습니다."],
                )
            )
    return results
