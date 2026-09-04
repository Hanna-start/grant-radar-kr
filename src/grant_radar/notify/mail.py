"""주간 보고 메일 작성·발송.

정기 실행 결과를 환경변수로 설정한 SMTP 계정과 수신자에게 보낸다.

원칙:
- 메일 본문은 보고서 Markdown에서 기계적으로 잘라낸다 (요약 줄 + '한눈에 보기'
  A 표). 자연어 요약·재작성 없음.
- 첨부는 보고서 Markdown과 JSON뿐. 회사 데이터(company.json)·로그 전체는 붙이지 않는다.
- 실패 메일은 로그 끝 N줄만 싣는다 (프로그램이 인증키를 이미 *** 로 가리므로
  로그에 비밀 값이 없다는 전제 — 비밀번호는 로그에 찍히지 않는다).
- SMTP 연결은 주입 가능(smtp_factory) — 테스트는 실제 발송을 하지 않는다.
"""

from __future__ import annotations

import json
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable

from grant_radar.config import MailSettings

SUBJECT_PREFIX = "[Grant Radar]"
FAILURE_LOG_TAIL_LINES = 30

# "- 회사: 예시 회사 (example-001, 실제 회사) / 공고 …" 에서 회사명만
_COMPANY_LINE = re.compile(r"^- 회사:\s*(?P<name>.+?)\s*\(")

# "| 분야 | 판정 | 마감 | [제목](url) |" 또는 링크 없는 제목
_TABLE_ROW = re.compile(r"^\|\s*(?P<cat>[^|]*?)\s*\|\s*(?P<dec>[^|]*?)\s*\|\s*(?P<end>[^|]*?)\s*\|\s*(?P<title>.*?)\s*\|\s*$")
_MD_LINK = re.compile(r"^\[(?P<text>.*)\]\((?P<url>[^)]*)\)$")


def _header_bullets(markdown: str) -> list[str]:
    """보고서 머리의 '- 생성 시각 / 판정 요약 / 관련도 / 필터' 줄을 그대로 가져온다."""
    bullets: list[str] = []
    for line in markdown.splitlines()[1:]:
        if line.startswith("- "):
            bullets.append(line)
        elif bullets:
            break
    return bullets


def _a_summary_lines(markdown: str) -> list[str]:
    """'## 한눈에 보기' 절의 표를 메일용 줄글로 바꾼다. 절이 없으면 빈 목록."""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## 한눈에 보기")), None)
    if start is None:
        return []
    out = [lines[start].lstrip("# ").strip(), ""]
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        match = _TABLE_ROW.match(line)
        if not match or set(match.group("cat")) <= {"-"} or match.group("cat") == "분야":
            continue
        title, url = match.group("title"), ""
        link = _MD_LINK.match(title)
        if link:
            title, url = link.group("text"), link.group("url")
        out.append(f"- [{match.group('cat')}] {match.group('dec')} · 마감 {match.group('end')} · {title}")
        if url:
            out.append(f"  {url}")
    return out


