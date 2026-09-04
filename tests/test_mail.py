"""주간 보고 메일 테스트. 실제 SMTP 연결·발송을 하지 않는다 (모의 SMTP 주입)."""

import argparse
import json
from pathlib import Path

import pytest

from grant_radar.__main__ import run_mail
from grant_radar.config import (
    MAIL_PASSWORD_ENV_VAR,
    MAIL_TO_ENV_VAR,
    MAIL_USER_ENV_VAR,
    MISSING_MAIL_MESSAGE,
    ConfigError,
    MailSettings,
    load_mail_settings,
)
from grant_radar.notify.mail import (
    ReportRef,
    build_failure_message,
    build_report_message,
    send_message,
)

FAKE_PASSWORD = "fake-app-password-not-real"
SETTINGS = MailSettings(user="sender@example.com", password=FAKE_PASSWORD, to="to@example.com")

SAMPLE_REPORT = """# Grant Radar KR 판정 보고서

- 생성 시각: 2026-08-24T09:05
- 전체 12건: 우선 검토 5, 판단 필요 4, 지원 불가 3
- 관련도: 관련 높음 2, 보통 6, 관련 낮음 4
- 필터: 2026-08-17T09:00:00 이후 신규 / 모집 중만

> 면책 문구

## 한눈에 보기 — 관련 높음(A) 2건

| 분야 | 판정 | 마감 | 공고 |
|---|---|---|---|
| 인력 | 우선 검토 | 2026-09-30 | [고용창출장려금 안내](https://example.com/a) |
| 금융 | 판단 필요 | 미정 | 링크 없는 공고 ㅣ 제목 |

## 관련 높음 (A)

본문...
"""


class FakeSMTP:
    """smtplib.SMTP 대역. 호출 순서를 기록만 한다."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send",))
        self.sent = message


@pytest.fixture(autouse=True)
def _reset_fake_smtp():
    FakeSMTP.instances.clear()
    yield
    FakeSMTP.instances.clear()


# ---- 설정 ----


def test_mail_settings_repr_masks_password():
    text = repr(SETTINGS)
    assert FAKE_PASSWORD not in text
    assert "***" in text


def test_load_mail_settings_from_env_file(tmp_path, monkeypatch):
    for name in (MAIL_USER_ENV_VAR, MAIL_PASSWORD_ENV_VAR, MAIL_TO_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        f"{MAIL_USER_ENV_VAR}=u@example.com\n"
        f"{MAIL_PASSWORD_ENV_VAR}={FAKE_PASSWORD}\n"
        f"{MAIL_TO_ENV_VAR}=t@example.com\n"
        "SMTP_PORT=2525\n",
        encoding="utf-8",
    )
    settings = load_mail_settings(project_root=tmp_path)
    assert settings.user == "u@example.com"
    assert settings.to == "t@example.com"
    assert settings.host == "smtp.gmail.com"
    assert settings.port == 2525


def test_load_mail_settings_missing_raises(tmp_path, monkeypatch):
    for name in (MAIL_USER_ENV_VAR, MAIL_PASSWORD_ENV_VAR, MAIL_TO_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(f"{MAIL_USER_ENV_VAR}=u@example.com\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_mail_settings(project_root=tmp_path)
    assert str(exc_info.value) == MISSING_MAIL_MESSAGE


# ---- 메시지 작성 ----


def test_report_message_subject_body_attachments(tmp_path):
    report = tmp_path / "report-20260824.md"
    report.write_text(SAMPLE_REPORT, encoding="utf-8")
    result = tmp_path / "eval-20260824.json"
    result.write_text(
        json.dumps({"summary": {"relevance": {"A": 2, "B": 6, "C": 4}}}), encoding="utf-8"
    )

    message = build_report_message(SETTINGS, "2026-08-24", report, result)

    assert message["From"] == "sender@example.com"
    assert message["To"] == "to@example.com"
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-24 — 관련 높음 A 2건 (A 2 / B 6 / C 4)"
    body = message.get_body(preferencelist=("plain",)).get_content()
    # 머리 요약 줄이 그대로 들어간다
    assert "- 관련도: 관련 높음 2, 보통 6, 관련 낮음 4" in body
    assert "- 필터: 2026-08-17T09:00:00 이후 신규 / 모집 중만" in body
    # A 표가 줄글로 바뀐다 (링크 있는 행·없는 행)
    assert "- [인력] 우선 검토 · 마감 2026-09-30 · 고용창출장려금 안내" in body
    assert "  https://example.com/a" in body
    assert "- [금융] 판단 필요 · 마감 미정 · 링크 없는 공고 ㅣ 제목" in body
    # 표 머리·구분선은 들어가지 않는다
    assert "| 분야 |" not in body and "|---|" not in body
    # A 절 이후 본문은 들어가지 않는다
    assert "본문..." not in body
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == ["report-20260824.md", "eval-20260824.json"]


def test_report_message_without_report_file_says_no_new(tmp_path):
    message = build_report_message(SETTINGS, "2026-08-24", tmp_path / "missing.md", None)
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-24 — 신규 공고 없음"
    assert list(message.iter_attachments()) == []


def test_report_message_without_a_section(tmp_path):
    report = tmp_path / "r.md"
    report.write_text(
        "# Grant Radar KR 판정 보고서\n\n- 생성 시각: x\n- 관련도: 관련 높음 0, 보통 1, 관련 낮음 0\n\n## 보통 (B)\n",
        encoding="utf-8",
    )
    message = build_report_message(SETTINGS, "2026-08-24", report, None)
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-24 — 관련 높음 A 0건"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "관련 높음(A)·모집 중·검토 대상 공고가 없습니다" in body


# ---- 회사별 보고서 여러 건을 한 통에 ----

SAMPLE_REPORT_2 = """# Grant Radar KR 판정 보고서

