import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    shutil.copytree(ROOT / "data/reference", tmp_path / "data/reference")
    source = (
        "from pathlib import Path\n"
        "from grant_radar import web_app\n"
        f"web_app.ROOT = Path({str(tmp_path)!r})\n"
        "web_app.main()\n"
    )
    return AppTest.from_string(source, default_timeout=15), tmp_path


def by_label(elements, label):
    return next(item for item in elements if item.label == label)


def test_initial_screen_and_explicit_save_confirmation(app):
    at, root = app
    at.run()
    assert not at.exception
    by_label(at.text_input, "회사 이름 *").set_value("가상 회사")
    by_label(at.button, "회사 정보 저장").click().run()
    assert not (root / "data/company.json").exists()
    assert any("확인" in error.value for error in at.error)
    by_label(at.checkbox, "문서에서 읽은 값과 입력 내용을 확인했습니다.").check()
    by_label(at.checkbox, "가상 회사로 사용").check()
    by_label(at.button, "회사 정보 저장").click().run()
    assert not at.exception
    company = json.loads((root / "data/company.json").read_text(encoding="utf-8"))
    assert company["name"] == "가상 회사"
    assert company["is_fictional"] is True
    assert company["small_business"] is None


def test_connection_form_does_not_render_saved_secrets(app):
    from grant_radar.local_setup import save_connection

    at, root = app
    save_connection(
        root,
        {
            "KSTARTUP_API_KEY": "fake-private-key",
            "SMTP_PASSWORD": "fake-private-password",
            "SMTP_USER": "sender@example.test",
            "REPORT_MAIL_TO": "reader@example.test",
        },
    )
    at.run()
    at.radio[0].set_value("API·메일").run()
    assert not at.exception
    assert by_label(at.text_input, "공공데이터포털 API 키").value == ""
    assert by_label(at.text_input, "메일 앱 비밀번호 / SMTP 비밀번호").value == ""


def test_run_actions_require_saved_config_and_schedule_consent(app):
    at, _ = app
    at.run()
    at.radio[0].set_value("실행·예약").run()
    assert not at.exception
    assert by_label(at.button, "지금 공고 찾아보기").disabled
    assert by_label(at.button, "자동 메일 켜기").disabled
    assert not by_label(at.button, "가상 회사로 체험하기").disabled
