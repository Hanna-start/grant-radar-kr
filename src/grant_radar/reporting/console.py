"""판정 결과 보고서 (지시서 19절 형식).

원칙:
- 사람이 결과만 읽고 판정 이유를 이해할 수 있어야 한다.
- 제외 결과에도 이유를 표시한다.
- 판정 우선순위(우선 검토 → 판단 필요 → 지원 불가), 같은 판정 안에서는
  마감일이 가까운 공고 먼저. 마감 공고는 맨 뒤로 보낸다.
"""

from __future__ import annotations

import json
from datetime import datetime

from grant_radar.models.company import Company
from grant_radar.models.decision import Decision, EvaluationResult, RuleStatus
from grant_radar.relevance import (
    TIER_LABELS,
    Relevance,
    RelevanceScorer,
    load_relevance_config,
)

# ELIGIBLE 라벨은 "지원 가능"이 아니다: 구조화 필드 4종만 통과한 상태이며
# 본문·첨부에만 있는 제한(validation-sample.md 4절)이 남아 있을 수 있다.
DECISION_LABELS = {
    Decision.ELIGIBLE: "우선 검토(구조화 조건 통과)",
    Decision.REVIEW_REQUIRED: "판단 필요",
    Decision.INELIGIBLE: "지원 불가",
}

DECISION_ORDER = {
    Decision.ELIGIBLE: 0,
    Decision.REVIEW_REQUIRED: 1,
    Decision.INELIGIBLE: 2,
}

RULE_LABELS = {
    "region.v1": "지역",
    "business_age.v1": "업력",
    "applicant_type.v1": "신청자 유형",
    "age.v1": "대표자 연령",
}

STATUS_LABELS = {
    RuleStatus.PASS: "통과",
    RuleStatus.FAIL: "불일치",
    RuleStatus.REVIEW: "판단 필요",
    RuleStatus.NOT_APPLICABLE: "해당 없음",
    RuleStatus.ERROR: "오류",
}

# 1차 규칙이 다루지 않는 상세 검토 항목 (지시서 17절). 자동 판정하지 않고
# 사람이 확인하도록 안내만 한다. 지원 불가 판정에는 붙이지 않는다.
STANDARD_CHECKS = [
    "공고 본문·첨부파일의 세부 자격 요건 (신청 제외 대상 포함)",
    "중복수혜 제한",
    "자부담 조건",
    "필수 제출서류",
]

DISCLAIMER = (
    "이 보고서는 공식 자격 판정이 아닙니다. K-Startup 공개 데이터의 구조화된 "
    "필드만 비교한 검토 우선순위이며, 지원 여부는 반드시 공고 본문과 첨부파일을 "
    "확인한 뒤 판단해야 합니다."
)


def sort_key(evaluation: EvaluationResult):
    end_date = evaluation.announcement.application_end_at.value
    return (
        1 if evaluation.closed else 0,
        DECISION_ORDER.get(evaluation.decision, 9),
        end_date.toordinal() if end_date is not None else 10**9,
    )


TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def _default_scorer() -> RelevanceScorer:
    return RelevanceScorer(load_relevance_config())


def score_all(
    evaluations: list[EvaluationResult],
    scorer: RelevanceScorer | None = None,
    company: Company | None = None,
) -> list[tuple[EvaluationResult, Relevance]]:
    """관련도를 매기고 (마감 → 관련도 → 판정 → 분야 우선순위 → 마감일) 순으로 정렬한다."""
    scorer = scorer or _default_scorer()

    def industry_flagged(evaluation: EvaluationResult) -> bool:
        return any(
            r.rule_id == "industry.v1" and r.status == RuleStatus.REVIEW
            for r in evaluation.rule_results
        )

    scored = [
        (
            evaluation,
            scorer.score(evaluation.announcement, industry_flagged(evaluation), company),
        )
        for evaluation in evaluations
    ]

    def key(pair):
        evaluation, relevance = pair
        end_date = evaluation.announcement.application_end_at.value
        return (
            1 if evaluation.closed else 0,
            TIER_ORDER[relevance.tier],
            DECISION_ORDER.get(evaluation.decision, 9),
            scorer.category_rank(evaluation.announcement),
            end_date.toordinal() if end_date is not None else 10**9,
        )

    return sorted(scored, key=key)


