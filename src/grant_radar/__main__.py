"""명령행 진입점.

사용 예:
    python -m grant_radar fetch --page 1 --per-page 5

`fetch`는 K-Startup 공고 목록을 조회해(--pages로 여러 페이지 연속 수집 가능)
최상위 구조를 요약 출력하고, 원본 응답을 data/raw/ 아래 JSON 파일로
저장한다(인증키는 저장하지 않는다).
"""

from __future__ import annotations

import argparse
import json
import logging
import smtplib
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from grant_radar.api.bizinfo import BizinfoClient
from grant_radar.api.kstartup import FetchResult, KStartupApiError, KStartupClient
from grant_radar.config import ConfigError, load_mail_settings, load_settings
from grant_radar.models.company import CompanyDataError, load_company
from grant_radar.notify.mail import (
    ReportRef,
    build_failure_message,
    build_report_message,
    send_message,
)
from grant_radar.normalization.bizinfo import (
    BizinfoNormalizationError,
    normalize_bizinfo_page,
)
from grant_radar.normalization.kstartup import NormalizationError, normalize_page
from grant_radar.reporting.console import (
    render_console_report,
    render_json_report,
    render_markdown_report,
)
from grant_radar.services.evaluation import evaluate_stored
from grant_radar.services.ingestion import KST, IngestOutcome, ingest_page
from grant_radar.storage.sqlite import AnnouncementStore

RAW_DIR = Path("data") / "raw"
DB_PATH = Path("data") / "announcements.db"
COMPANY_PATH = Path("data") / "company.json"  # 실제 회사 데이터 (Git 제외)
SAMPLE_COMPANY_PATH = Path("data") / "sample_company.json"


def default_company_path() -> Path:
    """data/company.json이 있으면 그것을, 없으면 저장소 동봉 가상회사를 쓴다."""
    return COMPANY_PATH if COMPANY_PATH.is_file() else SAMPLE_COMPANY_PATH


# 수집 원천별 구성. endpoint/요청 필드는 원본 저장 파일의 기록용이다.
SOURCES: dict[str, dict] = {
    "kstartup": {
        "client": KStartupClient,
        "normalizer": normalize_page,
        "endpoint": "getAnnouncementInformation01",
        "request_fields": lambda result: {
            "page": result.page,
            "perPage": result.per_page,
            "returnType": "json",
        },
    },
    "bizinfo": {
        "client": BizinfoClient,
        "normalizer": normalize_bizinfo_page,
        "endpoint": "pblancBsnsService",
        "request_fields": lambda result: {
            "pageNo": result.page,
            "numOfRows": result.per_page,
            "dataType": "json",
        },
    },
}


def extract_total_count(data: Any, source: str) -> int | None:
    """응답에서 전체 건수를 읽는다. 구조가 다르거나 없으면 None."""
    try:
        if source == "bizinfo":
            value = data["response"]["body"]["totalCount"]
        else:
            value = data.get("totalCount")
        return int(value)
    except (KeyError, TypeError, ValueError):
        return None


def summarize_top_level(data: Any) -> list[str]:
    """응답 최상위 구조를 사람이 읽을 수 있게 요약한다.

    실제 응답 구조는 아직 확인 전이므로 특정 키의 존재를 가정하지 않고,
    자주 쓰이는 키가 있으면 참고용으로만 보여준다.
    """
    lines: list[str] = []
    if isinstance(data, dict):
        lines.append(f"최상위 구조: object, 키: {sorted(data.keys())}")
        for count_key in ("currentCount", "matchCount", "totalCount", "page", "perPage"):
            if count_key in data:
                lines.append(f"  {count_key}: {data[count_key]!r}")
        items = data.get("data")
        if isinstance(items, list):
            lines.append(f"  data 항목 수: {len(items)}")
            if items and isinstance(items[0], dict):
                lines.append(f"  첫 항목 필드: {sorted(items[0].keys())}")
    elif isinstance(data, list):
        lines.append(f"최상위 구조: array, 항목 수: {len(data)}")
    else:
        lines.append(f"최상위 구조: {type(data).__name__}, 값: {data!r}")
    return lines


