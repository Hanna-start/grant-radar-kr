"""Local onboarding and isolated CLI execution. No network on import or save."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from grant_radar.api.kstartup import mask_secret
from grant_radar.config import MailSettings, parse_env_file
from grant_radar.models.company import company_from_dict
from grant_radar.notify.mail import build_report_message, send_message
from grant_radar.services.ingestion import KST

ENV_KEYS = (
    "KSTARTUP_API_KEY",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "REPORT_MAIL_TO",
    "SMTP_HOST",
    "SMTP_PORT",
)
SECRET_KEYS = ("KSTARTUP_API_KEY", "SMTP_PASSWORD")


class SetupError(ValueError):
    pass


@dataclass(frozen=True)
class ReportBundle:
    directory: Path
    company_hash: str
    warnings: tuple[str, ...] = ()
    fictional: bool = False


def read_company(root: Path) -> dict:
    path = root / "data/company.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        company_from_dict(data)
        return data
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise SetupError(
            "저장된 회사 정보를 읽지 못했습니다. 원본 파일은 변경하지 않았습니다."
        ) from None


def validate_company(data: dict) -> dict:
    if not str(data.get("name", "")).strip():
        raise SetupError("회사 이름을 입력해주세요.")
    if len(str(data["name"])) > 100 or "\n" in data["name"] or "\r" in data["name"]:
        raise SetupError("회사 이름은 줄바꿈 없이 100자 이내로 입력해주세요.")
    if data.get("business_type") not in {"corporation", "individual", None}:
        raise SetupError("사업자 형태를 확인해주세요.")
    for field in ("employee_count", "representative_age"):
        value = data.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise SetupError("직원 수와 대표자 나이는 0 이상의 정수로 입력해주세요.")
    if data.get("representative_age") is not None and data["representative_age"] > 120:
        raise SetupError("대표자 만 나이를 확인해주세요.")
    try:
        company = company_from_dict(data)
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise SetupError("회사 정보의 날짜·목록·확인 여부 형식을 확인해주세요.") from None
    if company.established_date and company.established_date > datetime.now(KST).date():
        raise SetupError("설립일은 오늘 이후일 수 없습니다.")
    return data


def _valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value))


def validate_connection(values: dict[str, str], *, complete: bool = False) -> dict[str, str]:
    result = {key: str(values.get(key, "")).strip() for key in ENV_KEYS}
    result["SMTP_HOST"] = result["SMTP_HOST"] or "smtp.gmail.com"
    result["SMTP_PORT"] = result["SMTP_PORT"] or "587"
    for value in result.values():
        if (
            any(char in value for char in "\r\n\0")
            or value.startswith(("'", '"'))
            or value.endswith(("'", '"'))
        ):
            raise SetupError("설정 값에 줄바꿈이나 감싸는 따옴표를 넣지 마세요.")
    for key in ("SMTP_USER", "REPORT_MAIL_TO"):
        if result[key] and not _valid_email(result[key]):
            raise SetupError("보내는 주소와 받는 주소를 각각 이메일 주소 하나로 입력해주세요.")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", result["SMTP_HOST"]):
        raise SetupError("SMTP 서버 주소를 확인해주세요.")
    if not result["SMTP_PORT"].isdigit() or not 1 <= int(result["SMTP_PORT"]) <= 65535:
        raise SetupError("SMTP 포트는 1~65535 사이 숫자여야 합니다.")
    if result["SMTP_PORT"] == "465":
        raise SetupError(
            "현재 메일은 STARTTLS 방식입니다. 제공자의 STARTTLS 포트(보통 587)를 사용하세요."
        )
    if complete and not all(result[key] for key in ENV_KEYS[:4]):
        raise SetupError("API 키, 발송 계정, 앱 비밀번호, 수신 주소를 모두 저장해주세요.")
    return result


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_company(root: Path, data: dict, *, confirmed: bool) -> None:
    if not confirmed:
        raise SetupError("문서와 입력 내용을 확인한 뒤 저장해주세요.")
    validate_company(data)
    _atomic_write(root / "data/company.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def save_connection(root: Path, values: dict[str, str]) -> None:
    env_path = root / ".env"
    previous = parse_env_file(env_path)
    merged = {key: values.get(key, previous.get(key, "")) for key in ENV_KEYS}
    # Blank secret fields explicitly mean "keep saved value"; secrets are never prefilled.
    for key in SECRET_KEYS:
        if not merged.get(key):
            merged[key] = previous.get(key, "")
    checked = validate_connection(merged)
    original = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
    lines = [
        line for line in original.splitlines() if line.partition("=")[0].strip() not in ENV_KEYS
    ]
    lines.extend(f"{key}={checked[key]}" for key in ENV_KEYS)
    _atomic_write(env_path, "\n".join(lines) + "\n")


def connection(root: Path, *, complete: bool = False) -> dict[str, str]:
    return validate_connection(parse_env_file(root / ".env"), complete=complete)


def scrub(text: str, values: dict[str, str]) -> str:
    for key in SECRET_KEYS:
        text = mask_secret(text, values.get(key, ""))
    return text


def _child_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_cli(root: Path, args: list[str], *, timeout: int = 600) -> str:
    values = connection(root)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "grant_radar", *args],
            check=False,
            cwd=root,
            env=_child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise SetupError("처리 시간이 초과됐습니다. 잠시 후 다시 시도하세요.") from None
    except OSError:
        raise SetupError("실행 환경을 찾지 못했습니다. 시작 파일로 다시 실행해주세요.") from None
    if result.returncode:
        safe = scrub(result.stdout + "\n" + result.stderr, values)
        raise SetupError("실행을 완료하지 못했습니다.\n" + safe[-3500:])
    return scrub(result.stdout, values)


def probe_api(root: Path) -> dict[str, str]:
    from grant_radar.api.bizinfo import BizinfoClient
    from grant_radar.api.kstartup import KStartupClient
    from grant_radar.normalization.bizinfo import normalize_bizinfo_page
    from grant_radar.normalization.kstartup import normalize_page

    values = connection(root)
    key = values["KSTARTUP_API_KEY"]
    if not key:
        raise SetupError("API 키를 먼저 저장해주세요.")
    results = {}
    for name, cls, normalizer in (
        ("K-Startup", KStartupClient, normalize_page),
        ("기업마당", BizinfoClient, normalize_bizinfo_page),
    ):
        try:
            with cls(key, max_retries=0) as client:
                page = client.fetch_announcements_page(page=1, per_page=1)
                normalizer(page.data)
            results[name] = "연결 확인"
        except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
            results[name] = "연결 실패 — 인증키·해당 API 활용 신청·네트워크를 확인해주세요."
    return results


def _company_hash(root: Path) -> str:
    return hashlib.sha256((root / "data/company.json").read_bytes()).hexdigest()


def collect_report(root: Path) -> ReportBundle:
    if not read_company(root):
        raise SetupError("회사 정보를 먼저 저장해주세요.")
    values = connection(root)
    if not values["KSTARTUP_API_KEY"]:
        raise SetupError("API 키를 먼저 저장해주세요.")
    warnings = []
    for source, pages in (("kstartup", 3), ("bizinfo", 17)):
        run_cli(
            root,
            ["fetch", "--source", source, "--per-page", "100", "--pages", str(pages), "--no-save"],
        )
        manifest = json.loads((root / "data/run_manifest.json").read_text(encoding="utf-8"))
        runs = [run for run in manifest["runs"] if run["source"] == source]
        if len(runs) != 1 or runs[0]["status"] not in {"complete", "partial"}:
            raise SetupError("수집 범위를 확인하지 못했습니다. 보고서를 발송하지 않았습니다.")
        if runs[0]["status"] == "partial":
            warnings.append(
                f"{source}: {runs[0]['collected']}건 부분 수집. 전체 공고 수집은 확인되지 않았습니다."
            )
    folder = Path(tempfile.mkdtemp(prefix="web-", dir=_reports_dir(root)))
    fingerprint = _company_hash(root)
    run_cli(
        root,
        [
            "evaluate",
            "--company",
            "data/company.json",
            "--open-only",
            "--report",
            str(folder / "report.md"),
            "--json",
            str(folder / "report.json"),
        ],
    )
    return ReportBundle(folder, fingerprint, tuple(warnings))


def _reports_dir(root: Path) -> Path:
    folder = root / "reports"
    folder.mkdir(exist_ok=True)
    return folder


def demo_report(root: Path) -> ReportBundle:
    from grant_radar.normalization.kstartup import normalize_page
    from grant_radar.storage.sqlite import AnnouncementStore

    folder = Path(tempfile.mkdtemp(prefix="demo-", dir=_reports_dir(root)))
    (folder / "data").mkdir()
    shutil.copytree(root / "data/reference", folder / "data/reference")
    data = json.loads((root / "data/sample_company.json").read_text(encoding="utf-8"))
    data.update(name="토끼랩 주식회사", company_id="fictional-tokkilab", is_fictional=True)
    save_company(folder, data, confirmed=True)
    body = json.loads(
        (root / "data/fixtures/golden_announcements.json").read_text(encoding="utf-8")
    )
    with AnnouncementStore(folder / "data/announcements.db") as store:
        for ann in normalize_page(body):
            store.upsert(ann, datetime.now(KST))
    run_cli(
        folder,
        [
            "evaluate",
            "--company",
            "data/company.json",
            "--report",
            str(folder / "report.md"),
            "--json",
            str(folder / "report.json"),
        ],
    )
    notice = "> 가상 회사·가상 공고로 실행한 시연입니다. 실제 수집 결과가 아닙니다.\n\n"
    report = folder / "report.md"
    report.write_text(notice + report.read_text(encoding="utf-8"), encoding="utf-8")
    return ReportBundle(folder, "", fictional=True)


class VerifiedSMTP(smtplib.SMTP):
    def starttls(self, *, context=None):
        return super().starttls(context=context or ssl.create_default_context())


def _mail_settings(root: Path) -> MailSettings:
    values = connection(root)
    if not all(values[key] for key in ("SMTP_USER", "SMTP_PASSWORD", "REPORT_MAIL_TO")):
        raise SetupError("메일 발송 계정·앱 비밀번호·받는 주소를 먼저 저장해주세요.")
    return MailSettings(
        user=values["SMTP_USER"],
        password=values["SMTP_PASSWORD"],
        to=values["REPORT_MAIL_TO"],
        host=values["SMTP_HOST"],
        port=int(values["SMTP_PORT"]),
    )


def _send(message: EmailMessage, settings: MailSettings) -> None:
    try:
        send_message(
            message, settings, smtp_factory=lambda host, port: VerifiedSMTP(host, port, timeout=20)
        )
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise SetupError(
            "메일 발송 실패: SMTP 서버·포트·발송 계정의 앱 비밀번호를 확인해주세요."
        ) from None


def send_test_email(root: Path) -> None:
    settings = _mail_settings(root)
    message = EmailMessage()
    message["From"], message["To"] = settings.user, settings.to
    message["Subject"] = "[Grant Radar] 메일 연결 시험"
    message.set_content(
        "메일 발송 설정 확인용입니다. 회사 정보나 사업자등록증은 첨부하지 않았습니다.\n"
    )
    _send(message, settings)


def send_report_email(root: Path, bundle: ReportBundle) -> None:
    if bundle.fictional:
        raise SetupError("가상 체험 보고서는 실제 보고서로 발송하지 않습니다.")
    if not bundle.directory.resolve().is_relative_to((root / "reports").resolve()):
        raise SetupError("보고서 경로를 확인해주세요.")
    if bundle.company_hash != _company_hash(root):
        raise SetupError("회사 정보가 바뀌었습니다. 새 보고서를 만든 뒤 발송해주세요.")
    settings = _mail_settings(root)
    message = build_report_message(
        settings,
        datetime.now(KST).date().isoformat(),
        bundle.directory / "report.md",
        bundle.directory / "report.json",
        warnings=list(bundle.warnings),
    )
    _send(message, settings)


def scheduled_task(root: Path, mode: str) -> str:
    if sys.platform != "win32":
        raise SetupError(
            "현재 자동 예약은 Windows에서 지원합니다. 다른 OS에서는 직접 실행해주세요."
        )
    if mode not in {"status", "enable", "disable"}:
        raise SetupError("알 수 없는 예약 작업입니다.")
    if mode == "enable":
        connection(root, complete=True)
        if not read_company(root):
            raise SetupError("회사 정보를 먼저 저장해주세요.")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts/local_task.ps1"),
                "-Mode",
                mode,
                "-PythonPath",
                sys.executable,
            ],
            check=False,
            cwd=root,
            env=_child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("Windows 예약 작업을 처리하지 못했습니다.") from None
    if result.returncode:
        raise SetupError(
            "예약 작업을 처리하지 못했습니다. Windows 권한과 실행 경로를 확인해주세요."
        )
    return result.stdout.strip()
