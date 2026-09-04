"""기업마당(bizinfo) 정규화 테스트. 실제 관찰(2026-08-21) 표본 형태 기반."""

from pathlib import Path

import pytest

from grant_radar.normalization.bizinfo import (
    BizinfoNormalizationError,
    iter_items,
    load_region_tokens,
    normalize_bizinfo_item,
    normalize_bizinfo_page,
)
from grant_radar.services.ingestion import KST, is_closed

REGION_TOKENS = load_region_tokens(
    Path(__file__).parent.parent / "data" / "reference" / "region_mapping.json"
)

ALL_SIDO_TAGS = (
    "서울,부산,대구,인천,전남광주,대전,울산,세종,경기,강원,충북,충남,전북,경북,경남,제주"
)


def full_bizinfo_item(**overrides):
    """실제 응답(20개 필드)을 본뜬 가상 공고 항목."""
    item = {
        "pblancNm": "[울산] 가상 지원사업 수요기업 모집 공고",
        "pblancUrl": "https://example.test/siia/selectSIIA200Detail.do?pblancId=PBLN_1",
        "pblancId": "PBLN_000000000000001",
        "jrsdInsttNm": "울산광역시",
        "excInsttNm": "가상테크노파크",
        "bsnsSumryCn": "<p>가상 <b>사업</b> 개요&nbsp;내용</p>",
        "pldirSportRealmLclasCodeNm": "기술",
        "creatPnttm": "2026-08-20 14:28:22",
        "reqstBeginEndDe": "2026-08-20 ~ 2026-09-02",
        "updtPnttm": "2026-08-20 15:10:45",
        "trgetNm": "중소기업",
        "inqireCo": 10,
        "flpthNm": "https://example.test/file?atchFileId=F1&fileSn=0",
        "fileNm": "붙임.hwp",
        "printFlpthNm": "https://example.test/file?atchFileId=F2&fileSn=0",
        "printFileNm": "공고.hwp",
        "hashtags": "기술,경영,울산,2026,울산광역시,중소기업",
        "reqstMthPapersCn": "이메일 접수",
        "refrncNm": "가상테크노파크 000-000-0000",
        "rceptEngnHmpgUrl": None,
    }
    item.update(overrides)
    return item


def normalize(**overrides):
    return normalize_bizinfo_item(full_bizinfo_item(**overrides), region_tokens=REGION_TOKENS)


def test_normal_item_maps_fields():
    ann = normalize()
    assert ann.source == "bizinfo"
    assert ann.source_id == "PBLN_000000000000001"
    assert ann.title.startswith("[울산]")
    assert ann.summary == "가상 사업 개요 내용"  # HTML 태그·엔티티 제거
    assert ann.support_category == "기술"
    assert ann.target_description == "중소기업"
    assert ann.applicant_categories == ["중소기업"]
    assert ann.application_start_at.value is not None
    assert ann.application_end_at.value is not None
    assert ann.application_end_at.raw == "2026-09-02"
    assert ann.detail_url == ann.guide_url
    assert ann.recruitment_open is None
    assert ann.issues == []


def test_region_extracts_only_mapped_tokens():
    ann = normalize()
    # 해시태그 중 지역 매핑표에 있는 것만, 순서 유지
    assert ann.region == "울산,울산광역시"


def test_nationwide_lists_all_sido():
    ann = normalize(hashtags=ALL_SIDO_TAGS + ",중소기업")
    assert "서울" in ann.region.split(",")
    assert len(ann.region.split(",")) == 16  # 17개 시도가 전남광주 결합 표기로 16토큰


def test_no_region_hashtags_gives_none():
    ann = normalize(hashtags="기술,경영,2026")
    assert ann.region is None


def test_unparsed_period_is_preserved_and_not_closed():
    ann = normalize(reqstBeginEndDe="예산 소진시까지")
    assert ann.application_end_at.value is None
    assert ann.application_end_at.raw == "예산 소진시까지"
    assert any("미해석" in issue for issue in ann.issues)
    from datetime import datetime

    assert is_closed(ann, datetime(2026, 12, 31, tzinfo=KST)) is False


def test_missing_id_recorded_as_issue():
    ann = normalize(pblancId=None)
    assert ann.source_id is None
    assert any("pblancId" in issue for issue in ann.issues)


def test_apply_url_becomes_online_method():
    ann = normalize(rceptEngnHmpgUrl="https://example.test/apply")
    assert ("online", "https://example.test/apply") in [
        (m.method, m.description) for m in ann.application_methods
    ]


def make_page(items):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {"items": {"item": items}, "numOfRows": 10, "pageNo": 1, "totalCount": 2},
        }
    }


def test_page_normalizes_list():
    page = make_page([full_bizinfo_item(), full_bizinfo_item(pblancId="PBLN_2")])
    assert len(normalize_bizinfo_page(page, region_tokens=REGION_TOKENS)) == 2


def test_single_item_dict_is_wrapped():
    page = make_page(full_bizinfo_item())
    assert len(normalize_bizinfo_page(page, region_tokens=REGION_TOKENS)) == 1


def test_empty_items_yields_nothing():
    page = make_page([])
    assert normalize_bizinfo_page(page, region_tokens=REGION_TOKENS) == []


def test_bad_structure_raises_clear_error():
    with pytest.raises(BizinfoNormalizationError):
        list(iter_items({"unexpected": True}))
