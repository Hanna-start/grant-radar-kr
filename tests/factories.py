"""테스트 공용 팩토리. 전부 가상 데이터다."""

from datetime import date

from grant_radar.models.company import Company
from grant_radar.normalization.kstartup import normalize_announcement

from tests.test_normalization import full_item


def make_company(**overrides) -> Company:
    defaults = dict(
        company_id="sample-tech-001",
        name="샘플테크 주식회사",
        is_fictional=True,
        business_type="corporation",
        established_date=date(2022, 3, 15),
        headquarters_region="서울특별시",
        business_locations=["서울특별시"],
        industry_codes=["J58222"],
        industry_names=["응용 소프트웨어 개발 및 공급업"],
        business_categories=["정보통신업"],
        employee_count=12,
    )
    defaults.update(overrides)
    return Company(**defaults)


def make_announcement(**overrides):
    return normalize_announcement(full_item(**overrides))
