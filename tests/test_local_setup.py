import json
import subprocess
from pathlib import Path

import pytest

from grant_radar import local_setup as setup
from grant_radar.config import parse_env_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


def company():
    return {
        "company_id": "fictional-test",
        "name": "테스트 회사",
        "is_fictional": True,
        "business_type": "corporation",
        "headquarters_region": "서울",
        "established_date": "2022-03-15",
        "sme": None,
        "small_business": None,
    }


def settings():
    return {
        "KSTARTUP_API_KEY": "fake-api-do-not-use",
        "SMTP_USER": "sender@example.test",
        "SMTP_PASSWORD": "fake-password",
        "REPORT_MAIL_TO": "receiver@example.test",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "587",
    }


def test_company_requires_confirmation_without_creating_file(workspace):
    with pytest.raises(setup.SetupError):
        setup.save_company(workspace, company(), confirmed=False)
    assert not (workspace / "data/company.json").exists()


def test_unknown_information_stays_unknown(workspace):
    data = company()
    data["employee_count"] = 3
    setup.save_company(workspace, data, confirmed=True)
    actual = setup.read_company(workspace)
    assert actual["sme"] is None
    assert actual["small_business"] is None
    assert "representative_age" not in actual


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "a\nb"},
        {"employee_count": -1},
        {"representative_age": 121},
        {"established_date": "2099-01-01"},
    ],
)
def test_invalid_company_never_overwrites_saved_data(workspace, change):
    setup.save_company(workspace, company(), confirmed=True)
    before = (workspace / "data/company.json").read_bytes()
    with pytest.raises(setup.SetupError):
        setup.save_company(workspace, company() | change, confirmed=True)
    assert (workspace / "data/company.json").read_bytes() == before


def test_save_preserves_other_env_values_and_blank_secrets(workspace):
    setup.save_connection(workspace, settings())
    with (workspace / ".env").open("a", encoding="utf-8") as stream:
        stream.write("# user note\nUNRELATED=keep\n")
    setup.save_connection(
        workspace,
        settings()
        | {
            "KSTARTUP_API_KEY": "",
            "SMTP_PASSWORD": "",
            "REPORT_MAIL_TO": "new@example.test",
        },
    )
    env = parse_env_file(workspace / ".env")
    assert env["KSTARTUP_API_KEY"] == settings()["KSTARTUP_API_KEY"]
    assert env["SMTP_PASSWORD"] == settings()["SMTP_PASSWORD"]
    assert env["UNRELATED"] == "keep"
    assert env["REPORT_MAIL_TO"] == "new@example.test"
    assert not list(workspace.glob(".setup-*"))


@pytest.mark.parametrize(
    "change",
    [
        {"KSTARTUP_API_KEY": "fake\nSMTP_HOST=elsewhere"},
        {"SMTP_PASSWORD": "fake\r\nInjected"},
        {"REPORT_MAIL_TO": "one@example.test,two@example.test"},
        {"SMTP_USER": "missing-at"},
        {"SMTP_PORT": "465"},
        {"SMTP_PORT": "0"},
        {"SMTP_HOST": "server\nother"},
    ],
)
def test_invalid_connection_never_overwrites_saved_file(workspace, change):
    setup.save_connection(workspace, settings())
    before = (workspace / ".env").read_bytes()
    with pytest.raises(setup.SetupError):
        setup.save_connection(workspace, settings() | change)
    assert (workspace / ".env").read_bytes() == before


def test_run_cli_removes_inherited_credentials_and_masks_errors(workspace, monkeypatch):
    setup.save_connection(workspace, settings())
    monkeypatch.setenv("KSTARTUP_API_KEY", "inherited-private")
    monkeypatch.setenv("SMTP_PASSWORD", "inherited-password")

    def run(argv, **kwargs):
        assert "KSTARTUP_API_KEY" not in kwargs["env"]
        assert "SMTP_PASSWORD" not in kwargs["env"]
        assert "fake-api-do-not-use" not in repr(argv)
        return subprocess.CompletedProcess(argv, 1, "fake-api-do-not-use", "fake-password")

    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(setup.SetupError) as caught:
        setup.run_cli(workspace, ["fetch"])
    assert "fake-api-do-not-use" not in str(caught.value)
    assert "fake-password" not in str(caught.value)


