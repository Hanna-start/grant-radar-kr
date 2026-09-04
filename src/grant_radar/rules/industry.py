"""업종·분야 규칙 (industry.v1).

API에 신청기업 업종의 구조화 필드가 없다(2026-08-21 관찰, docs/api-observations.md).
그래서 제목(biz_pbanc_nm)과 신청 대상 원문(aply_trgt_ctnt)의 키워드로
업종·분야 제한 '후보'를 감지한다.

원칙:
- 키워드 감지는 불완전하므로 이 규칙은 자동 제외(FAIL)를 내지 않는다
  (CLAUDE.md: 자연어 해석만으로 공고 자동 제외 금지). 제한 후보 감지 시 REVIEW.
- 회사 업종 키워드가 발견되면 제한 후보 키워드가 함께 있어도 PASS 한다
  (놓침 방지 우선 — region.v1의 부분 해석 PASS와 같은 설계). 다른 분야
  키워드가 함께 있으면 human_checks로 확인을 요청한다.
- 키워드 표(data/reference/industry_keywords.json)는 실제 저장 공고 1,000건의
  빈도 관찰로 작성했다. 라틴 문자 키워드(AI 등)는 영단어 경계로만 일치시켜
  다른 단어 안의 부분 문자열 오탐을 막는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import Confidence, RuleResult, RuleStatus

DEFAULT_KEYWORDS_PATH = Path("data") / "reference" / "industry_keywords.json"

BODY_CHECK = "공고 본문·첨부의 업종·분야 조건을 확인하세요 (키워드 감지는 불완전합니다)."


class IndustryKeywordError(Exception):
    """키워드 표 로드 실패."""


def load_industry_keywords(path: Path | None = None) -> dict:
    path = path or DEFAULT_KEYWORDS_PATH
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IndustryKeywordError(f"업종 키워드 표를 찾을 수 없습니다: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IndustryKeywordError(f"업종 키워드 표를 읽을 수 없습니다: {path}: {exc}") from exc
    for key in ("restricted_sector_keywords",):
        if key not in data:
            raise IndustryKeywordError(f"업종 키워드 표에 {key}가 없습니다: {path}")
    return data


def _latin_pattern(keyword: str) -> re.Pattern[str]:
    # 영단어 경계 일치: "AI"가 "MAIN" 안에서 오탐되지 않도록 한다.
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])")


class IndustryRule:
    rule_id = "industry.v1"

    def __init__(self, keywords: dict) -> None:
        self._sectors: dict[str, list[str]] = dict(keywords.get("restricted_sector_keywords", {}))
        self._latin_sectors: dict[str, list[tuple[str, re.Pattern[str]]]] = {
            sector: [(keyword, _latin_pattern(keyword)) for keyword in words]
            for sector, words in keywords.get("restricted_sector_keywords_latin", {}).items()
        }
        self._target_groups: dict[str, list[str]] = dict(
            keywords.get("target_restriction_keywords", {})
        )

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult:
        company_keywords = company.matching_keywords
        parts = [announcement.title, announcement.target_description]
        text = " / ".join(part for part in parts if part)

        def result(status, announcement_value, reason, human_checks=()):
            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                announcement_value=announcement_value,
                company_value=", ".join(company_keywords) or None,
                reason=reason,
                evidence_field=(
                    "pblancNm,trgetNm"
                    if announcement.source == "bizinfo"
                    else "biz_pbanc_nm,aply_trgt_ctnt"
                ),
                confidence=Confidence.MEDIUM,
                human_checks=list(human_checks),
            )

        if not text:
            return result(
                RuleStatus.NOT_APPLICABLE,
                None,
                "제목·신청 대상 텍스트가 없어 업종 신호를 확인할 수 없습니다.",
                human_checks=[BODY_CHECK],
            )

        company_hits = [keyword for keyword in company_keywords if keyword in text]

        sector_hits: dict[str, list[str]] = {}
        for sector, words in self._sectors.items():
            hits = [keyword for keyword in words if keyword in text]
            if hits:
                sector_hits.setdefault(sector, []).extend(hits)
        for sector, pairs in self._latin_sectors.items():
            matched = [keyword for keyword, pattern in pairs if pattern.search(text)]
            if matched:
                sector_hits.setdefault(sector, []).extend(matched)

        target_hits: dict[str, list[str]] = {}
        for group, words in self._target_groups.items():
            hits = [keyword for keyword in words if keyword in text]
            if hits:
                target_hits[group] = hits

        if company_hits:
            checks = [BODY_CHECK]
            if sector_hits or target_hits:
                others = sorted(set(sector_hits) | set(target_hits))
                checks.append(
                    f"다른 분야·대상 신호도 함께 감지되었습니다: {', '.join(others)}. "
                    "회사 업종이 실제 지원 대상에 포함되는지 확인하세요."
                )
            return result(
                RuleStatus.PASS,
                ", ".join(company_hits),
                f"회사 업종 관련 키워드가 공고에 있습니다: {', '.join(company_hits)}.",
                human_checks=checks,
            )

        if sector_hits or target_hits:
            flagged = sorted(set(sector_hits) | set(target_hits))
            matched_words = sorted(
                {word for hits in (*sector_hits.values(), *target_hits.values()) for word in hits}
            )
            return result(
                RuleStatus.REVIEW,
                ", ".join(matched_words),
                "특정 업종·분야·대상 제한 가능성이 있는 키워드가 감지되었습니다: "
                f"{', '.join(flagged)}. 키워드 감지만으로 제외하지 않으므로 본문 확인이 "
                "필요합니다.",
                human_checks=[BODY_CHECK],
            )

        return result(
            RuleStatus.NOT_APPLICABLE,
            None,
            "업종·분야 제한 신호가 감지되지 않았습니다.",
            human_checks=[BODY_CHECK],
        )