def save_raw_result(result: FetchResult, raw_dir: Path, source: str = "kstartup") -> Path:
    """원본 응답을 저장한다. 인증키와 전체 요청 URL은 저장하지 않는다."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    spec = SOURCES[source]
    timestamp = result.fetched_at.strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"{source}_announcements_{timestamp}_p{result.page}.json"
    payload = {
        "fetched_at": result.fetched_at.isoformat(),
        "endpoint": spec["endpoint"],
        "request": spec["request_fields"](result),
        "status_code": result.status_code,
        "body": result.data,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_fetch(args: argparse.Namespace, client_factory=None) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    source = getattr(args, "source", "kstartup") or "kstartup"
    spec = SOURCES[source]
    factory = client_factory if client_factory is not None else spec["client"]

    pages = max(1, getattr(args, "pages", 1) or 1)
    all_outcomes: list[IngestOutcome] = []
    total = 0
    total_count: int | None = None
    try:
        with factory(settings.api_key) as client, AnnouncementStore(DB_PATH) as store:
            for offset in range(pages):
                page = args.page + offset
                if offset > 0:
                    # 연속 조회 간 대기. API 호출 한도가 미확인이므로 보수적으로 둔다.
                    time.sleep(0.5)
                result = client.fetch_announcements_page(page=page, per_page=args.per_page)
                print(
                    f"[성공] {source}: HTTP {result.status_code}, "
                    f"page={result.page}, perPage={result.per_page}"
                )
                if offset == 0:
                    for line in summarize_top_level(result.data):
                        print(line)
                if not args.no_save:
                    path = save_raw_result(result, RAW_DIR, source)
                    print(f"[저장] {path}")
                outcomes = ingest_page(
                    store,
                    result.data,
                    fetched_at=result.fetched_at,
                    normalizer=spec["normalizer"],
                )
                all_outcomes.extend(outcomes)
                page_total = extract_total_count(result.data, source)
                if page_total is not None:
                    total_count = page_total
                if not outcomes:
                    break  # 빈 페이지 = 더 가져올 데이터 없음
                if total_count is not None and page * args.per_page >= total_count:
                    break
            total = store.count()
    except KStartupApiError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        if all_outcomes:
            print(f"[중단] 오류 전까지 수집분 {len(all_outcomes)}건은 저장되어 있습니다.")
        return 1
    except (NormalizationError, BizinfoNormalizationError) as exc:
        print(f"[오류] 정규화 실패: {exc}", file=sys.stderr)
        return 1

    print(format_ingest_summary(all_outcomes, total))
    changed = [o for o in all_outcomes if o.change in ("NEW", "UPDATED")]
    if len(changed) <= 20:
        for outcome in changed:
            ann = outcome.announcement
            marker = " (마감)" if outcome.closed else ""
            print(f"  {outcome.change}: [{ann.source_id}] {ann.title}{marker}")
    else:
        print(f"  (신규·변경 {len(changed)}건 — 목록은 evaluate 보고서에서 확인)")
    return 0


def format_ingest_summary(outcomes: list[IngestOutcome], total_stored: int) -> str:
    def count(change: str) -> int:
        return sum(1 for o in outcomes if o.change == change)

    closed = sum(1 for o in outcomes if o.closed)
    return (
        f"[수집] 신규 {count('NEW')}건, 변경 {count('UPDATED')}건, "
        f"동일 {count('UNCHANGED')}건, 판단불가 {count('UNKNOWN')}건, "
        f"마감 {closed}건 (저장소 누적 {total_stored}건)"
    )


def run_evaluate(args: argparse.Namespace) -> int:
    try:
        company = load_company(args.company)
    except CompanyDataError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1

    if not DB_PATH.is_file():
        print(
            "저장된 공고가 없습니다. 먼저 fetch를 실행하세요: python -m grant_radar fetch",
            file=sys.stderr,
        )
        return 1

    # 필터: --since(그 시각 이후 처음 관측된 공고), run --new-only(이번 실행에서
    # 처음 관측된 공고), --open-only(모집 중만)
    filters: list[str] = []
    since: datetime | None = None
    since_text = getattr(args, "since", None)
    if since_text:
        try:
            since = datetime.fromisoformat(since_text)
        except ValueError:
            print(
                f"[오류] --since 형식이 올바르지 않습니다 (YYYY-MM-DD): {since_text}",
                file=sys.stderr,
            )
            return 1
        if since.tzinfo is None:
            since = since.replace(tzinfo=KST)
        filters.append(f"{since_text} 이후 신규")
    elif getattr(args, "new_only", False):
        since = getattr(args, "_run_started_at", None)
        if since is None:
            print("[오류] --new-only는 run 명령에서만 쓸 수 있습니다.", file=sys.stderr)
            return 1
        filters.append("이번 실행에서 새로 관측된 공고만")
    open_only = bool(getattr(args, "open_only", False))
    if open_only:
        filters.append("모집 중만")

    with AnnouncementStore(DB_PATH) as store:
        stored_total = store.count()
        evaluations = evaluate_stored(store, company, since=since)

    if stored_total == 0:
        print("저장된 공고가 없습니다. 먼저 fetch를 실행하세요.", file=sys.stderr)
        return 1
    if open_only:
        evaluations = [evaluation for evaluation in evaluations if not evaluation.closed]

    print(render_console_report(evaluations, company, filters))
    if not evaluations:
        print("\n필터 조건에 해당하는 공고가 없습니다.")
        return 0

    generated_at = datetime.now(KST)
    report_path = getattr(args, "report", None)
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_markdown_report(evaluations, company, generated_at, filters),
            encoding="utf-8",
        )
        print(f"\n[보고서] {path}")
    json_path = getattr(args, "json", None)
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_json_report(evaluations, company, generated_at, filters), encoding="utf-8"
        )
        print(f"[JSON] {path}")
    return 0


def run_run(args: argparse.Namespace, client_factory=None) -> int:
    """수집과 판정을 연속 수행한다. client_factory=None이면 --source에 따라 고른다."""
    args._run_started_at = datetime.now(KST)  # --new-only의 기준 시각
    exit_code = run_fetch(args, client_factory)
    if exit_code != 0:
        return exit_code
    print()
    return run_evaluate(args)


def build_report_refs(args: argparse.Namespace) -> list[ReportRef]:
    """--report/--json/--label을 위치로 짝지어 회사별 보고서 목록을 만든다.

    각 옵션은 여러 번 줄 수 있고(회사별 1개씩), 개수가 맞지 않으면 오류다.
    문자열 하나만 온 기존 호출 형태도 그대로 받는다.
    """

    def as_list(value) -> list:
        if value is None:
            return []
        return list(value) if isinstance(value, list) else [value]

    reports = as_list(getattr(args, "report", None))
    jsons = as_list(getattr(args, "json", None))
    labels = as_list(getattr(args, "label", None))
    if not reports:
        return [ReportRef(report=None, json=None, label=labels[0] if labels else None)]
    for name, values in (("--json", jsons), ("--label", labels)):
        if values and len(values) != len(reports):
            raise ValueError(
                f"{name} 개수({len(values)})가 --report 개수({len(reports)})와 다릅니다. "
                "회사별로 하나씩 같은 순서로 지정하세요."
            )
    return [
        ReportRef(
            report=Path(reports[i]),
            json=Path(jsons[i]) if i < len(jsons) else None,
            label=labels[i] if i < len(labels) else None,
        )
        for i in range(len(reports))
    ]


def run_mail(args: argparse.Namespace, smtp_factory=None) -> int:
    """보고서(또는 실패 로그)를 메일로 보낸다. scripts/weekly_run.ps1이 호출한다.

    --report 파일이 없으면 '신규 공고 없음' 메일을 보낸다 (run이 신규 0건이면
    보고서를 쓰지 않으므로). --failure-log가 주어지면 실패 메일만 보낸다.
    """
    try:
        settings = load_mail_settings()
    except ConfigError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    run_date = getattr(args, "date", None) or datetime.now(KST).strftime("%Y-%m-%d")
    failure_log = getattr(args, "failure_log", None)
    if failure_log:
        message = build_failure_message(settings, run_date, Path(failure_log))
    else:
        try:
            refs = build_report_refs(args)
        except ValueError as exc:
            print(f"[오류] {exc}", file=sys.stderr)
            return 1
        message = build_report_message(settings, run_date, refs)
    try:
        send_message(message, settings, smtp_factory)
    except (OSError, smtplib.SMTPException) as exc:
        # smtplib 예외 메시지에는 비밀번호가 들어가지 않는다 (서버 응답 코드·문구만).
        print(f"[오류] 메일 발송 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"[메일] {settings.to} ← {message['Subject']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grant_radar",
        description="K-Startup 지원사업 공고 수집 및 1차 선별 도구 (실험적 의사결정 보조)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="공고 목록 조회 및 원본 저장")
    run_parser = sub.add_parser("run", help="수집(fetch)과 판정(evaluate)을 연속 수행")
    for target in (fetch_parser, run_parser):
        target.add_argument(
            "--source",
            choices=sorted(SOURCES),
            default="kstartup",
            help="수집 원천 (기본 kstartup). bizinfo=기업마당 중소기업 지원사업",
        )
        target.add_argument("--page", type=int, default=1, help="시작 페이지 번호 (기본 1)")
        target.add_argument(
            "--per-page", type=int, default=5, help="페이지당 결과 수 (기본 5)"
        )
        target.add_argument(
            "--pages",
            type=int,
            default=1,
            help="시작 페이지부터 연속 조회할 페이지 수 (기본 1). "
            "전체 건수에 도달하면 조기 종료한다",
        )
        target.add_argument("--no-save", action="store_true", help="원본 응답을 저장하지 않음")

    evaluate_parser = sub.add_parser(
        "evaluate", help="저장된 공고를 회사 기준으로 1차 판정 (API 호출 없음)"
    )
    run_parser.add_argument(
        "--new-only",
        action="store_true",
        help="이번 실행에서 처음 관측된 공고만 판정·보고 (run 전용)",
    )
    for target in (evaluate_parser, run_parser):
        target.add_argument(
            "--open-only", action="store_true", help="모집 중(마감되지 않은) 공고만 보고"
        )
        target.add_argument(
            "--since",
            default=None,
            metavar="YYYY-MM-DD",
            help="이 날짜 이후 처음 관측된 공고만 보고 (KST 기준)",
        )
        target.add_argument(
            "--company",
            default=str(default_company_path()),
            help=f"회사 데이터 JSON 경로 (기본 {default_company_path()})",
        )
        target.add_argument(
            "--report",
            default=None,
            metavar="PATH",
            help="Markdown 보고서를 지정 경로에 저장 (예: reports/report.md)",
        )
        target.add_argument(
            "--json",
            default=None,
            metavar="PATH",
            help="판정 결과를 JSON으로 저장 (실행 간 비교·외부 검토용)",
        )

    mail_parser = sub.add_parser(
        "mail", help="보고서(또는 실패 로그)를 .env의 SMTP 설정으로 메일 발송 (정기 실행용)"
    )
    mail_parser.add_argument(
        "--report",
        action="append",
        default=None,
        metavar="PATH",
        help="보낼 Markdown 보고서. 회사별로 여러 번 지정하면 한 통에 절을 나눠 담는다",
    )
    mail_parser.add_argument(
        "--json",
        action="append",
        default=None,
        metavar="PATH",
        help="첨부할 JSON 결과 (--report와 같은 순서로 하나씩)",
    )
    mail_parser.add_argument(
        "--label",
        action="append",
        default=None,
        metavar="TEXT",
        help="보고서가 없을 때(신규 0건) 절 제목에 쓸 회사 이름 (--report와 같은 순서)",
    )
    mail_parser.add_argument(
        "--failure-log",
        default=None,
        metavar="PATH",
        help="지정하면 실행 실패 메일을 보낸다 (로그 끝 30줄 포함)",
    )
    mail_parser.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD", help="제목에 쓸 실행 날짜 (기본 오늘)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # 출력이 파이프/파일로 리다이렉트되면 Windows에서 cp949가 사용될 수 있다.
    # 공고 본문에 cp949로 표현 불가한 문자가 있어도 크래시하지 않도록 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "fetch":
        return run_fetch(args)
    if args.command == "evaluate":
        return run_evaluate(args)
    if args.command == "run":
        return run_run(args)
    if args.command == "mail":
        return run_mail(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