def tier_counts(scored) -> dict[str, int]:
    counts = {tier: 0 for tier in TIER_ORDER}
    for _evaluation, relevance in scored:
        counts[relevance.tier] += 1
    return counts


def relevance_summary(scored) -> str:
    counts = tier_counts(scored)
    return "[관련도] " + ", ".join(
        f"{TIER_LABELS[tier]} {counts[tier]}" for tier in TIER_ORDER
    )


def one_line(evaluation: EvaluationResult, relevance: Relevance) -> str:
    """부록용 한 줄: 판정 · 분야 · 마감 · 제목 · 사유 · 링크.

    지원 불가는 관련도 사유 대신 제외 근거(불일치 규칙의 이유)를 쓴다 —
    제외 결과에도 이유를 표시한다는 원칙 유지.
    """
    ann = evaluation.announcement
    label = DECISION_LABELS[evaluation.decision]
    end = _format_date(ann.application_end_at)
    category = f" [{ann.support_category}]" if ann.support_category else ""
    link = f" {ann.detail_url}" if ann.detail_url else ""
    if evaluation.decision == Decision.INELIGIBLE:
        fails = [r.reason for r in evaluation.rule_results if r.status == RuleStatus.FAIL]
        reason = fails[0] if fails else (relevance.reasons[0] if relevance.reasons else "")
    else:
        reason = relevance.reasons[0] if relevance.reasons else ""
    return f"- ({label}){category} ~{end} {ann.title or '(제목 없음)'} — {reason}{link}"


def _format_date(date_field) -> str:
    if date_field.value is not None:
        return date_field.value.isoformat()
    if date_field.raw is not None:
        return f"{date_field.raw} (해석 불가)"
    return "미상"


def summary_line(evaluations: list[EvaluationResult], company: Company) -> str:
    counts = {decision: 0 for decision in Decision}
    closed = 0
    for evaluation in evaluations:
        counts[evaluation.decision] += 1
        if evaluation.closed:
            closed += 1
    kind = "가상회사" if company.is_fictional else "실제 회사"
    return (
        f"[판정 요약] 회사: {company.name} ({company.company_id}, {kind}) / "
        f"공고 {len(evaluations)}건 — "
        f"우선 검토 {counts[Decision.ELIGIBLE]}, "
        f"판단 필요 {counts[Decision.REVIEW_REQUIRED]}, "
        f"지원 불가 {counts[Decision.INELIGIBLE]}, 마감 {closed}"
    )


def render_announcement_block(evaluation: EvaluationResult) -> list[str]:
    """공고 하나의 판정 블록 (지시서 19절 형식)."""
    ann = evaluation.announcement
    label = DECISION_LABELS[evaluation.decision]
    if evaluation.closed:
        label += " · 마감"

    lines = [
        f"[판정] {label}",
        f"[공고명] {ann.title or '(제목 없음)'} (공고번호 {ann.source_id or '미상'})",
    ]
    if ann.organization_name:
        lines.append(f"[주관기관] {ann.organization_name}")
    if ann.support_category:
        lines.append(f"[지원분야] {ann.support_category}")
    lines.append(
        f"[접수기간] {_format_date(ann.application_start_at)}"
        f" ~ {_format_date(ann.application_end_at)}"
    )
    if ann.detail_url:
        lines.append(f"[상세페이지] {ann.detail_url}")

    lines.append("")
    lines.append("확인된 조건")
    for result in evaluation.rule_results:
        name = RULE_LABELS.get(result.rule_id, result.rule_id)
        status = STATUS_LABELS.get(result.status, result.status.value)
        lines.append(f"- {name}: {status}")
        if result.announcement_value is not None:
            lines.append(f"  - 공고 조건: {result.announcement_value}")
        if result.company_value is not None:
            lines.append(f"  - 회사 정보: {result.company_value}")
        lines.append(f"  - 판단 사유: {result.reason}")

    if ann.excluded_target_description:
        snippet = " ".join(ann.excluded_target_description.split())
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        lines.append("")
        lines.append(f"신청 제외 대상 (원문 발췌): {snippet}")

    checks = list(evaluation.human_checks)
    if evaluation.decision != Decision.INELIGIBLE:
        for check in STANDARD_CHECKS:
            if check not in checks:
                checks.append(check)
    if checks:
        lines.append("")
        lines.append("추가 검토 사항")
        lines.extend(f"- {check}" for check in checks)
    return lines


