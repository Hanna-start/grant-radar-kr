"""명령행 진입점.

사용 예:
    python -m grant_radar fetch --page 1 --per-page 5

`fetch`는 K-Startup 공고 목록 한 페이지를 조회해 최상위 구조를 요약 출력하고,
원본 응답을 data/raw/ 아래 JSON 파일로 저장한다(인증키는 저장하지 않는다).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from grant_radar.api.kstartup import FetchResult, KStartupApiError, KStartupClient
from grant_radar.config import ConfigError, load_settings
from grant_radar.models.company import CompanyDataError, load_company
from grant_radar.models.decision import Decision, EvaluationResult
from grant_radar.normalization.kstartup import NormalizationError
from grant_radar.services.evaluation import evaluate_stored
from grant_radar.services.ingestion import IngestOutcome, ingest_page
from grant_radar.storage.sqlite import AnnouncementStore

RAW_DIR = Path("data") / "raw"
DB_PATH = Path("data") / "announcements.db"
DEFAULT_COMPANY_PATH = Path("data") / "sample_company.json"

DECISION_LABELS = {
    Decision.ELIGIBLE: "지원 가능",
    Decision.REVIEW_REQUIRED: "판단 필요",
    Decision.INELIGIBLE: "지원 불가",
}

# 판정별 표시 우선순위 (지시서 19절). 마감 공고는 맨 뒤로 보낸다.
DECISION_ORDER = {
    Decision.ELIGIBLE: 0,
    Decision.REVIEW_REQUIRED: 1,
    Decision.INELIGIBLE: 2,
}


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


def save_raw_result(result: FetchResult, raw_dir: Path) -> Path:
    """원본 응답을 저장한다. 인증키와 전체 요청 URL은 저장하지 않는다."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = result.fetched_at.strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"kstartup_announcements_{timestamp}_p{result.page}.json"
    payload = {
        "fetched_at": result.fetched_at.isoformat(),
        "endpoint": "getAnnouncementInformation01",
        "request": {"page": result.page, "perPage": result.per_page, "returnType": "json"},
        "status_code": result.status_code,
        "body": result.data,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_fetch(args: argparse.Namespace, client_factory=KStartupClient) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        with client_factory(settings.api_key) as client:
            result = client.fetch_announcements_page(page=args.page, per_page=args.per_page)
    except KStartupApiError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1

    print(f"[성공] HTTP {result.status_code}, page={result.page}, perPage={result.per_page}")
    for line in summarize_top_level(result.data):
        print(line)

    if not args.no_save:
        path = save_raw_result(result, RAW_DIR)
        print(f"[저장] {path}")

    try:
        with AnnouncementStore(DB_PATH) as store:
            outcomes = ingest_page(store, result.data, fetched_at=result.fetched_at)
            total = store.count()
    except NormalizationError as exc:
        print(f"[오류] 정규화 실패: {exc}", file=sys.stderr)
        return 1

    print(format_ingest_summary(outcomes, total))
    for outcome in outcomes:
        if outcome.change in ("NEW", "UPDATED"):
            ann = outcome.announcement
            marker = " (마감)" if outcome.closed else ""
            print(f"  {outcome.change}: [{ann.source_id}] {ann.title}{marker}")
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


def sort_key(evaluation: EvaluationResult):
    """판정 우선순위 → 마감 임박 순 (지시서 19절)."""
    end_date = evaluation.announcement.application_end_at.value
    return (
        1 if evaluation.closed else 0,
        DECISION_ORDER.get(evaluation.decision, 9),
        end_date.toordinal() if end_date is not None else 10**9,
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

    with AnnouncementStore(DB_PATH) as store:
        evaluations = evaluate_stored(store, company)

    if not evaluations:
        print("저장된 공고가 없습니다. 먼저 fetch를 실행하세요.", file=sys.stderr)
        return 1

    evaluations.sort(key=sort_key)

    counts = {decision: 0 for decision in Decision}
    closed_count = 0
    for evaluation in evaluations:
        counts[evaluation.decision] += 1
        if evaluation.closed:
            closed_count += 1
    print(
        f"[판정 요약] 회사: {company.name} ({company.company_id}, 가상회사) / "
        f"공고 {len(evaluations)}건 — "
        f"지원 가능 {counts[Decision.ELIGIBLE]}, "
        f"판단 필요 {counts[Decision.REVIEW_REQUIRED]}, "
        f"지원 불가 {counts[Decision.INELIGIBLE]}, 마감 {closed_count}"
    )

    for evaluation in evaluations:
        ann = evaluation.announcement
        label = DECISION_LABELS[evaluation.decision]
        if evaluation.closed:
            label += " · 마감"
        print(f"\n[판정] {label}")
        print(f"[공고명] {ann.title or '(제목 없음)'} (공고번호 {ann.source_id})")
        start = ann.application_start_at.value or ann.application_start_at.raw or "?"
        end = ann.application_end_at.value or ann.application_end_at.raw or "?"
        print(f"[접수기간] {start} ~ {end}")
        if ann.detail_url:
            print(f"[상세페이지] {ann.detail_url}")
        for result in evaluation.rule_results:
            print(f"  - {result.rule_id}: {result.status.value} — {result.reason}")
            if result.announcement_value is not None:
                print(f"      공고 조건: {result.announcement_value}")
            if result.company_value is not None:
                print(f"      회사 정보: {result.company_value}")
        checks = evaluation.human_checks
        if checks:
            print("  추가 확인 사항:")
            for check in checks:
                print(f"    - {check}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grant_radar",
        description="K-Startup 지원사업 공고 수집 및 1차 선별 도구 (실험적 의사결정 보조)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_parser = sub.add_parser("fetch", help="공고 목록 한 페이지 조회 및 원본 저장")
    fetch_parser.add_argument("--page", type=int, default=1, help="페이지 번호 (기본 1)")
    fetch_parser.add_argument("--per-page", type=int, default=5, help="페이지당 결과 수 (기본 5)")
    fetch_parser.add_argument("--no-save", action="store_true", help="원본 응답을 저장하지 않음")

    evaluate_parser = sub.add_parser(
        "evaluate", help="저장된 공고를 가상회사 기준으로 1차 판정 (API 호출 없음)"
    )
    evaluate_parser.add_argument(
        "--company",
        default=str(DEFAULT_COMPANY_PATH),
        help=f"회사 데이터 JSON 경로 (기본 {DEFAULT_COMPANY_PATH})",
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
    return 2


if __name__ == "__main__":
    sys.exit(main())
