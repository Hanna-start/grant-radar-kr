"""CLI(__main__) 테스트. 실제 API를 호출하지 않는다."""

import argparse
import json
from datetime import datetime, timezone

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