- 생성 시각: 2026-08-24T09:07
- 회사: 예시상점 (sample-shop-002, 가상회사) / 공고 3건 — 우선 검토 2, 판단 필요 1
- 관련도: 관련 높음 1, 보통 2, 관련 낮음 0

## 한눈에 보기 — 관련 높음(A) 1건

| 분야 | 판정 | 마감 | 공고 |
|---|---|---|---|
| 인력 | 우선 검토 | 2026-10-31 | [소상공인 고용보험료 지원](https://example.com/b) |
"""


def _write_pair(tmp_path, name, markdown, relevance):
    report = tmp_path / f"{name}.md"
    report.write_text(markdown, encoding="utf-8")
    result = tmp_path / f"{name}.json"
    result.write_text(json.dumps({"summary": {"relevance": relevance}}), encoding="utf-8")
    return ReportRef(report=report, json=result)


def test_two_reports_share_one_message(tmp_path):
    ref1 = _write_pair(tmp_path, "report-corp", SAMPLE_REPORT, {"A": 2, "B": 6, "C": 4})
    ref2 = _write_pair(tmp_path, "report-secondary", SAMPLE_REPORT_2, {"A": 1, "B": 2, "C": 0})

    message = build_report_message(SETTINGS, "2026-08-31", [ref1, ref2])

    # 제목에 회사별 A 건수가 함께 들어간다 (첫 보고서엔 회사 줄이 없어 label 없이 '회사 미상')
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-31 — 회사 미상 A 2건 / 예시상점 A 1건"
    body = message.get_body(preferencelist=("plain",)).get_content()
    # 회사별 절이 나뉘고 각 A 목록이 모두 들어간다
    assert "━ 예시상점 ━" in body
    assert "- [인력] 우선 검토 · 마감 2026-09-30 · 고용창출장려금 안내" in body
    assert "- [인력] 우선 검토 · 마감 2026-10-31 · 소상공인 고용보험료 지원" in body
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == [
        "report-corp.md",
        "report-corp.json",
        "report-secondary.md",
        "report-secondary.json",
    ]


def test_second_report_missing_says_no_new_for_that_company(tmp_path):
    ref1 = _write_pair(tmp_path, "report-corp", SAMPLE_REPORT, {"A": 2, "B": 6, "C": 4})
    ref2 = ReportRef(report=tmp_path / "missing.md", json=None, label="예시상점")

    message = build_report_message(SETTINGS, "2026-08-31", [ref1, ref2])

    assert message["Subject"].endswith("회사 미상 A 2건 / 예시상점 신규 없음")
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "━ 예시상점 ━" in body
    assert "새로 관측된 모집 중 공고가 없습니다" in body
    # 있는 보고서만 첨부된다
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == ["report-corp.md", "report-corp.json"]


def test_all_reports_missing_is_single_no_new_message(tmp_path):
    refs = [
        ReportRef(report=tmp_path / "a.md", label="법인"),
        ReportRef(report=tmp_path / "b.md", label="개인"),
    ]
    message = build_report_message(SETTINGS, "2026-08-31", refs)
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-31 — 신규 공고 없음"
    assert list(message.iter_attachments()) == []


def test_company_name_read_from_report_header(tmp_path):
    ref = _write_pair(tmp_path, "r", SAMPLE_REPORT_2, {"A": 1, "B": 2, "C": 0})
    other = ReportRef(report=tmp_path / "none.md", label="다른 회사")
    message = build_report_message(SETTINGS, "2026-08-31", [ref, other])
    assert "예시상점 A 1건" in message["Subject"]


def test_failure_message_includes_log_tail_only(tmp_path):
    log = tmp_path / "weekly.log"
    log.write_text("\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8")
    message = build_failure_message(SETTINGS, "2026-08-24", log, tail_lines=30)
    assert message["Subject"] == "[Grant Radar] 주간 2026-08-24 — 실행 실패"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "line 50" in body and "line 21" in body
    assert "line 20" not in body


def test_failure_message_without_log(tmp_path):
    message = build_failure_message(SETTINGS, "2026-08-24", tmp_path / "none.log")
    assert "(로그 파일 없음)" in message.get_body(preferencelist=("plain",)).get_content()


# ---- 발송 ----


def test_send_message_uses_starttls_login_then_send():
    message = build_failure_message(SETTINGS, "2026-08-24", None)
    send_message(message, SETTINGS, smtp_factory=FakeSMTP)
    [smtp] = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 587)
    assert [c[0] for c in smtp.calls] == ["starttls", "login", "send", "quit"]
    assert smtp.calls[1] == ("login", "sender@example.com", FAKE_PASSWORD)
    assert smtp.sent is message


# ---- CLI ----


def _set_mail_env(monkeypatch):
    monkeypatch.setenv(MAIL_USER_ENV_VAR, "sender@example.com")
    monkeypatch.setenv(MAIL_PASSWORD_ENV_VAR, FAKE_PASSWORD)
    monkeypatch.setenv(MAIL_TO_ENV_VAR, "to@example.com")


def test_run_mail_sends_report(tmp_path, monkeypatch, capsys):
    _set_mail_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "report.md"
    report.write_text(SAMPLE_REPORT, encoding="utf-8")
    args = argparse.Namespace(report=str(report), json=None, failure_log=None, date="2026-08-24")

    assert run_mail(args, smtp_factory=FakeSMTP) == 0
    [smtp] = FakeSMTP.instances
    assert smtp.sent["Subject"].startswith("[Grant Radar] 주간 2026-08-24 — 관련 높음 A 2건")
    out = capsys.readouterr().out
    assert "to@example.com" in out
    assert FAKE_PASSWORD not in out


def test_run_mail_sends_two_reports_in_one_message(tmp_path, monkeypatch):
    _set_mail_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    report1 = tmp_path / "corp.md"
    report1.write_text(SAMPLE_REPORT, encoding="utf-8")
    report2 = tmp_path / "secondary.md"
    report2.write_text(SAMPLE_REPORT_2, encoding="utf-8")
    args = argparse.Namespace(
        report=[str(report1), str(report2)],
        json=None,
        label=["예시회사", "예시상점"],
        failure_log=None,
        date="2026-08-31",
    )

    assert run_mail(args, smtp_factory=FakeSMTP) == 0
    [smtp] = FakeSMTP.instances
    # 메일은 한 통, 보고서는 두 건이 절로 나뉘어 들어간다
    assert smtp.sent["Subject"] == (
        "[Grant Radar] 주간 2026-08-31 — 예시회사 A 2건 / 예시상점 A 1건"
    )
    names = [part.get_filename() for part in smtp.sent.iter_attachments()]
    assert names == ["corp.md", "secondary.md"]


def test_run_mail_rejects_mismatched_option_counts(tmp_path, monkeypatch, capsys):
    _set_mail_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "corp.md"
    report.write_text(SAMPLE_REPORT, encoding="utf-8")
    args = argparse.Namespace(
        report=[str(report)],
        json=["a.json", "b.json"],
        label=None,
        failure_log=None,
        date="2026-08-31",
    )
    assert run_mail(args, smtp_factory=FakeSMTP) == 1
    assert "--json 개수" in capsys.readouterr().err
    assert FakeSMTP.instances == []


def test_run_mail_failure_log_mode(tmp_path, monkeypatch):
    _set_mail_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "w.log"
    log.write_text("[오류] 어떤 실패", encoding="utf-8")
    args = argparse.Namespace(report=None, json=None, failure_log=str(log), date="2026-08-24")

    assert run_mail(args, smtp_factory=FakeSMTP) == 0
    [smtp] = FakeSMTP.instances
    assert "실행 실패" in smtp.sent["Subject"]
    assert "[오류] 어떤 실패" in smtp.sent.get_body(preferencelist=("plain",)).get_content()


def test_run_mail_without_settings_exits_1(tmp_path, monkeypatch, capsys):
    for name in (MAIL_USER_ENV_VAR, MAIL_PASSWORD_ENV_VAR, MAIL_TO_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(report=None, json=None, failure_log=None, date=None)
    assert run_mail(args, smtp_factory=FakeSMTP) == 1
    assert "SMTP_USER" in capsys.readouterr().err
    assert FakeSMTP.instances == []


def test_run_mail_smtp_error_exits_1_without_password(tmp_path, monkeypatch, capsys):
    _set_mail_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    class FailingSMTP(FakeSMTP):
        def login(self, user, password):
            raise OSError("connection refused")

    args = argparse.Namespace(report=None, json=None, failure_log=None, date="2026-08-24")
    assert run_mail(args, smtp_factory=FailingSMTP) == 1
    err = capsys.readouterr().err
    assert "메일 발송 실패" in err
    assert FAKE_PASSWORD not in err