def test_run_cli_timeout_has_actionable_message(workspace, monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("fake", 1)

    monkeypatch.setattr(setup.subprocess, "run", run)
    with pytest.raises(setup.SetupError, match="초과"):
        setup.run_cli(workspace, ["fetch"], timeout=1)


def test_test_email_uses_saved_recipient_and_no_documents(workspace, monkeypatch):
    setup.save_connection(workspace, settings())
    captured = []
    monkeypatch.setattr(setup, "_send", lambda msg, config: captured.append((msg, config)))
    setup.send_test_email(workspace)
    message, config = captured[0]
    assert message["To"] == "receiver@example.test"
    assert config.password == "fake-password"
    assert list(message.iter_attachments()) == []


def test_mail_error_does_not_echo_credentials(workspace, monkeypatch):
    setup.save_connection(workspace, settings())

    def fail(*args, **kwargs):
        raise RuntimeError("fake-password")

    monkeypatch.setattr(setup, "send_message", fail)
    with pytest.raises(setup.SetupError) as caught:
        setup.send_test_email(workspace)
    assert "fake-password" not in str(caught.value)


def test_changed_company_cannot_send_stale_report(workspace):
    setup.save_company(workspace, company(), confirmed=True)
    report = workspace / "reports/current"
    report.mkdir(parents=True)
    bundle = setup.ReportBundle(report, setup._company_hash(workspace))
    setup.save_company(workspace, company() | {"name": "다른 회사"}, confirmed=True)
    with pytest.raises(setup.SetupError, match="바뀌"):
        setup.send_report_email(workspace, bundle)


def test_fictional_demo_is_isolated_and_uses_real_cli(workspace):
    import shutil

    for folder in ("reference", "fixtures"):
        shutil.copytree(ROOT / "data" / folder, workspace / "data" / folder)
    shutil.copy2(ROOT / "data/sample_company.json", workspace / "data/sample_company.json")
    setup.save_company(workspace, company(), confirmed=True)
    before = (workspace / "data/company.json").read_bytes()
    bundle = setup.demo_report(workspace)
    payload = json.loads((bundle.directory / "report.json").read_text(encoding="utf-8"))
    assert payload["company_is_fictional"] is True
    assert payload["summary"]["total"] == 15
    assert payload["summary"]["ELIGIBLE"] == 3
    assert payload["summary"]["REVIEW_REQUIRED"] == 8
    assert payload["summary"]["INELIGIBLE"] == 4
    assert (workspace / "data/company.json").read_bytes() == before
    assert not (workspace / "data/announcements.db").exists()
    with pytest.raises(setup.SetupError, match="가상"):
        setup.send_report_email(workspace, bundle)


def test_partial_collection_warning_survives_to_report(workspace, monkeypatch):
    setup.save_company(workspace, company(), confirmed=True)
    setup.save_connection(workspace, settings())
    calls = []

    def run(root, args, **kwargs):
        calls.append(args)
        if args[0] == "fetch":
            source = args[args.index("--source") + 1]
            (root / "data/run_manifest.json").write_text(
                json.dumps({"runs": [{"source": source, "status": "partial", "collected": 100}]})
            )
        return ""

    monkeypatch.setattr(setup, "run_cli", run)
    bundle = setup.collect_report(workspace)
    assert len(bundle.warnings) == 2
    assert [args[0] for args in calls] == ["fetch", "fetch", "evaluate"]
    assert all("mail" not in args for args in calls)


def test_api_probe_checks_both_with_real_clients_and_mock_transport(workspace, monkeypatch):
    import httpx

    from grant_radar.api import bizinfo, kstartup

    setup.save_connection(workspace, settings())
    first, second = kstartup.KStartupClient, bizinfo.BizinfoClient
    calls = []

    def k_handler(request):
        calls.append("kstartup")
        return httpx.Response(200, json={"data": [], "totalCount": 0})

    def b_handler(request):
        calls.append("bizinfo")
        return httpx.Response(
            200,
            json={
                "response": {"header": {"resultCode": "00"}, "body": {"items": {}, "totalCount": 0}}
            },
        )

    monkeypatch.setattr(
        kstartup,
        "KStartupClient",
        lambda key, **kw: first(key, transport=httpx.MockTransport(k_handler), **kw),
    )
    monkeypatch.setattr(
        bizinfo,
        "BizinfoClient",
        lambda key, **kw: second(key, transport=httpx.MockTransport(b_handler), **kw),
    )
    result = setup.probe_api(workspace)
    assert calls == ["kstartup", "bizinfo"]
    assert set(result.values()) == {"연결 확인"}