def _render_sections(scored, heading) -> list[str]:
    """본문(A·B 전체 블록) → 부록(C 한 줄) → 마감(전체 블록) 순 렌더링.

    heading(title) -> list[str]: 섹션 제목 줄을 만드는 함수 (콘솔/Markdown 차이).
    """
    def reviewable(pair) -> bool:
        return pair[0].decision != Decision.INELIGIBLE

    hidden = [p for p in scored if p[1].hidden]
    shown = [p for p in scored if not p[1].hidden]
    open_ab = [p for p in shown if not p[0].closed and p[1].tier != "C" and reviewable(p)]
    open_c = [p for p in shown if not p[0].closed and p[1].tier == "C" and reviewable(p)]
    open_ineligible = [p for p in shown if not p[0].closed and not reviewable(p)]
    closed = [p for p in shown if p[0].closed]
    parts: list[str] = []
    if hidden:
        parts.append("")
        parts.append(
            f"(타 자치구·시군 한정 공고 {len(hidden)}건은 표시하지 않음 — JSON 출력에는 포함)"
        )
    for tier in ("A", "B"):
        group = [p for p in open_ab if p[1].tier == tier]
        if not group:
            continue
        parts.extend(["", *heading(f"{TIER_LABELS[tier]} ({tier}) — {len(group)}건")])
        for evaluation, relevance in group:
            parts.append("")
            parts.extend(render_announcement_block(evaluation))
            parts.append(f"[관련도] {relevance.label}: {'; '.join(relevance.reasons)}")
    if open_c:
        parts.extend([
            "",
            *heading(f"관련 낮음 (C) — {len(open_c)}건, 제목만 (놓침 방지용 부록)"),
            "",
        ])
        parts.extend(one_line(evaluation, relevance) for evaluation, relevance in open_c)
    if open_ineligible:
        parts.extend([
            "",
            *heading(f"지원 불가 — {len(open_ineligible)}건, 제목·제외 사유만"),
            "",
        ])
        parts.extend(
            one_line(evaluation, relevance) for evaluation, relevance in open_ineligible
        )
    if closed:
        parts.extend(["", *heading(f"마감 — {len(closed)}건")])
        for evaluation, _relevance in closed:
            parts.append("")
            parts.extend(render_announcement_block(evaluation))
    return parts


def render_console_report(
    evaluations: list[EvaluationResult], company: Company, filters=None, scorer=None
) -> str:
    scored = score_all(evaluations, scorer, company)
    parts = [summary_line([e for e, _r in scored], company), relevance_summary(scored)]
    if filters:
        parts.append(f"[필터] {' / '.join(filters)}")
    parts.extend(["", DISCLAIMER])
    parts.extend(_render_sections(scored, lambda title: [f"== {title} =="]))
    return "\n".join(parts)


