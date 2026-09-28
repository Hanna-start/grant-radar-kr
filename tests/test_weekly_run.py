"""주간 스크립트 실행 검증. CLI 경계만 대역으로 바꿔 API·SMTP 접근을 막는다."""

import codecs
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell이 필요합니다")

RADAR_STUB = r"""
function Invoke-Radar([string[]]$RadarArgs) {
    ConvertTo-Json -InputObject @($RadarArgs) -Compress | Add-Content (Join-Path $Root "calls.jsonl") -Encoding UTF8
    if ($RadarArgs[0] -eq "fetch") {
        if ($env:RADAR_TEST_CASE -eq "missing-manifest") { return 0 }
        $Source = if ($RadarArgs -contains "bizinfo") { "bizinfo" } else { "kstartup" }
        $Status = if ($env:RADAR_TEST_CASE -eq "partial") { "partial" } else { "complete" }
        @{schema_version=1; runs=@(@{source=$Source; status=$Status; start_page=1; last_page=3; collected=6})} |
            ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Root "data\run_manifest.json") -Encoding UTF8
    }
    if ($RadarArgs[0] -eq "evaluate") {
        $Report = $RadarArgs[[Array]::IndexOf($RadarArgs, "--report") + 1]
        $Json = $RadarArgs[[Array]::IndexOf($RadarArgs, "--json") + 1]
        "- 결과 건수: 0" | Set-Content $Report -Encoding UTF8
        '{"summary":{"total":0},"results":[]}' | Set-Content $Json -Encoding UTF8
    }
    if ($RadarArgs[0] -eq "mail" -and $env:RADAR_TEST_CASE -eq "mail-failed") { return 1 }
    return 0
}
"""


@pytest.mark.parametrize("scenario", ["partial", "complete", "missing-manifest", "mail-failed"])
def test_weekly_reports_scope_and_preserves_state_on_failure(tmp_path, scenario):
    source = Path(__file__).resolve().parents[1] / "scripts" / "weekly_run.ps1"
    assert source.read_bytes().startswith(codecs.BOM_UTF8), (
        "Windows PowerShell 5.1에는 UTF-8 BOM 필요"
    )
    script = source.read_text(encoding="utf-8-sig")
    script, count = re.subn(
        r"^function Invoke-Radar\([^\n]*\n.*?^}",
        lambda _: RADAR_STUB.strip(),
        script,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert count == 1
    (tmp_path / "scripts").mkdir()
    target = tmp_path / "scripts/weekly_run.ps1"
    target.write_text(script, encoding="utf-8-sig")
    (tmp_path / ".venv/Scripts").mkdir(parents=True)
    (tmp_path / ".venv/Scripts/python.exe").touch()  # 존재 검사 전용, 대역이므로 실행하지 않음
    (tmp_path / "data").mkdir()
    (tmp_path / "data/sample_company.json").write_text("{}", encoding="utf-8")
    state = tmp_path / "data/last_success.txt"
    state.write_text("2026-01-01", encoding="ascii")
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(target),
        ],
        cwd=tmp_path,
        env={**os.environ, "RADAR_TEST_CASE": scenario},
        capture_output=True,
        check=False,
        timeout=30,
    )
    calls = [
        json.loads(line)
        for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8-sig").splitlines()
    ]
    mails = [call for call in calls if call[0] == "mail"]
    assert len(mails) == 1
    if scenario in {"missing-manifest", "mail-failed"}:
        assert result.returncode == 1, result.stderr
        assert state.read_text(encoding="ascii") == "2026-01-01"
        if scenario == "missing-manifest":
            assert "--failure-log" in mails[0]
            assert not any(call[0] == "evaluate" for call in calls)
    else:
        assert result.returncode == 0, result.stderr
        assert state.read_text(encoding="ascii").strip() != "2026-01-01"
        assert mails[0].count("--warning") == (2 if scenario == "partial" else 0)
        latest = tmp_path / "reports/weekly/latest-sample.md"
        assert "결과 건수: 0" in latest.read_text(encoding="utf-8-sig")
        if scenario == "partial":
            assert any("kstartup 부분 수집" in part for part in mails[0])
            assert any("bizinfo 부분 수집" in part for part in mails[0])
