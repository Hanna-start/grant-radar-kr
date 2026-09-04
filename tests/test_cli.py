"""CLI(__main__) 테스트. 실제 API를 호출하지 않는다."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from grant_radar.__main__ import main, run_fetch, save_raw_result, summarize_top_level
from grant_radar.api.bizinfo import BizinfoClient
from grant_radar.api.kstartup import FetchResult, KStartupClient
from grant_radar.config import API_KEY_ENV_VAR, MISSING_KEY_MESSAGE

FAKE_KEY = "fake-cli-test-key"


def make_factory(handler):
    """MockTransport를 주입하는 KStartupClient 팩토리를 만든다."""

    def factory(api_key):
        return KStartupClient(api_key, transport=httpx.MockTransport(handler), retry_wait=0.0)

    return factory


def make_bizinfo_factory(handler):
    def factory(api_key):
        return BizinfoClient(api_key, transport=httpx.MockTransport(handler), retry_wait=0.0)

    return factory


def bizinfo_page(items, total=None):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "items": {"item": items},
                "numOfRows": len(items),
                "pageNo": 1,
                "totalCount": total if total is not None else len(items),
            },
        }
    }


def fetch_args(**overrides):
    defaults = {"page": 1, "per_page": 5, "no_save": False}
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def make_result(data, page=1, per_page=5):
    return FetchResult(
        page=page,
        per_page=per_page,
        status_code=200,
        data=data,
        raw_text=json.dumps(data, ensure_ascii=False),
        fetched_at=datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_fetch_without_key_prints_guide(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)  # .env가 없는 빈 디렉터리
    exit_code = main(["fetch"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert MISSING_KEY_MESSAGE in captured.err


def test_unknown_command_exits_with_error():
    with pytest.raises(SystemExit):
        main(["no-such-command"])


def test_run_fetch_success_saves_and_summarizes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)

    def handler(request):
        return httpx.Response(
            200, json={"currentCount": 1, "data": [{"pbanc_sn": "1", "biz_pbanc_nm": "공고"}]}
        )

    exit_code = run_fetch(fetch_args(), client_factory=make_factory(handler))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[성공]" in captured.out
    assert "data 항목 수: 1" in captured.out
    assert "[수집] 신규 1건" in captured.out
    saved = list((tmp_path / "data" / "raw").glob("*.json"))
    assert len(saved) == 1
    saved_text = saved[0].read_text(encoding="utf-8")
    assert FAKE_KEY not in saved_text
    assert "ServiceKey" not in saved_text
    assert (tmp_path / "data" / "announcements.db").exists()


def test_run_fetch_twice_reports_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"data": [{"pbanc_sn": 1, "biz_pbanc_nm": "공고"}]})

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()
    exit_code = run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "동일 1건" in captured.out
    assert "저장소 누적 1건" in captured.out


def test_run_fetch_no_save_flag_skips_raw_but_still_ingests(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"data": []})

    exit_code = run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    assert exit_code == 0
    assert not (tmp_path / "data" / "raw").exists()  # 원본 저장만 생략
    assert (tmp_path / "data" / "announcements.db").exists()  # 수집 DB는 유지


def test_run_fetch_api_error_exits_1_without_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            text=(
                "<OpenAPI_ServiceResponse><cmmMsgHeader>"
                "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
                "<returnReasonCode>30</returnReasonCode>"
                "</cmmMsgHeader></OpenAPI_ServiceResponse>"
            ),
        )

    exit_code = run_fetch(fetch_args(), client_factory=make_factory(handler))
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[오류]" in captured.err
    assert "30" in captured.err
    assert FAKE_KEY not in captured.err


def prepare_project_files(tmp_path):
    """evaluate가 기본 경로에서 찾는 참조 파일을 임시 작업 폴더에 복사한다."""
    import shutil

    repo_root = Path(__file__).parent.parent
    (tmp_path / "data" / "reference").mkdir(parents=True)
    shutil.copy(repo_root / "data" / "sample_company.json", tmp_path / "data")
    for reference_name in ("region_mapping.json", "industry_keywords.json", "relevance.json"):
        shutil.copy(
            repo_root / "data" / "reference" / reference_name,
            tmp_path / "data" / "reference",
        )


def test_evaluate_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        # 업력 조건은 의도적으로 생략 (NOT_APPLICABLE) —
                        # 실행 시점에 따라 판정이 달라지지 않도록 한다
                        "pbanc_sn": 1,
                        "biz_pbanc_nm": "전국 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                        "pbanc_rcpt_bgng_dt": "20990701",
                        "pbanc_rcpt_end_dt": "20990731",
                        "rcrt_prgs_yn": "Y",
                    },
                    {
                        "pbanc_sn": 2,
                        "biz_pbanc_nm": "부산 한정 가상 공고",
                        "supt_regin": "부산",
                        "aply_trgt": "일반기업",
                        "pbanc_rcpt_bgng_dt": "20990701",
                        "pbanc_rcpt_end_dt": "20990731",
                        "rcrt_prgs_yn": "Y",
                    },
                ]
            },
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()

    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[판정 요약]" in captured.out
    assert "우선 검토 1" in captured.out
    assert "지원 불가 1" in captured.out
    # 우선 검토 공고가 먼저 표시된다 (지시서 19절 정렬)
    assert captured.out.index("전국 가상 공고") < captured.out.index("부산 한정 가상 공고")
    # 제외 결과에도 이유가 표시된다
    assert "포함되지 않습니다" in captured.out


def test_evaluate_without_db_guides_user(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "fetch" in captured.err


def test_evaluate_accepts_real_company_data(tmp_path, monkeypatch, capsys):
    # is_fictional=false 실데이터 허용, 보고서에 구분 표시
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    company_path = tmp_path / "data" / "sample_company.json"
    data = json.loads(company_path.read_text(encoding="utf-8"))
    data["is_fictional"] = False
    company_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def handler(request):
        return httpx.Response(
            200, json={"data": [{"pbanc_sn": 1, "biz_pbanc_nm": "공고", "supt_regin": "전국"}]}
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "실제 회사" in captured.out
    assert "가상회사" not in captured.out


def test_evaluate_rejects_missing_fictional_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    company_path = tmp_path / "data" / "sample_company.json"
    data = json.loads(company_path.read_text(encoding="utf-8"))
    del data["is_fictional"]
    company_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "is_fictional" in captured.err


def test_default_company_prefers_real_company_file(tmp_path, monkeypatch, capsys):
    # data/company.json이 있으면 기본 회사로 우선 사용된다
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    data = json.loads((tmp_path / "data" / "sample_company.json").read_text(encoding="utf-8"))
    data.update({"is_fictional": False, "company_id": "real-001", "name": "실제회사테스트"})
    (tmp_path / "data" / "company.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    def handler(request):
        return httpx.Response(
            200, json={"data": [{"pbanc_sn": 1, "biz_pbanc_nm": "공고", "supt_regin": "전국"}]}
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "실제회사테스트" in captured.out


def test_evaluate_report_writes_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={"data": [{"pbanc_sn": 1, "biz_pbanc_nm": "가상 공고", "supt_regin": "전국"}]},
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()

    exit_code = main(["evaluate", "--report", "reports/test-report.md"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[보고서]" in captured.out
    report = (tmp_path / "reports" / "test-report.md").read_text(encoding="utf-8")
    assert report.startswith("# Grant Radar KR 판정 보고서")
    assert "가상 공고" in report
    assert FAKE_KEY not in report


def test_evaluate_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "pbanc_sn": 1,
                        "biz_pbanc_nm": "전국 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                    }
                ]
            },
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()

    exit_code = main(["evaluate", "--json", "reports/out.json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[JSON]" in captured.out
    payload = json.loads((tmp_path / "reports" / "out.json").read_text(encoding="utf-8"))
    assert payload["company_is_fictional"] is True
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["ELIGIBLE"] == 1
    result = payload["results"][0]
    assert result["source_id"] == "1"
    assert result["decision"] == "ELIGIBLE"
    assert {rule["rule_id"] for rule in result["rules"]} == {
        "region.v1",
        "business_age.v1",
        "applicant_type.v1",
        "age.v1",
        "industry.v1",
    }
    assert all(rule["reason"] for rule in result["rules"])  # 근거 포함
    assert "ServiceKey" not in json.dumps(payload)
    assert FAKE_KEY not in json.dumps(payload)


def test_run_command_fetches_and_evaluates(tmp_path, monkeypatch, capsys):
    from grant_radar.__main__ import run_run

    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "pbanc_sn": 1,
                        "biz_pbanc_nm": "전국 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                    }
                ]
            },
        )

    args = fetch_args(
        no_save=True,
        company=str(tmp_path / "data" / "sample_company.json"),
        report="reports/run-report.md",
    )
    exit_code = run_run(args, client_factory=make_factory(handler))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[수집] 신규 1건" in captured.out
    assert "[판정 요약]" in captured.out
    assert (tmp_path / "reports" / "run-report.md").is_file()


def test_save_raw_result_excludes_service_key(tmp_path):
    result = make_result({"currentCount": 1, "data": [{"pbanc_sn": "1"}]})
    path = save_raw_result(result, tmp_path)
    saved_text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in saved_text
    assert "ServiceKey" not in saved_text
    payload = json.loads(saved_text)
    assert payload["endpoint"] == "getAnnouncementInformation01"
    assert payload["request"] == {"page": 1, "perPage": 5, "returnType": "json"}
    assert payload["body"]["data"] == [{"pbanc_sn": "1"}]
    assert "20260721" in path.name


def test_summarize_dict_with_data_list():
    lines = summarize_top_level(
        {"currentCount": 2, "data": [{"pbanc_sn": "1", "biz_pbanc_nm": "이름"}]}
    )
    text = "\n".join(lines)
    assert "object" in text
    assert "data 항목 수: 1" in text
    assert "pbanc_sn" in text


def test_summarize_unexpected_shapes():
    assert "array" in summarize_top_level([1, 2])[0]
    assert "str" in summarize_top_level("plain")[0]
    # 기대 키(currentCount, data)가 없어도 예외 없이 키 목록을 요약해야 한다
    lines = summarize_top_level({"weird": True})
    assert lines == ["최상위 구조: object, 키: ['weird']"]


def _paged_handler(calls, total_count=6, per_page=2):
    """페이지 번호를 기록하고 페이지별 가상 공고를 돌려주는 핸들러."""

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        remaining = max(0, total_count - (page - 1) * per_page)
        count = min(per_page, remaining)
        items = [
            {"pbanc_sn": page * 100 + i, "biz_pbanc_nm": f"가상 공고 {page}-{i}"}
            for i in range(count)
        ]
        return httpx.Response(
            200,
            json={
                "currentCount": count,
                "totalCount": total_count,
                "page": page,
                "perPage": per_page,
                "data": items,
            },
        )

    return handler


def test_fetch_pages_collects_consecutive_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("grant_radar.__main__.time.sleep", lambda seconds: None)
    calls = []
    handler = _paged_handler(calls, total_count=6, per_page=2)

    exit_code = run_fetch(
        fetch_args(no_save=True, per_page=2, pages=2), client_factory=make_factory(handler)
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [1, 2]
    assert "신규 4건" in captured.out
    assert "저장소 누적 4건" in captured.out


def test_fetch_pages_stops_at_total_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("grant_radar.__main__.time.sleep", lambda seconds: None)
    calls = []
    handler = _paged_handler(calls, total_count=3, per_page=2)

    exit_code = run_fetch(
        fetch_args(no_save=True, per_page=2, pages=10), client_factory=make_factory(handler)
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    # 2페이지에서 totalCount(3)에 도달하므로 3페이지는 호출하지 않는다
    assert calls == [1, 2]
    assert "신규 3건" in captured.out


def test_fetch_pages_stops_on_empty_page(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("grant_radar.__main__.time.sleep", lambda seconds: None)
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        items = [{"pbanc_sn": 1, "biz_pbanc_nm": "공고"}] if page == 1 else []
        return httpx.Response(200, json={"data": items})  # totalCount 없는 응답

    exit_code = run_fetch(
        fetch_args(no_save=True, per_page=5, pages=10), client_factory=make_factory(handler)
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [1, 2]  # 빈 페이지를 만나면 중단
    assert "신규 1건" in captured.out


def test_fetch_bizinfo_source_ingests_and_saves(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)  # bizinfo 정규화가 지역 매핑표를 읽는다

    def handler(request):
        assert request.url.params["dataType"] == "json"
        return httpx.Response(
            200,
            json=bizinfo_page(
                [
                    {
                        "pblancId": "PBLN_000000000000001",
                        "pblancNm": "[서울] 가상 중소기업 지원",
                        "hashtags": "경영,서울,서울특별시,중소기업",
                        "trgetNm": "중소기업",
                        "reqstBeginEndDe": "2099-07-01 ~ 2099-07-31",
                        "pblancUrl": "https://example.test/detail?pblancId=PBLN_1",
                    }
                ]
            ),
        )

    exit_code = run_fetch(
        fetch_args(source="bizinfo"), client_factory=make_bizinfo_factory(handler)
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[수집] 신규 1건" in captured.out
    saved = list((tmp_path / "data" / "raw").glob("bizinfo_announcements_*.json"))
    assert len(saved) == 1
    saved_text = saved[0].read_text(encoding="utf-8")
    assert FAKE_KEY not in saved_text
    assert json.loads(saved_text)["endpoint"] == "pblancBsnsService"


def test_evaluate_mixed_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def kstartup_handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "pbanc_sn": 1,
                        "biz_pbanc_nm": "전국 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                    }
                ]
            },
        )

    def bizinfo_handler(request):
        return httpx.Response(
            200,
            json=bizinfo_page(
                [
                    {
                        "pblancId": "PBLN_000000000000002",
                        "pblancNm": "[서울] 가상 인력 지원",
                        "hashtags": "인력,서울,서울특별시",
                        "trgetNm": "중소기업",
                        "reqstBeginEndDe": "2099-07-01 ~ 2099-07-31",
                        "pblancUrl": "https://example.test/detail?pblancId=PBLN_2",
                    }
                ]
            ),
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(kstartup_handler))
    run_fetch(
        fetch_args(no_save=True, source="bizinfo"),
        client_factory=make_bizinfo_factory(bizinfo_handler),
    )
    capsys.readouterr()
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "공고 2건" in captured.out
    assert "우선 검토 2" in captured.out  # 두 원천 모두 지역·신청자 통과
    assert "가상 인력 지원" in captured.out


def test_fetch_pages_partial_failure_keeps_earlier_pages(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("grant_radar.__main__.time.sleep", lambda seconds: None)
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        if page >= 2:
            return httpx.Response(
                200,
                text=(
                    "<OpenAPI_ServiceResponse><cmmMsgHeader>"
                    "<returnReasonCode>30</returnReasonCode>"
                    "</cmmMsgHeader></OpenAPI_ServiceResponse>"
                ),
            )
        return httpx.Response(
            200,
            json={
                "currentCount": 1,
                "totalCount": 9,
                "data": [{"pbanc_sn": 1, "biz_pbanc_nm": "공고"}],
            },
        )

    exit_code = run_fetch(
        fetch_args(no_save=True, per_page=1, pages=3), client_factory=make_factory(handler)
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[오류]" in captured.err
    assert "1건은 저장되어" in captured.out  # 1페이지 수집분 유지 안내
    import sqlite3

    conn = sqlite3.connect(tmp_path / "data" / "announcements.db")
    stored = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
    conn.close()
    assert stored == 1


def _two_items_one_closed():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "pbanc_sn": 11,
                        "biz_pbanc_nm": "모집 중 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                        "pbanc_rcpt_end_dt": "20990731",
                        "rcrt_prgs_yn": "Y",
                    },
                    {
                        "pbanc_sn": 12,
                        "biz_pbanc_nm": "마감된 가상 공고",
                        "supt_regin": "전국",
                        "aply_trgt": "일반기업",
                        "pbanc_rcpt_end_dt": "20000101",
                        "rcrt_prgs_yn": "N",
                    },
                ]
            },
        )

    return handler


def test_evaluate_open_only_filters_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    run_fetch(fetch_args(no_save=True), client_factory=make_factory(_two_items_one_closed()))
    capsys.readouterr()

    exit_code = main(["evaluate", "--open-only", "--json", "reports/open.json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "공고 1건" in captured.out
    assert "[필터] 모집 중만" in captured.out
    assert "마감된 가상 공고" not in captured.out
    payload = json.loads((tmp_path / "reports" / "open.json").read_text(encoding="utf-8"))
    assert payload["filters"] == ["모집 중만"]
    assert payload["summary"]["total"] == 1


def test_evaluate_since_filters_by_first_seen(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    run_fetch(fetch_args(no_save=True), client_factory=make_factory(_two_items_one_closed()))
    capsys.readouterr()

    exit_code = main(["evaluate", "--since", "2000-01-01"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "공고 2건" in captured.out

    exit_code = main(["evaluate", "--since", "2999-01-01"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "해당하는 공고가 없습니다" in captured.out

    exit_code = main(["evaluate", "--since", "not-a-date"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--since" in captured.err


def test_run_new_only_reports_only_this_run(tmp_path, monkeypatch, capsys):
    from grant_radar.__main__ import run_run

    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    # 1차 실행: 2건 저장
    run_fetch(fetch_args(no_save=True), client_factory=make_factory(_two_items_one_closed()))
    capsys.readouterr()

    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"pbanc_sn": 11, "biz_pbanc_nm": "모집 중 가상 공고", "supt_regin": "전국",
                     "aply_trgt": "일반기업", "pbanc_rcpt_end_dt": "20990731", "rcrt_prgs_yn": "Y"},
                    {"pbanc_sn": 13, "biz_pbanc_nm": "새로 올라온 가상 공고", "supt_regin": "전국",
                     "aply_trgt": "일반기업", "pbanc_rcpt_end_dt": "20990731", "rcrt_prgs_yn": "Y"},
                ]
            },
        )

    args = fetch_args(
        no_save=True,
        new_only=True,
        open_only=True,
        company=str(tmp_path / "data" / "sample_company.json"),
    )
    exit_code = run_run(args, client_factory=make_factory(handler))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "공고 1건" in captured.out
    assert "새로 올라온 가상 공고" in captured.out
    assert "모집 중 가상 공고" not in captured.out.split("[판정 요약]")[1]


def test_evaluate_since_accepts_iso_datetime(tmp_path, monkeypatch, capsys):
    """scripts/weekly_run.ps1은 --since에 마지막 성공 '시각'(YYYY-MM-DDTHH:MM:SS)을 준다."""
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)

    def handler(request):
        return httpx.Response(
            200,
            json={"data": [{"pbanc_sn": 21, "biz_pbanc_nm": "가상 공고", "supt_regin": "전국",
                            "aply_trgt": "일반기업", "pbanc_rcpt_end_dt": "20990731",
                            "rcrt_prgs_yn": "Y"}]},
        )

    run_fetch(fetch_args(no_save=True), client_factory=make_factory(handler))
    capsys.readouterr()

    # 미래 시각 이후 신규 → 0건, 과거 시각 이후 신규 → 1건. 둘 다 형식 오류가 아니어야 한다.
    assert main(["evaluate", "--since", "2099-01-01T09:00:00"]) == 0
    assert "해당하는 공고가 없습니다" in capsys.readouterr().out
    assert main(["evaluate", "--since", "2000-01-01T09:00:00"]) == 0
    out = capsys.readouterr().out
    assert "가상 공고" in out
    assert "2000-01-01T09:00:00 이후 신규" in out
