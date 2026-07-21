"""가상회사 모델과 로더.

초기 버전은 가상회사 데이터만 사용한다. 로더는 `is_fictional: true`가 아닌
데이터를 거부한다 (실제 기업정보 사용 금지 원칙).

null 값은 정보 부족을 뜻하며, 조건 불충족으로 해석하지 않는다 (지시서 13절).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class CompanyDataError(Exception):
    """회사 데이터 형식 오류 또는 사용 불가 데이터."""


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    is_fictional: bool
    business_type: str | None  # "corporation" | "individual" 등. None = 정보 부족
    established_date: date | None
    headquarters_region: str | None
    business_locations: list[str] = field(default_factory=list)
    industry_codes: list[str] = field(default_factory=list)
    industry_names: list[str] = field(default_factory=list)
    business_categories: list[str] = field(default_factory=list)
    employee_count: int | None = None
    representative_birth_date: date | None = None
    certifications: list[str] = field(default_factory=list)
    research_institute: bool | None = None
    export_experience: bool | None = None
    revenue: Any = None
    tax_arrears: bool | None = None
    support_history: list = field(default_factory=list)
    data_as_of: date | None = None


def _parse_date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise CompanyDataError(
            f"회사 데이터의 {field_name} 날짜 형식이 잘못되었습니다: {value!r} (YYYY-MM-DD 필요)"
        ) from None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CompanyDataError(f"목록이어야 하는 값이 목록이 아닙니다: {value!r}")
    return [str(item) for item in value]


def company_from_dict(data: dict) -> Company:
    if data.get("is_fictional") is not True:
        raise CompanyDataError(
            "is_fictional=true가 아닌 회사 데이터는 사용할 수 없습니다. "
            "실제 기업정보의 수집·판정은 현재 단계에서 수행하지 않습니다."
        )
    company_id = data.get("company_id")
    name = data.get("name")
    if not company_id or not name:
        raise CompanyDataError("회사 데이터에 company_id와 name이 필요합니다.")
    return Company(
        company_id=str(company_id),
        name=str(name),
        is_fictional=True,
        business_type=data.get("business_type"),
        established_date=_parse_date(data.get("established_date"), "established_date"),
        headquarters_region=data.get("headquarters_region"),
        business_locations=_str_list(data.get("business_locations")),
        industry_codes=_str_list(data.get("industry_codes")),
        industry_names=_str_list(data.get("industry_names")),
        business_categories=_str_list(data.get("business_categories")),
        employee_count=data.get("employee_count"),
        representative_birth_date=_parse_date(
            data.get("representative_birth_date"), "representative_birth_date"
        ),
        certifications=_str_list(data.get("certifications")),
        research_institute=data.get("research_institute"),
        export_experience=data.get("export_experience"),
        revenue=data.get("revenue"),
        tax_arrears=data.get("tax_arrears"),
        support_history=data.get("support_history") or [],
        data_as_of=_parse_date(data.get("data_as_of"), "data_as_of"),
    )


def load_company(path: str | Path) -> Company:
    file = Path(path)
    if not file.is_file():
        raise CompanyDataError(f"회사 데이터 파일을 찾을 수 없습니다: {file}")
    try:
        data = json.loads(file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CompanyDataError(f"회사 데이터 JSON 파싱 실패: {exc.msg}") from None
    if not isinstance(data, dict):
        raise CompanyDataError("회사 데이터는 JSON 객체여야 합니다.")
    return company_from_dict(data)
