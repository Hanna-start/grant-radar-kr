"""골든 스냅샷 테스트 — "매번 동일한 결론"의 회귀 고정 장치.

가상 공고 표본(data/fixtures/golden_announcements.json)에 대한 기대 판정을
tests/golden_expected.json에 스냅샷으로 고정한다. 규칙·정규화·판정 로직이
바뀌어 결론이 달라지면 이 테스트가 diff로 드러낸다.

의도된 변경이라면 재생성 후 diff를 검토하고 함께 커밋한다:
    $env:UPDATE_GOLDEN="1"; .venv\\Scripts\\python.exe -m pytest tests/test_golden.py -q
    (재생성 후 환경변수를 지우고 다시 실행해 통과를 확인)
"""

import json
import os
from datetime import datetime
from pathlib import Path

from grant_radar.normalization.kstartup import normalize_page
from grant_radar.rules.age import AgeRule
from grant_radar.rules.applicant_type import ApplicantTypeRule
from grant_radar.rules.business_age import BusinessAgeRule
from grant_radar.rules.region import RegionRule, load_region_mapping
from grant_radar.services.evaluation import evaluate_announcement
from grant_radar.services.ingestion import KST

from tests.factories import make_company

ROOT = Path(__file__).parent.parent
FIXTURE_PATH = ROOT / "data" / "fixtures" / "golden_announcements.json"
EXPECTED_PATH = Path(__file__).parent / "golden_expected.json"

# 골든 판정의 기준 시각 — 고정해야 마감 판정이 재현된다
AS_OF = datetime(2026, 7, 21, 12, 0, 0, tzinfo=KST)


def compute_actual() -> dict:
    body = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    rules = [
        RegionRule(load_region_mapping(ROOT / "data" / "reference" / "region_mapping.json")),
        BusinessAgeRule(),
        ApplicantTypeRule(),
        AgeRule(),
    ]
    company = make_company()
    actual = {}
    for announcement in normalize_page(body):
        evaluation = evaluate_announcement(announcement, company, rules, AS_OF)
        actual[announcement.source_id] = {
            "title": announcement.title,
            "decision": evaluation.decision.value,
            "closed": evaluation.closed,
            "rules": {r.rule_id: r.status.value for r in evaluation.rule_results},
        }
    return actual


def test_golden_decisions_are_stable():
    actual = compute_actual()
    if os.environ.get("UPDATE_GOLDEN"):
        EXPECTED_PATH.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    assert EXPECTED_PATH.is_file(), (
        "golden_expected.json이 없습니다. UPDATE_GOLDEN=1로 최초 생성한 뒤 "
        "내용을 검토하고 커밋하세요."
    )
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    assert actual == expected, (
        "골든 스냅샷과 판정 결론이 다릅니다. 의도된 규칙 변경이라면 "
        "UPDATE_GOLDEN=1로 재생성한 뒤 diff를 검토하고 함께 커밋하세요."
    )


def test_golden_covers_every_decision_and_closed_state():
    """표본이 판정 경로를 실제로 모두 커버하는지 자체 검증."""
    actual = compute_actual()
    decisions = {entry["decision"] for entry in actual.values()}
    assert decisions == {"ELIGIBLE", "REVIEW_REQUIRED", "INELIGIBLE"}
    assert any(entry["closed"] for entry in actual.values())
    assert any(not entry["closed"] for entry in actual.values())
    statuses = {status for entry in actual.values() for status in entry["rules"].values()}
    assert {"PASS", "FAIL", "REVIEW", "NOT_APPLICABLE"} <= statuses
