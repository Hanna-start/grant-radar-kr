"""수집 범위와 종료 상태를 기록하는 실행 매니페스트."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def update_run_manifest(path: str | Path, run: dict[str, Any]) -> Path:
    """원천별 최신 실행 상태를 원자적으로 저장한다.

    공고 본문, 검색어, 회사 정보, 인증키는 기록하지 않는다.
    기존 파일이 손상됐으면 덮어쓰지 않고 ``.corrupt`` 사본으로 보존한다.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []

    if target.is_file():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
            if current.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("지원하지 않는 매니페스트 스키마")
            if not isinstance(current.get("runs"), list):
                raise TypeError("runs가 배열이 아님")
            runs = [item for item in current["runs"] if isinstance(item, dict)]
        except (OSError, TypeError, ValueError, json.JSONDecodeError, AttributeError):
            corrupt = target.with_name(
                f"{target.name}.corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            os.replace(target, corrupt)

    source = run["source"]
    runs = [item for item in runs if item.get("source") != source]
    runs.append(run)
    payload = {"schema_version": SCHEMA_VERSION, "runs": runs}

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
