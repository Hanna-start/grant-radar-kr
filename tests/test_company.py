"""가상회사 모델 로더 테스트."""

import json
from datetime import date
from pathlib import Path

import pytest

from grant_radar.models.company import CompanyDataError, company_from_dict, load_company

SAMPLE_PATH = Path(__file__).parent.parent / "data" / "sample_company.json"


def sample_dict(**overrides):
    data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.update(overrides)
    return data


def test_load_repo_sample_company():
    company = load_company(SAMPLE_PATH)
    assert company.company_id == "sample-tech-001"
    assert company.is_fictional is True
    assert company.business_type == "corporation"
    assert company.established_date == date(2022, 3, 15)
    assert company.headquarters_region == "서울특별시"
    assert company.employee_count == 12
    assert company.representative_birth_date is None  # null = 정보 부족
    assert company.revenue is None


def test_non_fictional_company_rejected():
    with pytest.raises(CompanyDataError) as exc_info:
        company_from_dict(sample_dict(is_fictional=False))
    assert "실제 기업정보" in str(exc_info.value)


def test_missing_fictional_flag_rejected():
    data = sample_dict()
    del data["is_fictional"]
    with pytest.raises(CompanyDataError):
        company_from_dict(data)


def test_invalid_date_rejected_with_field_name():
    with pytest.raises(CompanyDataError) as exc_info:
        company_from_dict(sample_dict(established_date="2022/03/15"))
    assert "established_date" in str(exc_info.value)


def test_missing_required_fields_rejected():
    with pytest.raises(CompanyDataError):
        company_from_dict(sample_dict(name=None))


def test_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(CompanyDataError) as exc_info:
        load_company(tmp_path / "none.json")
    assert "찾을 수 없습니다" in str(exc_info.value)
