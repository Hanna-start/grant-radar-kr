"""정규화 테스트.

가상 공고 데이터를 사용한다. 구조는 실제 응답 관찰
(docs/api-observations.md, 2026-07-21)을 그대로 본떴다.
"""

from datetime import date, datetime, timezone

import pytest

from grant_radar.normalization.kstartup import (
    NormalizationError,
    normalize_announcement,
    normalize_page,
)

FETCHED_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def full_item(**overrides):
    """실제 응답 구조(30개 필드)를 본뜬 가상 공고 항목."""
    item = {
        "aply_excl_trgt_ctnt": "세금 체납 중인 자",
        "aply_mthd_eml_rcpt_istc": None,
        "aply_mthd_etc_istc": None,
        "aply_mthd_fax_rcpt_istc": None,
        "aply_mthd_onli_rcpt_istc": "https://example.test/apply?id=1",
        "aply_mthd_pssr_rcpt_istc": None,
        "aply_mthd_vst_rcpt_istc": None,
        "aply_trgt": "일반인,일반기업",
        "aply_trgt_ctnt": "공고일 기준 창업 7년 이내 기업",
        "biz_aply_url": None,
        "biz_enyy": "예비창업자,1년미만,2년미만,3년미만",
        "biz_gdnc_url": "https://example.test/guide",
        "biz_pbanc_nm": "2026년 가상 창업지원사업 모집공고",
        "biz_prch_dprt_nm": "가상팀",
        "biz_trgt_age": "만 20세 이상 ~ 만 39세 이하,만 40세 이상",
        "detl_pg_url": "https://example.test/view?pbancSn=900001",
        "id": 1,
        "intg_pbanc_biz_nm": "가상 통합공고 사업",
        "intg_pbanc_yn": "N",
        "pbanc_ctnt": "가상 공고 내용입니다.",
        "pbanc_ntrp_nm": "가상진흥원",
        "pbanc_rcpt_bgng_dt": "20260701",
        "pbanc_rcpt_end_dt": "20260731",
        "pbanc_sn": 900001,
        "prch_cnpl_no": "0000000000",
        "prfn_matr": None,
        "rcrt_prgs_yn": "Y",
        "sprv_inst": "공공기관",
        "supt_biz_clsfc": "사업화",
        "supt_regin": "서울",
    }
    item.update(overrides)
    return item


class TestFullItem:
    def test_normal_full_item(self):
        ann = normalize_announcement(full_item(), FETCHED_AT)
        assert ann.source == "kstartup"
        assert ann.source_id == "900001"  # 숫자 → 문자열 통일
        assert ann.title == "2026년 가상 창업지원사업 모집공고"
        assert ann.summary == "가상 공고 내용입니다."
        assert ann.support_category == "사업화"
        assert ann.target_description == "공고일 기준 창업 7년 이내 기업"
        assert ann.excluded_target_description == "세금 체납 중인 자"
        assert ann.region == "서울"
        assert ann.application_start_at.value == date(2026, 7, 1)
        assert ann.application_start_at.raw == "20260701"
        assert ann.application_start_at.error is None
        assert ann.application_end_at.value == date(2026, 7, 31)
        assert ann.organization_name == "가상진흥원"
        assert ann.supervising_organization == "공공기관"
        assert ann.contact_department == "가상팀"
        assert ann.contact_phone == "0000000000"
        assert ann.guide_url == "https://example.test/guide"
        assert ann.detail_url == "https://example.test/view?pbancSn=900001"
        assert [m.method for m in ann.application_methods] == ["online"]
        assert ann.business_age_conditions == ["예비창업자", "1년미만", "2년미만", "3년미만"]
        assert ann.applicant_age_conditions == ["만 20세 이상 ~ 만 39세 이하", "만 40세 이상"]
        assert ann.preferred_conditions is None
        assert ann.recruitment_open is True
        assert ann.integrated_announcement is False
        assert ann.fetched_at == FETCHED_AT
        assert ann.issues == []

    def test_raw_data_preserves_unknown_fields(self):
        item = full_item(unknown_new_field="값", biz_aply_url="something")
        ann = normalize_announcement(item)
        assert ann.raw_data["unknown_new_field"] == "값"
        assert ann.raw_data["biz_aply_url"] == "something"
        assert ann.raw_data == item


class TestMissingAndEmpty:
    def test_missing_fields_do_not_crash(self):
        ann = normalize_announcement({"pbanc_sn": 1})
        assert ann.source_id == "1"
        assert ann.title is None
        assert ann.region is None
        assert ann.application_methods == []
        assert ann.business_age_conditions == []
        assert ann.application_start_at.raw is None
        assert ann.application_start_at.error is None
        assert ann.recruitment_open is None

    def test_null_values_become_none_or_empty_list(self):
        ann = normalize_announcement(full_item(supt_regin=None, biz_enyy=None, rcrt_prgs_yn=None))
        assert ann.region is None
        assert ann.business_age_conditions == []
        assert ann.recruitment_open is None
        assert ann.issues == []  # null은 특이사항이 아니라 정보 부족

    def test_empty_strings_become_none_not_empty_string(self):
        ann = normalize_announcement(full_item(supt_regin="", biz_pbanc_nm="   ", biz_enyy=" , , "))
        assert ann.region is None
        assert ann.title is None
        assert ann.business_age_conditions == []

    def test_missing_source_id_recorded_as_issue(self):
        item = full_item()
        del item["pbanc_sn"]
        ann = normalize_announcement(item)
        assert ann.source_id is None
        assert any("pbanc_sn" in issue for issue in ann.issues)


