"""관련도 층 (relevance) — 자격 판정과 별개인 '보고서 배치' 기준.

자격 규칙이 남긴 공고를 회사별 관심 키워드로 정렬한다. 공통 설정은 안전한
기본값만 제공하고, 회사별 업종·관심·제외 정책은 Company 프로필에서 읽는다.

원칙:
- 자격 판정(decision)과 마감 판정은 건드리지 않는다. 크로스체크 대상도 아니다.
- C(관련 낮음)로 분류해도 보고서 부록에 제목·판정·링크를 남긴다 (놓침 방지).
- 설정은 data/reference/relevance.json (관찰 기반으로만 갱신).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company

DEFAULT_RELEVANCE_PATH = Path("data") / "reference" / "relevance.json"

TIER_LABELS = {"A": "관련 높음", "B": "보통", "C": "관련 낮음"}


class RelevanceConfigError(Exception):
    """관련도 설정표 로드 실패."""


@dataclass(frozen=True)
class Relevance:
    tier: str  # "A" | "B" | "C"
    score: int
    reasons: list[str] = field(default_factory=list)
    # True면 보고서 본문·부록에 싣지 않고 건수만 표시한다 (JSON에는 남긴다).
    # 회사 소재지가 아닌 타 자치구 한정 공고는 관련도를 낮춘다.
    hidden: bool = False

    @property
    def label(self) -> str:
        return TIER_LABELS[self.tier]


def load_relevance_config(path: Path | None = None) -> dict:
    path = path or DEFAULT_RELEVANCE_PATH
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RelevanceConfigError(f"관련도 설정표를 찾을 수 없습니다: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RelevanceConfigError(f"관련도 설정표를 읽을 수 없습니다: {path}: {exc}") from exc
    for key in ("exclude_title_keywords", "source_category_policy", "positive_keywords"):
        if key not in data:
            raise RelevanceConfigError(f"관련도 설정표에 {key}가 없습니다: {path}")
    return data


def _latin_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])", re.IGNORECASE)


class RelevanceScorer:
    def __init__(self, config: dict) -> None:
        self._exclude_title = list(config.get("exclude_title_keywords", []))
        self._exclude_title_latin = [
            _latin_pattern(word) for word in config.get("exclude_title_keywords_latin", [])
        ]
        self._exclude_body = list(config.get("exclude_body_phrases", []))
        self._policy = dict(config.get("source_category_policy", {}))
        self._positive: dict[str, list[str]] = dict(config.get("positive_keywords", {}))
        self._strong_groups = set(config.get("strong_groups_in_title", []))
        # 강한 제목 신호 전용 키워드 (없으면 긍정 키워드 전체를 사용)
        self._strong_title: dict[str, list[str]] = {
            group: list(
                config.get("strong_title_keywords", {}).get(group, self._positive.get(group, []))
            )
            for group in self._strong_groups
        }
        self._demote_title = list(config.get("demote_title_keywords", []))
        self._demote_title_latin = [
            _latin_pattern(w) for w in self._demote_title if re.fullmatch(r"[A-Za-z0-9]+", w)
        ]
        self._demote_title = [w for w in self._demote_title if not re.fullmatch(r"[A-Za-z0-9]+", w)]
        self._title_signal_categories = set(
            config.get("tier_a_requires_title_signal_categories", [])
        )
        # 제목의 제외 키워드(창업·행사형)를 무시할 수 있는 강한 신호 그룹.
        # 지역·대상 신호는 A 승격에는 쓰지만 제외를 뚫지는 못한다
        # ("[서울] 창업경진대회"가 A가 되는 것을 막기 위함, 2026-08-22 검수).
        self._override_groups = set(config.get("exclusion_override_groups", ["업종", "고용"]))
        # 단독으로는 A를 만들지 못하는 강한 신호 그룹 (다른 긍정 신호가 함께 있어야 함)
        self._needs_support = set(config.get("strong_title_needs_support", []))
        self._seoul_districts = list(config.get("seoul_districts", []))
        self._city_stopwords = set(config.get("city_token_stopwords", []))
        # 제목의 시·군 토큰: "과천시", "춘천시", "완도군" (앞뒤가 한글이 아닌 경우만)
        self._city_pattern = re.compile(r"(?<![가-힣])([가-힣]{2,4}[시군])(?![가-힣])")
        rules = config.get("tier_a_rules", {})
        self._body_groups = set(config.get("body_groups", ["업종", "고용", "자금"]))
        self._min_distinct = int(rules.get("min_distinct_positive", 2))
        self._priority_any = set(rules.get("priority_categories_with_any_positive", []))

    def category_rank(self, announcement: NormalizedAnnouncement) -> int:
        """정렬용: 기업마당 분야 우선순위(작을수록 앞). 그 외는 뒤."""
        policy = self._policy.get(announcement.source, {})
        priority = policy.get("priority", [])
        if announcement.support_category in priority:
            return priority.index(announcement.support_category)
        return len(priority) + 1

    def locality_mismatch(
        self, announcement: NormalizedAnnouncement, company: Company | None
    ) -> tuple[list[str], list[str]]:
        """제목의 자치구·시군 토큰을 회사 사업장과 대조한다.

        반환: (회사 밖 지자체 토큰, 회사 자치구와 일치한 토큰).
        회사 자치구 정보가 없으면 대조하지 않는다.
        """
        if company is None or not company.business_districts:
            return [], []
        title = announcement.title or ""
        home = set(company.business_districts)
        found_districts = [d for d in self._seoul_districts if d in title]
        matched = [d for d in found_districts if d in home]
        foreign = [d for d in found_districts if d not in home]
        if company.headquarters_region and company.headquarters_region.startswith("서울"):
            for token in self._city_pattern.findall(title):
                if token in self._city_stopwords or token in home:
                    continue
                if token.endswith("시") and len(token) <= 2:
                    continue
                foreign.append(token)
        return foreign, matched

    def score(
        self,
        announcement: NormalizedAnnouncement,
        industry_flagged: bool = False,
        company: Company | None = None,
    ) -> Relevance:
        """관련도 계산.

        industry_flagged: 업종 규칙(industry.v1)이 다른 업종·대상 신호를 감지해
        REVIEW를 낸 경우 True — 이때는 A로 올리지 않는다 (B 상한).
        """
        title = announcement.title or ""
        target = announcement.target_description or ""
        body = announcement.summary or ""
        head = f"{title} / {target}"
        reasons: list[str] = []

        positive = {group: list(words) for group, words in self._positive.items()}
        if company is not None:
            if company.matching_keywords:
                positive["회사 업종"] = list(company.matching_keywords)
            for group, words in company.interest_keywords.items():
                current = positive.setdefault(group, [])
                current.extend(word for word in words if word not in current)

        # 긍정 신호. 제목·신청대상(head)은 모든 그룹, 본문(body)은 설정된 그룹
        # 그룹만 인정한다 — 본문의 '홍보·안전·스마트' 같은 범용어로 점수가
        # 올라가는 것을 막기 위함 (2026-08-22 1,722건 검수 결과).
        hits: dict[str, list[str]] = {}
        for group, words in positive.items():
            matched = [w for w in words if w in head]
            if group in self._body_groups:
                matched += [w for w in words if w not in matched and w in body]
            if matched:
                hits[group] = matched
        distinct = sum(len(v) for v in hits.values())
        strong_in_title = [
            group
            for group in self._strong_groups
            if any(w in title for w in self._strong_title.get(group, positive.get(group, [])))
        ]
        company_demoted = company.demote_keywords if company is not None else []
        demoted_by = [w for w in self._demote_title + company_demoted if w in title]
        demoted_by += [p.pattern for p in self._demote_title_latin if p.search(title)]

        # 제외 신호 — 제목·신청대상의 프로그램 유형 키워드 (강한 긍정 신호가 제목에
        # 있으면 제외하지 않는다: 예 "인력양성 무료교육")
        company_excluded = company.exclude_keywords if company is not None else []
        excluded_by = [w for w in self._exclude_title + company_excluded if w in head]
        excluded_by += [p.pattern for p in self._exclude_title_latin if p.search(head)]
        body_excluded = [p for p in self._exclude_body if p in body]

        policy = self._policy.get(announcement.source, {})
        category = announcement.support_category
        company_dropped = company.exclude_categories if company is not None else []
        category_dropped = category in set(policy.get("drop", [])) | set(company_dropped)
        appendix_unless_positive = category in set(policy.get("appendix_unless_positive", []))

        foreign, matched_districts = self.locality_mismatch(announcement, company)
        if foreign and not matched_districts:
            reasons.append(
                f"타 지자체 한정 가능성: {', '.join(sorted(set(foreign))[:3])} "
                "(관내 사업장 요건 확인)"
            )
            return Relevance("C", -1, reasons, hidden=True)
        if matched_districts:
            reasons.append(f"회사 사업장 자치구: {', '.join(matched_districts)}")
            if "지역" in self._strong_groups and "지역" not in strong_in_title:
                strong_in_title.append("지역")
            hits.setdefault("지역", []).extend(
                d for d in matched_districts if d not in hits.get("지역", [])
            )

        overriding = [g for g in strong_in_title if g in self._override_groups]
        if strong_in_title:
            reasons.append(f"제목에 회사 관련 신호: {', '.join(strong_in_title)}")
        if not overriding and (excluded_by or body_excluded):
            words = sorted(set(excluded_by + body_excluded))
            reasons.append(f"회사 비관심 프로그램 신호: {', '.join(words)}")
            return Relevance("C", -1, reasons)

        if category_dropped:
            reasons.append(f"{announcement.source} 분류 '{category}'는 회사 제외 정책")
            return Relevance("C", -1, reasons)
        if appendix_unless_positive and not hits:
            reasons.append(f"분야 '{category}'이고 회사 관련 키워드 없음")
            return Relevance("C", -1, reasons)

        for group, matched in hits.items():
            reasons.append(f"{group}: {', '.join(matched[:4])}")

        tier = "B"
        standalone_strong = [g for g in strong_in_title if g not in self._needs_support]
        supported_strong = [
            g for g in strong_in_title if g in self._needs_support and any(h != g for h in hits)
        ]
        if (
            standalone_strong
            or supported_strong
            or distinct >= self._min_distinct
            or category in self._priority_any
            and hits
        ):
            tier = "A"
        if tier == "A" and industry_flagged:
            tier = "B"
            reasons.append("업종 규칙이 다른 업종·대상 신호를 감지해 A로 올리지 않음")
        if tier == "A" and demoted_by:
            tier = "B"
            reasons.append(f"제목에 회사와 거리가 있는 분야 신호: {', '.join(demoted_by[:3])}")
        if tier == "A" and category in self._title_signal_categories and not strong_in_title:
            tier = "B"
            reasons.append(f"분야 '{category}'는 제목에 회사 업종·고용 신호가 있을 때만 A")
        if tier == "A" and category:
            reasons.append(f"분야 '{category}'")
        if not reasons:
            reasons.append("회사 관련 신호 없음 (자격 조건만 통과)")
        return Relevance(tier, distinct, reasons)
