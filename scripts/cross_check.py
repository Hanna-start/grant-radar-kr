# -*- coding: utf-8 -*-
"""독립 구현 크로스체크 (상시 검증 하네스).

grant_radar 패키지를 임포트하지 않은 별도 로직으로, DB의 원본 응답(raw_json)과
회사 JSON만으로 판정을 처음부터 재계산해 파이프라인 결과와 전수 대조한다.
같은 코드를 두 번 실행하는 것이 아니라 서로 다른 두 구현의 결론을 비교한다.

실행 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe scripts\\cross_check.py

종료 코드: 0 = 전 건 일치, 1 = 불일치 발견, 2 = 전제 조건 불충족

주의: 이 스크립트의 독립 판정부는 기본 가상회사(서울 법인, 대표 생년월일
없음)를 전제로 단순화되어 있다. 회사 데이터를 바꾸면 아래 전제 검사에서
멈추며, 그 경우 독립 판정부도 함께 갱신해야 한다.
"""

import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "announcements.db"
COMPANY_PATH = PROJECT_ROOT / "data" / "sample_company.json"
KST = timezone(timedelta(hours=9), "KST")

# ---------------------------------------------------------------------------
# 독립 판정 로직 (grant_radar 미사용 — 의도적으로 별도 작성)
# ---------------------------------------------------------------------------

SIDO = {
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
}
GROUPS = {
    "전국": SIDO,
    "수도권": {"서울", "인천", "경기"},
    "비수도권": SIDO - {"서울", "인천", "경기"},
    "전남광주": {"전남", "광주"},
}
NON_COMPANY = {"청소년", "대학생", "대학", "연구기관", "공공기관"}
AGE_TOKENS = {"만 20세 미만", "만 20세 이상 ~ 만 39세 이하", "만 40세 이상"}


def parse_yyyymmdd(value):
    if value is None:
        return None
    match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", str(value).strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def split_csv(value):
    if not value or not str(value).strip():
        return []
    return [token.strip() for token in str(value).split(",") if token.strip()]


def region_status(item):
    tokens = split_csv(item.get("supt_regin"))
    if not tokens:
        return "REVIEW"
    allowed = set()
    unknown = False
    for token in tokens:
        if token in GROUPS:
            allowed |= GROUPS[token]
        elif token in SIDO:
            allowed.add(token)
        else:
            unknown = True
    if "서울" in allowed:  # 전제: 회사 본점·사업장 모두 서울
        return "PASS"
    return "REVIEW" if unknown else "FAIL"


def bizage_status(item, established):
    tokens = split_csv(item.get("biz_enyy"))
    if not tokens:
        return "NOT_APPLICABLE"
    bounds, unknown, pre = [], [], False
    for token in tokens:
        if token == "예비창업자":
            pre = True
            continue
        match = re.fullmatch(r"(\d+)년미만", token)
        if match:
            bounds.append(int(match.group(1)))
        else:
            unknown.append(token)
    if pre and not bounds and not unknown:
        return "FAIL"  # 예비창업자 전용 vs 설립기업 (기준일 무관)
    if bounds:
        end = parse_yyyymmdd(item.get("pbanc_rcpt_end_dt"))
        if end is None:
            return "REVIEW"
        cutoff = date(established.year + max(bounds), established.month, established.day)
        return "PASS" if end < cutoff else "REVIEW"
    return "REVIEW"


def applicant_status(item):
    tokens = split_csv(item.get("aply_trgt"))
    if not tokens:
        return "NOT_APPLICABLE"
    if "일반기업" in tokens or "법인사업자" in tokens:  # 전제: 법인, 직원 2인 이상
        return "PASS"
    if set(tokens) <= NON_COMPANY:
        return "FAIL"
    return "REVIEW"


def age_status(item):
    tokens = split_csv(item.get("biz_trgt_age"))
    if not tokens:
        return "NOT_APPLICABLE"
    if "만 20세 이상 ~ 만 39세 이하" in tokens and "만 40세 이상" in tokens:
        return "PASS"  # 성인 전 연령 허용 = 실질 제한 없음
    return "REVIEW"  # 전제: 대표자 생년월일 없음 (미지 토큰 포함 시에도 REVIEW)


def overall(statuses):
    if "FAIL" in (statuses["region"], statuses["bizage"], statuses["applicant"]):
        return "INELIGIBLE"
    if any(status == "REVIEW" for status in statuses.values()):
        return "REVIEW_REQUIRED"
    if any(status == "PASS" for status in statuses.values()):
        return "ELIGIBLE"
    return "REVIEW_REQUIRED"


