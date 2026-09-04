"""회사 모델과 로더.

가상회사와 실제 회사 데이터를 모두 지원한다. `is_fictional`을 true/false로 반드시 명시해야
하며, 실제 회사 데이터 파일은 Git에 커밋하지 않는다 (.gitignore 유지).

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
    # 사업장 소재 기초지자체(자치구·시군). 자치구 한정 공고의 관련도 판단에 쓴다
    business_districts: list[str] = field(default_factory=list)
    industry_codes: list[str] = field(default_factory=list)
    industry_names: list[str] = field(default_factory=list)
    business_categories: list[str] = field(default_factory=list)
    employee_count: int | None = None
    # 중소기업 해당 여부. 법인/개인사업자라는 사실만으로 중소기업으로 간주하지 않는다.
    sme: bool | None = None
    # 소상공인(소기업 중 업종별 상시근로자 기준 미만) 해당 여부. 상시근로자 산정과
    # 업종별 기준이 회사 데이터만으로 기계적으로 결정되지 않으므로 확인된 값을
    # 명시하게 한다. None = 확인 안 됨 → 소상공인 전용 공고는 REVIEW로 남는다.
    small_business: bool | None = None
    representative_birth_date: date | None = None
    # 생년월일 대신 쓸 수 있는 대표자 만 나이 (data_as_of 시점 값). 개인정보를
    # 줄이기 위한 대안이며, 둘 다 있으면 생년월일을 우선한다.
    representative_age: int | None = None
    certifications: list[str] = field(default_factory=list)
    research_institute: bool | None = None
    export_experience: bool | None = None
    revenue: Any = None
    tax_arrears: bool | None = None
    support_history: list = field(default_factory=list)
    # 공고 매칭에 사용할 회사별 명시 설정. 값이 없으면 추정하지 않는다.
    matching_keywords: list[str] = field(default_factory=list)
    interest_keywords: dict[str, list[str]] = field(default_factory=dict)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_categories: list[str] = field(default_factory=list)
    demote_keywords: list[str] = field(default_factory=list)
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


def _parse_optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise CompanyDataError(f"{field_name}은(는) true/false 또는 null이어야 합니다: {value!r}")
    return value


def _parse_optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompanyDataError(f"{field_name}은(는) 0 이상의 정수여야 합니다: {value!r}")
    return value


def _str_list_map(value: Any, field_name: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CompanyDataError(f"{field_name}은(는) 문자열 목록을 값으로 갖는 객체여야 합니다.")
    return {str(key): _str_list(items) for key, items in value.items()}


def company_from_dict(data: dict) -> Company:
    is_fictional = data.get("is_fictional")
    if not isinstance(is_fictional, bool):
        raise CompanyDataError(
            "회사 데이터에 is_fictional을 true/false로 명시해야 합니다. "
            "실제 회사 데이터는 is_fictional=false로 표기하고 Git에 커밋하지 않습니다."
        )
    company_id = data.get("company_id")
    name = data.get("name")
    if not company_id or not name:
        raise CompanyDataError("회사 데이터에 company_id와 name이 필요합니다.")
    return Company(
        company_id=str(company_id),
        name=str(name),
        is_fictional=is_fictional,
        business_type=data.get("business_type"),
        established_date=_parse_date(data.get("established_date"), "established_date"),
        headquarters_region=data.get("headquarters_region"),
        business_locations=_str_list(data.get("business_locations")),
        business_districts=_str_list(data.get("business_districts")),
        industry_codes=_str_list(data.get("industry_codes")),
        industry_names=_str_list(data.get("industry_names")),
        business_categories=_str_list(data.get("business_categories")),
        employee_count=data.get("employee_count"),
        sme=_parse_optional_bool(data.get("sme"), "sme"),
        small_business=_parse_optional_bool(data.get("small_business"), "small_business"),
        representative_birth_date=_parse_date(
            data.get("representative_birth_date"), "representative_birth_date"
        ),
        representative_age=_parse_optional_int(
            data.get("representative_age"), "representative_age"
        ),
        certifications=_str_list(data.get("certifications")),
        research_institute=data.get("research_institute"),
        export_experience=data.get("export_experience"),
        revenue=data.get("revenue"),
        tax_arrears=data.get("tax_arrears"),
        support_history=data.get("support_history") or [],
        matching_keywords=_str_list(data.get("matching_keywords")),
        interest_keywords=_str_list_map(data.get("interest_keywords"), "interest_keywords"),
        exclude_keywords=_str_list(data.get("exclude_keywords")),
        exclude_categories=_str_list(data.get("exclude_categories")),
        demote_keywords=_str_list(data.get("demote_keywords")),
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
