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
    return (
        f"[판정 요약] 회사: {company.name} ({company.company_id}, 가상회사) / "
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


def render_console_report(evaluations: list[EvaluationResult], company: Company) -> str:
    ordered = sorted(evaluations, key=sort_key)
    parts = [summary_line(ordered, company), "", DISCLAIMER]
    for evaluation in ordered:
        parts.append("")
        parts.extend(render_announcement_block(evaluation))
    return "\n".join(parts)


def evaluation_to_dict(evaluation: EvaluationResult) -> dict:
    """판정 하나를 기계가 읽을 수 있는 dict로 직렬화한다 (실행 간 diff 비교용)."""
    ann = evaluation.announcement
    return {
        "source": ann.source,
        "source_id": ann.source_id,
        "title": ann.title,
        "decision": evaluation.decision.value,
        "closed": evaluation.closed,
        "application_start": ann.application_start_at.value.isoformat()
        if ann.application_start_at.value
        else ann.application_start_at.raw,
        "application_end": ann.application_end_at.value.isoformat()
        if ann.application_end_at.value
        else ann.application_end_at.raw,
        "detail_url": ann.detail_url,
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
    evaluations: list[EvaluationResult], company: Company, generated_at: datetime
) -> str:
    """정렬된 전체 판정을 JSON으로 직렬화한다."""
    ordered = sorted(evaluations, key=sort_key)
    counts = {decision.value: 0 for decision in Decision}
    for evaluation in ordered:
        counts[evaluation.decision.value] += 1
    payload = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "company_id": company.company_id,
        "company_is_fictional": company.is_fictional,
        "disclaimer": DISCLAIMER,
        "summary": {
            **counts,
            "closed": sum(1 for e in ordered if e.closed),
            "total": len(ordered),
        },
        "results": [evaluation_to_dict(evaluation) for evaluation in ordered],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_markdown_report(
    evaluations: list[EvaluationResult], company: Company, generated_at: datetime
) -> str:
    ordered = sorted(evaluations, key=sort_key)
    parts = [
        "# Grant Radar KR 판정 보고서",
        "",
        f"- 생성 시각: {generated_at.isoformat(timespec='minutes')}",
        f"- {summary_line(ordered, company)[len('[판정 요약] ') :]}",
        "",
        f"> {DISCLAIMER}",
    ]
    for evaluation in ordered:
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.extend(render_announcement_block(evaluation))
    parts.append("")
    return "\n".join(parts)