def is_closed(item, as_of):
    value = item.get("rcrt_prgs_yn")
    if isinstance(value, str) and value.strip().upper() == "N":
        return True
    end = parse_yyyymmdd(item.get("pbanc_rcpt_end_dt"))
    return end is not None and as_of.astimezone(KST).date() > end


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def main() -> int:
    if not DB_PATH.is_file():
        print("저장된 공고가 없습니다. 먼저 fetch를 실행하세요.", file=sys.stderr)
        return 2

    with open(COMPANY_PATH, encoding="utf-8") as file:
        company = json.load(file)
    # 전제 검사 — 어긋나면 독립 판정부를 함께 갱신해야 한다
    problems = []
    if company.get("is_fictional") is not True:
        problems.append("is_fictional=true가 아님")
    if company.get("headquarters_region") != "서울특별시":
        problems.append("본점이 서울특별시가 아님")
    if set(company.get("business_locations") or []) - {"서울특별시"}:
        problems.append("서울 외 사업장 존재")
    if company.get("business_type") != "corporation":
        problems.append("법인이 아님")
    if company.get("representative_birth_date") is not None:
        problems.append("대표자 생년월일이 설정됨")
    if (company.get("employee_count") or 0) == 1:
        problems.append("1인 기업(1인 창조기업 범주 판정 분기 필요)")
    if problems:
        print("회사 데이터가 독립 검증 전제와 다릅니다:", "; ".join(problems), file=sys.stderr)
        print("scripts/cross_check.py의 독립 판정부를 함께 갱신하세요.", file=sys.stderr)
        return 2
    established = date.fromisoformat(company["established_date"])

    as_of = datetime.now(KST)

    mine = {}
    conn = sqlite3.connect(DB_PATH)
    for source_id, raw in conn.execute("SELECT source_id, raw_json FROM announcements"):
        item = json.loads(raw)
        statuses = {
            "region": region_status(item),
            "bizage": bizage_status(item, established),
            "applicant": applicant_status(item),
            "age": age_status(item),
        }
        mine[source_id] = {
            "decision": overall(statuses),
            "closed": is_closed(item, as_of),
            "statuses": statuses,
            "title": (item.get("biz_pbanc_nm") or "")[:44],
        }
    conn.close()

    # 파이프라인 결과 (대조 대상) — 여기서만 grant_radar를 사용한다
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from grant_radar.models.company import load_company
    from grant_radar.services.evaluation import evaluate_stored
    from grant_radar.storage.sqlite import AnnouncementStore

    with AnnouncementStore(DB_PATH) as store:
        pipeline = {
            evaluation.announcement.source_id: evaluation
            for evaluation in evaluate_stored(store, load_company(COMPANY_PATH), as_of=as_of)
        }

    if set(mine) != set(pipeline):
        print("공고 집합이 일치하지 않습니다.", file=sys.stderr)
        return 1

    mismatches = []
    for source_id, verdict in mine.items():
        result = pipeline[source_id]
        if verdict["decision"] != result.decision.value or verdict["closed"] != result.closed:
            mismatches.append((source_id, verdict, result))

    print(
        f"대조 대상: {len(mine)}건 (독립 구현 vs 파이프라인, 기준 시각 {as_of.isoformat(timespec='seconds')})"
    )
    print(
        "독립 구현 분포:",
        dict(Counter(v["decision"] for v in mine.values())),
        "/ 마감",
        sum(1 for v in mine.values() if v["closed"]),
    )
    print(
        "파이프라인 분포:",
        dict(Counter(r.decision.value for r in pipeline.values())),
        "/ 마감",
        sum(1 for r in pipeline.values() if r.closed),
    )

    if not mismatches:
        print("결과: 전 건 일치 ✔")
        return 0

    print(f"결과: 불일치 {len(mismatches)}건 ✘")
    for source_id, verdict, result in mismatches:
        print(f"\n[불일치] {source_id} {verdict['title']}")
        print(
            f"  독립 구현: {verdict['decision']} closed={verdict['closed']} {verdict['statuses']}"
        )
        print(
            f"  파이프라인: {result.decision.value} closed={result.closed} "
            f"{[(r.rule_id, r.status.value) for r in result.rule_results]}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