def evaluation_to_dict(evaluation: EvaluationResult) -> dict:
    """판정 하나를 기계가 읽을 수 있는 dict로 직렬화한다 (실행 간 diff 비교용)."""
    ann = evaluation.announcement
    return {
        "source": ann.source,
        "source_id": ann.source_id,
        "title": ann.title,
        "support_category": ann.support_category,
        "decision": evaluation.decision.value,
        "closed": evaluation.closed,
        "application_start": ann.application_start_at.value.isoformat()
        if ann.application_start_at.value
        else ann.application_start_at.raw,
        "application_end": ann.application_end_at.value.isoformat()
        if ann.application_end_at.value
        else ann.application_end_at.raw,
        "detail_url": ann.detail_url,
        "relevance": None,  # render_json_report에서 채운다
        "rules": [
            {
                "rule_id": result.rule_id,
                "status": result.status.value,
                "announcement_value": result.announcement_value,
                "company_value": result.company_value,
                "reason": result.reason,
                "evidence_field": result.evidence_field,
                "confidence": result.confidence.value,
                "human_checks": list(result.human_checks),
            }
            for result in evaluation.rule_results
        ],
        "human_checks": evaluation.human_checks,
    }


def render_json_report(
    evaluations: list[EvaluationResult],
    company: Company,
    generated_at: datetime,
    filters=None,
    scorer=None,
) -> str:
    """정렬된 전체 판정을 JSON으로 직렬화한다 (관련도 포함)."""
    scored = score_all(evaluations, scorer, company)
    ordered = [evaluation for evaluation, _relevance in scored]
    counts = {decision.value: 0 for decision in Decision}
    for evaluation in ordered:
        counts[evaluation.decision.value] += 1
    results = []
    for evaluation, relevance in scored:
        entry = evaluation_to_dict(evaluation)
        entry["relevance"] = {
            "tier": relevance.tier,
            "label": relevance.label,
            "score": relevance.score,
            "reasons": list(relevance.reasons),
            "hidden": relevance.hidden,
        }
        results.append(entry)
    payload = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "company_id": company.company_id,
        "company_is_fictional": company.is_fictional,
        "filters": list(filters or []),
        "disclaimer": DISCLAIMER,
        "summary": {
            **counts,
            "closed": sum(1 for e in ordered if e.closed),
            "total": len(ordered),
            "relevance": tier_counts(scored),
        },
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_markdown_report(
    evaluations: list[EvaluationResult],
    company: Company,
    generated_at: datetime,
    filters=None,
    scorer=None,
) -> str:
    scored = score_all(evaluations, scorer, company)
    ordered = [evaluation for evaluation, _relevance in scored]
    parts = [
        "# Grant Radar KR 판정 보고서",
        "",
        f"- 생성 시각: {generated_at.isoformat(timespec='minutes')}",
        f"- {summary_line(ordered, company)[len('[판정 요약] ') :]}",
        f"- 관련도: {relevance_summary(scored)[len('[관련도] ') :]}",
    ]
    if filters:
        parts.append(f"- 필터: {' / '.join(filters)}")
    parts.extend(["", f"> {DISCLAIMER}"])
    parts.extend(_render_a_summary(scored))
    parts.extend(_render_sections(scored, lambda title: [f"## {title}"]))
    parts.append("")
    return "\n".join(parts)


def _render_a_summary(scored) -> list[str]:
    """맨 위 요약표: 관련 높음(A)·모집 중·검토 대상만 분야별 한 줄씩."""
    rows = [
        (e, r) for e, r in scored
        if r.tier == "A" and not e.closed and e.decision != Decision.INELIGIBLE
    ]
    if not rows:
        return []
    by_category: dict[str, list] = {}
    for e, r in rows:
        by_category.setdefault(e.announcement.support_category or "(분야 없음)", []).append((e, r))
    parts = ["", f"## 한눈에 보기 — 관련 높음(A) {len(rows)}건", ""]
    parts.append("| 분야 | 판정 | 마감 | 공고 |")
    parts.append("|---|---|---|---|")
    for category, items in by_category.items():
        for e, _r in items:
            ann = e.announcement
            title = (ann.title or "(제목 없음)").replace("|", "ㅣ")
            link = f"[{title}]({ann.detail_url})" if ann.detail_url else title
            parts.append(
                f"| {category} | {DECISION_LABELS[e.decision].split('(')[0]} "
                f"| {_format_date(ann.application_end_at)} | {link} |"
            )
    return parts
