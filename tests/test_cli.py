"""CLI(__main__) 테스트. 실제 API를 호출하지 않는다."""

import json
from datetime import datetime, timezone

import pytest

from grant_radar.__main__ import main, save_raw_result, summarize_top_level
from grant_radar.api.kstartup import FetchResult
from grant_radar.config import API_KEY_ENV_VAR, MISSING_KEY_MESSAGE

FAKE_KEY = "fake-cli-test-key"


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
    # 예상과 다른 구조라도 예외 없이 요약해야 한다
    assert summarize_top_level({"weird": True})
