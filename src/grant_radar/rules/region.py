"""지역 규칙 (region.v1) — 지시서 14.1절.

판정 원칙:
- 전국 대상 → PASS
- 회사 본점 소재지가 대상 지역에 명확히 포함 → PASS
- 본점은 불일치지만 추가 사업장이 일치 → REVIEW (소재지 요건 상세 확인 필요)
- 명확하게 다른 지역만 허용 → FAIL (조건부 허용 문구 확인은 human_checks로 남김)
- 지역 정보가 비어 있거나 매핑 불가 → REVIEW
- 문자열 포함 비교를 하지 않고 명시적 매핑표(data/reference/region_mapping.json)만 사용
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from grant_radar.models.announcement import NormalizedAnnouncement
from grant_radar.models.company import Company
from grant_radar.models.decision import Confidence, RuleResult, RuleStatus

DEFAULT_MAPPING_PATH = Path("data") / "reference" / "region_mapping.json"

RELOCATION_CHECK = (
    "공고 본문에 '해당 지역으로 이전 예정 기업 허용' 등 조건부 허용 문구가 있는지 확인하세요."
)


class RegionMappingError(Exception):
    """지역 매핑표를 읽을 수 없음."""


@dataclass(frozen=True)
class RegionMapping:
    canonical: frozenset[str]
    aliases: dict[str, str]
    groups: dict[str, object]  # 값: 정규 지역명 목록 또는 "ALL"

    def resolve(self, name: str) -> frozenset[str] | None:
        """지역 표현 하나를 정규 지역 집합으로 해석한다. 해석 불가면 None."""
        text = name.strip()
        if not text:
            return None
        if text in self.groups:
            value = self.groups[text]
            if value == "ALL":
                return self.canonical
            return frozenset(value)  # type: ignore[arg-type]
        if text in self.canonical:
            return frozenset({text})
        if text in self.aliases:
            return frozenset({self.aliases[text]})
        return None


def load_region_mapping(path: str | Path = DEFAULT_MAPPING_PATH) -> RegionMapping:
    file = Path(path)
    if not file.is_file():
        raise RegionMappingError(f"지역 매핑표를 찾을 수 없습니다: {file}")
    data = json.loads(file.read_text(encoding="utf-8-sig"))
    return RegionMapping(
        canonical=frozenset(data["canonical"]),
        aliases=dict(data["aliases"]),
        groups=dict(data["groups"]),
    )


class RegionRule:
    rule_id = "region.v1"

    def __init__(self, mapping: RegionMapping) -> None:
        self._mapping = mapping

    def evaluate(self, announcement: NormalizedAnnouncement, company: Company) -> RuleResult:
        region_text = announcement.region
        hq = company.headquarters_region

        def result(status, reason, confidence=Confidence.HIGH, human_checks=()):
            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                announcement_value=region_text,
                company_value=hq,
                reason=reason,
                evidence_field="supt_regin",
                confidence=confidence,
                human_checks=list(human_checks),
            )

        if region_text is None:
            return result(
                RuleStatus.REVIEW,
                "공고에 지역 정보가 없습니다. 본문에서 지역 조건을 확인해야 합니다.",
                Confidence.MEDIUM,
            )
        if hq is None:
            return result(
                RuleStatus.REVIEW,
                "회사 본점 소재지 정보가 없어 지역 조건을 비교할 수 없습니다.",
                Confidence.MEDIUM,
            )

        hq_resolved = self._mapping.resolve(hq)
        if hq_resolved is None:
            return result(
                RuleStatus.REVIEW,
                f"회사 소재지 {hq!r}를 지역 매핑표에서 해석할 수 없습니다.",
                Confidence.LOW,
            )
        (hq_canonical,) = hq_resolved if len(hq_resolved) == 1 else (None,)
        if hq_canonical is None:
            return result(
                RuleStatus.REVIEW,
                f"회사 소재지 {hq!r}가 단일 지역으로 해석되지 않습니다.",
                Confidence.LOW,
            )

        tokens = [token.strip() for token in region_text.split(",") if token.strip()]
        allowed: set[str] = set()
        unresolved: list[str] = []
        for token in tokens:
            resolved = self._mapping.resolve(token)
            if resolved is None:
                unresolved.append(token)
            else:
                allowed.update(resolved)

        if hq_canonical in allowed:
            if "전국" in tokens:
                reason = "공고가 전국 대상입니다."
            else:
                reason = f"회사 본점 소재지({hq})가 공고 대상 지역({region_text})에 포함됩니다."
            return result(RuleStatus.PASS, reason)

        if unresolved:
            return result(
                RuleStatus.REVIEW,
                f"해석할 수 없는 지역 표현이 있습니다: {', '.join(unresolved)}. "
                "매핑표에 없는 표현은 사람이 확인해야 합니다.",
                Confidence.LOW,
            )

        # 추가 사업장 확인
        for location in company.business_locations:
            loc_resolved = self._mapping.resolve(location)
            if loc_resolved is not None and loc_resolved & allowed:
                return result(
                    RuleStatus.REVIEW,
                    f"본점({hq})은 대상 지역이 아니지만 사업장({location})이 포함됩니다. "
                    "공고가 요구하는 소재지 기준(본점/사업장)을 확인해야 합니다.",
                    Confidence.MEDIUM,
                )

        return result(
            RuleStatus.FAIL,
            f"공고 대상 지역({region_text})에 회사 본점({hq})과 사업장이 포함되지 않습니다.",
            Confidence.HIGH,
            human_checks=[RELOCATION_CHECK],
        )
