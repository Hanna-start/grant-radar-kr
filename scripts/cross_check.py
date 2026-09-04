"""독립 구현 크로스체크 (상시 검증 하네스).

grant_radar 패키지를 임포트하지 않은 별도 로직으로, DB의 원본 응답(raw_json)과
회사 JSON만으로 판정을 처음부터 재계산해 파이프라인 결과와 전수 대조한다.
같은 코드를 두 번 실행하는 것이 아니라 서로 다른 두 구현의 결론을 비교한다.

실행 (프로젝트 루트에서):
    .venv\\Scripts\\python.exe scripts\\cross_check.py
    .venv\\Scripts\\python.exe scripts\\cross_check.py --company data\\company_example.json

종료 코드: 0 = 전 건 일치, 1 = 불일치 발견, 2 = 전제 조건 불충족

주의: 이 스크립트의 독립 판정부는 '서울 소재 법인 또는 개인사업자' 프로필을
전제로 단순화되어 있다. 대표자 연령은 생년월일(접수 기간 기준 만 나이) 또는
representative_age(기준일 시점 값)로 독립 계산한다. 회사 데이터가 전제를
벗어나면 아래 전제 검사에서 멈추며, 그 경우 독립 판정부도 함께 갱신해야 한다.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "announcements.db"
# CLI 기본값과 동일한 규칙: data/company.json(실제 회사, Git 제외)이 있으면
# 그것을, 없으면 저장소 동봉 가상회사를 쓴다.
_REAL_COMPANY = PROJECT_ROOT / "data" / "company.json"
_SAMPLE_COMPANY = PROJECT_ROOT / "data" / "sample_company.json"
COMPANY_PATH = _REAL_COMPANY if _REAL_COMPANY.is_file() else _SAMPLE_COMPANY
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

# 업종 키워드 표는 파이프라인과 공유하는 '입력 데이터'다 (DB와 같은 지위).
# 판정 로직 자체는 아래에 독립적으로 재구현한다.
KEYWORDS_PATH = PROJECT_ROOT / "data" / "reference" / "industry_keywords.json"
with open(KEYWORDS_PATH, encoding="utf-8") as _file:
    _KEYWORDS = json.load(_file)
_LATIN_PATTERNS = [
    re.compile("(?<![A-Za-z0-9])" + re.escape(word) + "(?![A-Za-z0-9])")
    for words in _KEYWORDS.get("restricted_sector_keywords_latin", {}).values()
    for word in words
]


def industry_status(item, company_keywords, source="kstartup"):
    if source == "bizinfo":
        parts = (item.get("pblancNm"), item.get("trgetNm"))
    else:
        parts = (item.get("biz_pbanc_nm"), item.get("aply_trgt_ctnt"))
    text = " / ".join(part for part in parts if part)
    if not text:
        return "NOT_APPLICABLE"
    if any(word in text for word in company_keywords):
        return "PASS"
    for words in _KEYWORDS["restricted_sector_keywords"].values():
        if any(word in text for word in words):
            return "REVIEW"
    if any(pattern.search(text) for pattern in _LATIN_PATTERNS):
        return "REVIEW"
    for words in _KEYWORDS.get("target_restriction_keywords", {}).values():
        if any(word in text for word in words):
            return "REVIEW"
    return "NOT_APPLICABLE"


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
    if established is None:
        # 설립일 정보 없음 — 예비창업자 전용 FAIL보다 앞선다 (파이프라인과 같은 순서)
        return "REVIEW"
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


def company_category_tokens(company):
    """회사 JSON에서 '명확히 해당하는' 신청 대상 범주를 뽑는다.

    중소기업·소상공인 지위는 회사 JSON의 확인된 선언값에서만 온다.
    """
    kind = company.get("business_type")
    tokens = set()
    if kind == "corporation":
        tokens |= {"일반기업", "법인사업자"}
    elif kind == "individual":
        tokens.add("개인사업자")
    if company.get("sme") is True:
        tokens.add("중소기업")
    if kind in ("corporation", "individual"):
        if company.get("small_business") is True:
            tokens.add("소상공인")
        if company.get("employee_count") == 1:
            tokens.add("1인 창조기업")
    return tokens


def applicant_status(item, categories):
    tokens = split_csv(item.get("aply_trgt"))
    return _applicant_from_tokens(tokens, categories)


def _applicant_from_tokens(tokens, categories):
    if not tokens:
        return "NOT_APPLICABLE"
    if categories & set(tokens):
        return "PASS"
    if set(tokens) <= NON_COMPANY:
        return "FAIL"
    return "REVIEW"


def _age_band(token):
    m = re.fullmatch(r"만\s*(\d+)세\s*미만", token)
    if m:
        return (0, int(m.group(1)) - 1)
    m = re.fullmatch(r"만\s*(\d+)세\s*이상\s*~\s*만\s*(\d+)세\s*이하", token)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"만\s*(\d+)세\s*이상", token)
    if m:
        return (int(m.group(1)), 10**9)
    return None


def _age_at(birth, on):
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def age_status(item, birth, rep_age):
    tokens = split_csv(item.get("biz_trgt_age"))
    if not tokens:
        return "NOT_APPLICABLE"
    bands = [b for b in (_age_band(t) for t in tokens) if b is not None]
    if len(bands) != len(tokens):
        return "REVIEW"  # 해석 불가 토큰
    # 성인 전 연령(만 20세 이상 전부) 허용이면 실질 제한 없음
    age = 20
    while age <= 120:
        covering = [b for b in bands if b[0] <= age <= b[1]]
        if not covering:
            break
        top = max(b[1] for b in covering)
        if top >= 10**9:
            return "PASS"
        age = top + 1
    else:
        return "PASS"
    if birth is not None:
        refs = [
            d
            for d in (
                parse_yyyymmdd(item.get("pbanc_rcpt_bgng_dt")),
                parse_yyyymmdd(item.get("pbanc_rcpt_end_dt")),
            )
            if d
        ]
        if not refs:
            return "REVIEW"
        ages = {_age_at(birth, d) for d in refs}
    elif rep_age is not None:
        ages = {rep_age}  # 회사 데이터의 연령(기준일 시점 값)
    else:
        return "REVIEW"
    if all(any(lo <= a <= hi for lo, hi in bands) for a in ages):
        return "PASS"
    return "REVIEW"  # 구간 밖·경계 통과 — 기준일 부재로 자동 제외 없음


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
# 기업마당(bizinfo) 독립 판정 — 관찰(2026-08-21) 기반, grant_radar 미사용
# ---------------------------------------------------------------------------


def bizinfo_region_status(item):
    # 관찰: 지역 한정 공고는 짧은 시도명 태그가 해당 지역만, 전국 공고는 17개
    # 시도 전부 나열. 짧은 시도명·그룹 표현만으로도 독립 판정에 충분하다.
    tags = split_csv(item.get("hashtags"))
    allowed = set()
    for tag in tags:
        if tag in GROUPS:
            allowed |= GROUPS[tag]
        elif tag in SIDO:
            allowed.add(tag)
    if not allowed:
        return "REVIEW"  # 지역 신호 없음 = 정보 부족
    return "PASS" if "서울" in allowed else "FAIL"


def bizinfo_applicant_status(item, categories):
    return _applicant_from_tokens(split_csv(item.get("trgetNm")), categories)


def bizinfo_is_closed(item, as_of):
    raw = str(item.get("reqstBeginEndDe") or "")
    match = re.search(r"~\s*(\d{4})-(\d{2})-(\d{2})\s*$", raw)
    if not match:
        return False  # 기간 미해석("예산 소진시까지" 등)은 마감 아님
    try:
        end = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return as_of.astimezone(KST).date() > end


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="독립 구현 크로스체크")
    parser.add_argument(
        "--company",
        default=str(COMPANY_PATH),
        metavar="PATH",
        help=f"대조할 회사 데이터 JSON (기본 {COMPANY_PATH})",
    )
    args = parser.parse_args(argv)
    company_path = Path(args.company)

    if not DB_PATH.is_file():
        print("저장된 공고가 없습니다. 먼저 fetch를 실행하세요.", file=sys.stderr)
        return 2

    with open(company_path, encoding="utf-8") as file:
        company = json.load(file)
    # 전제 검사 — 어긋나면 독립 판정부를 함께 갱신해야 한다
    problems = []
    if not isinstance(company.get("is_fictional"), bool):
        problems.append("is_fictional(true/false) 명시 없음")
    if company.get("headquarters_region") != "서울특별시":
        problems.append("본점이 서울특별시가 아님")
    if set(company.get("business_locations") or []) - {"서울특별시"}:
        problems.append("서울 외 사업장 존재")
    if company.get("business_type") not in ("corporation", "individual"):
        problems.append("법인·개인사업자가 아님")
    if problems:
        print("회사 데이터가 독립 검증 전제와 다릅니다:", "; ".join(problems), file=sys.stderr)
        print("scripts/cross_check.py의 독립 판정부를 함께 갱신하세요.", file=sys.stderr)
        return 2
    established_text = company.get("established_date")
    established = date.fromisoformat(established_text) if established_text else None
    birth_text = company.get("representative_birth_date")
    birth = date.fromisoformat(birth_text) if birth_text else None
    rep_age = company.get("representative_age")
    categories = company_category_tokens(company)
    company_keywords = company.get("matching_keywords") or []

    as_of = datetime.now(KST)

    mine = {}
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT source, source_id, raw_json FROM announcements"
    for source, source_id, raw in conn.execute(query):
        item = json.loads(raw)
        if source == "bizinfo":
            statuses = {
                "region": bizinfo_region_status(item),
                # 업력·연령 조건 필드가 원천에 없음 — 항상 NOT_APPLICABLE
                "bizage": "NOT_APPLICABLE",
                "applicant": bizinfo_applicant_status(item, categories),
                "age": "NOT_APPLICABLE",
                "industry": industry_status(item, company_keywords, source="bizinfo"),
            }
            closed = bizinfo_is_closed(item, as_of)
            title = (item.get("pblancNm") or "")[:44]
        else:
            statuses = {
                "region": region_status(item),
                "bizage": bizage_status(item, established),
                "applicant": applicant_status(item, categories),
                "age": age_status(item, birth, rep_age),
                "industry": industry_status(item, company_keywords),
            }
            closed = is_closed(item, as_of)
            title = (item.get("biz_pbanc_nm") or "")[:44]
        mine[(source, source_id)] = {
            "decision": overall(statuses),
            "closed": closed,
            "statuses": statuses,
            "title": title,
        }
    conn.close()

    # 파이프라인 결과 (대조 대상) — 여기서만 grant_radar를 사용한다
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from grant_radar.models.company import load_company
    from grant_radar.services.evaluation import evaluate_stored
    from grant_radar.storage.sqlite import AnnouncementStore

    with AnnouncementStore(DB_PATH) as store:
        pipeline = {
            (evaluation.announcement.source, evaluation.announcement.source_id): evaluation
            for evaluation in evaluate_stored(store, load_company(company_path), as_of=as_of)
        }

    if set(mine) != set(pipeline):
        print("공고 집합이 일치하지 않습니다.", file=sys.stderr)
        return 1

    mismatches = []
    for key, verdict in mine.items():
        result = pipeline[key]
        if verdict["decision"] != result.decision.value or verdict["closed"] != result.closed:
            mismatches.append((key, verdict, result))

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
    for key, verdict, result in mismatches:
        print(f"\n[불일치] {key[0]}:{key[1]} {verdict['title']}")
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