def _tier_counts(json_path: Path | None) -> dict[str, int] | None:
    if json_path is None or not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        counts = payload["summary"]["relevance"]
        return {tier: int(counts.get(tier, 0)) for tier in ("A", "B", "C")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class ReportRef:
    """메일에 실을 보고서 한 건 (회사 하나의 판정 결과)."""

    report: Path | None
    json: Path | None = None
    # 보고서 파일이 없을 때(신규 0건) 쓸 이름. 파일이 있으면 보고서 머리에서 회사명을 읽는다.
    label: str | None = None


def _as_refs(reports, json_path=None) -> list[ReportRef]:
    """단일 경로(기존 호출 형태)와 ReportRef 목록을 모두 받아준다."""
    if isinstance(reports, list):
        return list(reports)
    return [
        ReportRef(
            report=Path(reports) if reports else None,
            json=Path(json_path) if json_path else None,
        )
    ]


def _label_of(ref: ReportRef, markdown: str | None) -> str:
    if markdown:
        for line in markdown.splitlines():
            match = _COMPANY_LINE.match(line)
            if match:
                return match.group("name")
    return ref.label or "회사 미상"


def _section(ref: ReportRef, markdown: str) -> tuple[list[str], int]:
    """보고서 하나를 메일 본문 줄 목록으로. (줄 목록, A 건수)를 돌려준다."""
    a_lines = _a_summary_lines(markdown)
    a_count = sum(1 for line in a_lines if line.startswith("- ["))
    lines = list(_header_bullets(markdown))
    lines.append("")
    if a_lines:
        lines.extend(a_lines)
    else:
        lines.append("관련 높음(A)·모집 중·검토 대상 공고가 없습니다. 첨부 보고서의 B 절을 확인하세요.")
    return lines, a_count


def _attachments_of(ref: ReportRef, markdown: str) -> list[tuple[str, str, bytes]]:
    """(파일명, 서브타입, 내용) 목록. 실제 첨부는 set_content 이후에 해야 한다
    (첨부가 붙으면 메시지가 multipart가 되어 set_content를 더 못 쓴다)."""
    items = [(ref.report.name, "markdown", markdown.encode("utf-8"))]
    if ref.json and Path(ref.json).is_file():
        items.append((Path(ref.json).name, "json", Path(ref.json).read_bytes()))
    return items


def build_report_message(
    settings: MailSettings,
    run_date: str,
    reports,
    json_path: Path | None = None,
) -> EmailMessage:
    """보고서 메일. `reports`는 경로 하나 또는 ReportRef 목록(회사별 보고서).

    보고서 파일이 하나도 없으면 '신규 공고 없음' 메일을 만든다 (`run --since`가
    신규 0건이면 보고서 파일을 쓰지 않고 정상 종료하므로, 그 경우도 메일은 보내
    '실행은 됐다'를 알린다).
    """
    refs = _as_refs(reports, json_path)
    loaded: list[tuple[ReportRef, str | None]] = [
        (ref, ref.report.read_text(encoding="utf-8") if ref.report and ref.report.is_file() else None)
        for ref in refs
    ]

    message = EmailMessage()
    message["From"] = settings.user
    message["To"] = settings.to

    if all(markdown is None for _, markdown in loaded):
        message["Subject"] = f"{SUBJECT_PREFIX} 주간 {run_date} — 신규 공고 없음"
        message.set_content(
            f"Grant Radar KR 주간 실행 ({run_date})\n\n"
            "지난 실행 이후 새로 관측된 모집 중 공고가 없어 보고서가 생성되지 않았습니다.\n"
            "(수집·판정은 정상 실행됨)\n"
        )
        return message

    single = len(loaded) == 1
    body = [f"Grant Radar KR 주간 보고 ({run_date})", ""]
    attachments: list[tuple[str, str, bytes]] = []
    subject_parts: list[str] = []
    first_a_count = 0

    for ref, markdown in loaded:
        label = _label_of(ref, markdown)
        if markdown is None:
            subject_parts.append(f"{label} 신규 없음")
            body.extend([f"━ {label} ━", "", "지난 실행 이후 새로 관측된 모집 중 공고가 없습니다.", ""])
            continue
        lines, a_count = _section(ref, markdown)
        if not subject_parts:
            first_a_count = a_count
        subject_parts.append(f"{label} A {a_count}건")
        if not single:
            body.extend([f"━ {label} ━", ""])
        body.extend(lines)
        body.append("")
        attachments.extend(_attachments_of(ref, markdown))

    if single:
        # 기존 단일 보고서 메일의 제목 형식을 그대로 유지한다 (2026-08-23 발송 확인분).
        subject = f"{SUBJECT_PREFIX} 주간 {run_date} — 관련 높음 A {first_a_count}건"
        counts = _tier_counts(loaded[0][0].json)
        if counts:
            subject += f" (A {counts['A']} / B {counts['B']} / C {counts['C']})"
    else:
        subject = f"{SUBJECT_PREFIX} 주간 {run_date} — " + " / ".join(subject_parts)
    message["Subject"] = subject

    names = [name for name, _, _ in attachments]
    body.append(f"첨부: {', '.join(names)}" if names else "첨부 없음")
    body.append("")
    body.append("이 결과는 공식 자격 판정이 아니며, 공고 원문·첨부를 사람이 확인해야 합니다.")
    message.set_content("\n".join(body) + "\n")

    for name, subtype, content in attachments:
        maintype = "text" if subtype == "markdown" else "application"
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)
    return message


def build_failure_message(
    settings: MailSettings,
    run_date: str,
    log_path: Path | None,
    tail_lines: int = FAILURE_LOG_TAIL_LINES,
) -> EmailMessage:
    """실행 실패 메일 — 로그 끝 tail_lines줄만 싣는다."""
    message = EmailMessage()
    message["From"] = settings.user
    message["To"] = settings.to
    message["Subject"] = f"{SUBJECT_PREFIX} 주간 {run_date} — 실행 실패"
    tail = "(로그 파일 없음)"
    if log_path is not None and Path(log_path).is_file():
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-tail_lines:]) or "(로그 비어 있음)"
    message.set_content(
        f"Grant Radar KR 주간 실행 ({run_date})이 실패했습니다.\n"
        "마지막 성공 시각은 갱신되지 않았으므로 다음 실행이 이 기간을 다시 수집합니다.\n\n"
        f"로그 끝 {tail_lines}줄:\n"
        f"{'-' * 40}\n{tail}\n"
    )
    return message


SmtpFactory = Callable[[str, int], smtplib.SMTP]


def send_message(
    message: EmailMessage, settings: MailSettings, smtp_factory: SmtpFactory | None = None
) -> None:
    """STARTTLS + 로그인 후 발송. 예외는 그대로 올린다 (메시지에 비밀번호 없음)."""
    factory = smtp_factory if smtp_factory is not None else smtplib.SMTP
    with factory(settings.host, settings.port) as smtp:
        smtp.starttls()
        smtp.login(settings.user, settings.password)
        smtp.send_message(message)