class TestFieldNameVariants:
    def test_uppercase_field_names_are_recognized(self):
        item = {
            "Rcrt_prgs_yn": "N",  # 공식 가이드가 경고한 표기 변형
            "PBANC_SN": 777,
            "Supt_Regin": "부산",
        }
        ann = normalize_announcement(item)
        assert ann.recruitment_open is False
        assert ann.source_id == "777"
        assert ann.region == "부산"

    def test_excluded_target_guide_spelling_alias(self):
        item = full_item()
        del item["aply_excl_trgt_ctnt"]
        item["aply_exclt_trgt_ctnt"] = "가이드 표기 제외 대상"
        ann = normalize_announcement(item)
        assert ann.excluded_target_description == "가이드 표기 제외 대상"

    def test_observed_spelling_wins_over_guide_spelling(self):
        item = full_item(aply_exclt_trgt_ctnt="가이드 표기")
        ann = normalize_announcement(item)
        assert ann.excluded_target_description == "세금 체납 중인 자"


class TestDates:
    def test_invalid_date_preserved_with_error(self):
        ann = normalize_announcement(full_item(pbanc_rcpt_end_dt="미정"))
        end = ann.application_end_at
        assert end.value is None
        assert end.raw == "미정"
        assert end.error is not None

    def test_impossible_date_preserved_with_error(self):
        ann = normalize_announcement(full_item(pbanc_rcpt_end_dt="20261340"))
        assert ann.application_end_at.value is None
        assert ann.application_end_at.raw == "20261340"
        assert ann.application_end_at.error is not None

    def test_hyphenated_date_accepted(self):
        ann = normalize_announcement(full_item(pbanc_rcpt_bgng_dt="2026-07-01"))
        assert ann.application_start_at.value == date(2026, 7, 1)

    def test_date_parse_failure_does_not_drop_announcement(self):
        ann = normalize_announcement(full_item(pbanc_rcpt_bgng_dt="??", pbanc_rcpt_end_dt="??"))
        assert ann.title is not None  # 나머지 필드는 정상 변환


class TestUrls:
    def test_url_without_protocol_gets_https(self):
        ann = normalize_announcement(full_item(biz_gdnc_url="www.example.test/guide"))
        assert ann.guide_url == "https://www.example.test/guide"
        assert any("biz_gdnc_url" in issue for issue in ann.issues)

    def test_url_with_protocol_unchanged(self):
        ann = normalize_announcement(full_item())
        assert ann.guide_url == "https://example.test/guide"
        assert ann.issues == []


class TestValues:
    def test_unknown_yn_value_recorded(self):
        ann = normalize_announcement(full_item(rcrt_prgs_yn="예"))
        assert ann.recruitment_open is None
        assert any("rcrt_prgs_yn" in issue for issue in ann.issues)

    def test_html_entities_unescaped(self):
        ann = normalize_announcement(full_item(aply_trgt_ctnt="공고일(&apos;26. 5. 22.) 기준"))
        assert ann.target_description == "공고일('26. 5. 22.) 기준"

    def test_email_method_value_kept_verbatim(self):
        encoded = "AAAA+BBBB/CCCC=="  # 실제 응답에서 관찰된 base64 형태 (가짜 값)
        ann = normalize_announcement(full_item(aply_mthd_eml_rcpt_istc=encoded))
        emails = [m for m in ann.application_methods if m.method == "email"]
        assert emails[0].description == encoded


class TestNormalizePage:
    def envelope(self, items):
        return {
            "currentCount": len(items),
            "matchCount": 29505,
            "page": 1,
            "perPage": len(items),
            "totalCount": 29505,
            "data": items,
        }

    def test_normal_page(self):
        results = normalize_page(
            self.envelope([full_item(), full_item(pbanc_sn=900002)]), FETCHED_AT
        )
        assert [a.source_id for a in results] == ["900001", "900002"]
        assert all(a.fetched_at == FETCHED_AT for a in results)

    def test_non_dict_entry_preserved_not_dropped(self):
        results = normalize_page(self.envelope([full_item(), "corrupted"]))
        assert len(results) == 2
        broken = results[1]
        assert broken.source_id is None
        assert broken.raw_data == {"_raw": "corrupted"}
        assert broken.issues

    def test_bare_list_body_accepted(self):
        results = normalize_page([full_item()])
        assert len(results) == 1

    def test_missing_data_list_raises(self):
        with pytest.raises(NormalizationError):
            normalize_page({"unexpected": True})
        with pytest.raises(NormalizationError):
            normalize_page("완전히 다른 구조")
