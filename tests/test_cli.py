"""CLI(__main__) 테스트. 실제 API를 호출하지 않는다."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from grant_radar.__main__ import main, run_fetch, save_raw_result, summarize_top_level
from grant_radar.api.kstartup import FetchResult, KStartupClient
from grant_radar.config import API_KEY_ENV_VAR, MISSING_KEY_MESSAGE

FAKE_KEY = "fake-cli-test-key"


def make_factory(handler):
    """MockTransport를 주입하는 KStartupClient 팩토리를 만든다."""

    def factory(api_key):
        return KStartupClient(api_key, transport=httpx.MockTransport(handler), retry_wait=0.0)

    return factory


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
    shutil.copy(
        repo_root / "data" / "reference" / "region_mapping.json",
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


def test_evaluate_rejects_non_fictional_company(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    prepare_project_files(tmp_path)
    company_path = tmp_path / "data" / "sample_company.json"
    data = json.loads(company_path.read_text(encoding="utf-8"))
    data["is_fictional"] = False
    company_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    exit_code = main(["evaluate"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "실제 기업정보" in captured.err


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
