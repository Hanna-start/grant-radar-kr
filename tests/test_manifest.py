"""수집 실행 매니페스트 테스트."""

import json

from grant_radar.reporting.manifest import update_run_manifest


def test_manifest_keeps_latest_run_per_source(tmp_path):
    path = tmp_path / "run_manifest.json"
    update_run_manifest(path, {"source": "kstartup", "status": "partial"})
    update_run_manifest(path, {"source": "bizinfo", "status": "complete"})
    update_run_manifest(path, {"source": "kstartup", "status": "complete"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["runs"] == [
        {"source": "bizinfo", "status": "complete"},
        {"source": "kstartup", "status": "complete"},
    ]


def test_corrupt_manifest_is_preserved(tmp_path):
    path = tmp_path / "run_manifest.json"
    path.write_text("not-json", encoding="utf-8")

    update_run_manifest(path, {"source": "kstartup", "status": "complete"})

    assert len(list(tmp_path.glob("run_manifest.json.corrupt-*"))) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["runs"][0]["source"] == "kstartup"
