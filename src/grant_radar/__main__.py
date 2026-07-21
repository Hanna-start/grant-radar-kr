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

RAW_DIR = Path("data") / "raw"


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


def run_fetch(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        with KStartupClient(settings.api_key) as client:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.command == "fetch":
        return run_fetch(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
