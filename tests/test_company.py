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


def test_real_company_accepted_with_explicit_false_flag():
    # 실제 회사 데이터는 is_fictional=false 명시로 허용
    company = company_from_dict(sample_dict(is_fictional=False))
    assert company.is_fictional is False


def test_missing_fictional_flag_rejected():
    data = sample_dict()
    del data["is_fictional"]
    with pytest.raises(CompanyDataError) as exc_info:
        company_from_dict(data)
    assert "is_fictional" in str(exc_info.value)


def test_non_bool_fictional_flag_rejected():
    with pytest.raises(CompanyDataError):
        company_from_dict(sample_dict(is_fictional="true"))


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


def test_representative_age_parsed():
    company = company_from_dict(sample_dict(representative_age=35))
    assert company.representative_age == 35


def test_representative_age_rejects_non_integer():
    with pytest.raises(CompanyDataError):
        company_from_dict(sample_dict(representative_age="서른다섯"))


def test_small_business_defaults_to_unknown():
    # 명시가 없으면 None — 소상공인 전용 공고는 '판단 필요'로 남는다
    assert company_from_dict(sample_dict()).small_business is None


def test_small_business_parsed():
    assert company_from_dict(sample_dict(small_business=True)).small_business is True
    assert company_from_dict(sample_dict(small_business=False)).small_business is False


def test_small_business_rejects_non_bool():
    with pytest.raises(CompanyDataError) as exc_info:
        company_from_dict(sample_dict(small_business="예"))
    assert "small_business" in str(exc_info.value)
