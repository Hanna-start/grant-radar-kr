"""보고서 렌더러 테스트 (지시서 19절 형식)."""

from datetime import datetime, timezone
from pathlib import Path

from grant_radar.reporting.console import (
    DISCLAIMER,
    render_announcement_block,
    render_console_report,
    render_markdown_report,
)
from grant_radar.rules.age import AgeRule
from grant_radar.rules.applicant_type import ApplicantTypeRule
from grant_radar.rules.business_age import BusinessAgeRule
from grant_radar.rules.region import RegionRule, load_region_mapping
from grant_radar.services.evaluation import evaluate_announcement

from tests.factories import make_announcement, make_company

MAPPING_PATH = Path(__file__).parent.parent / "data" / "reference" / "region_mapping.json"
RULES = [
    RegionRule(load_region_mapping(MAPPING_PATH)),
    BusinessAgeRule(),
    ApplicantTypeRule(),
    AgeRule(),
]
AS_OF = datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc)


def eligible_evaluation(**overrides):
    defaults = dict(supt_regin="전국", biz_enyy="10년미만", aply_trgt="일반기업")
    defaults.update(overrides)
    return evaluate_announcement(make_announcement(**defaults), make_company(), RULES, AS_OF)


def ineligible_evaluation(**overrides):
    defaults = dict(supt_regin="부산", biz_enyy="10년미만", aply_trgt="일반기업")
    defaults.update(overrides)
    return evaluate_announcement(make_announcement(**defaults), make_company(), RULES, AS_OF)


class TestAnnouncementBlock:
    def test_block_follows_spec_format(self):
        text = "\n".join(render_announcement_block(eligible_evaluation()))
        assert "[판정] 지원 가능" in text
        assert "[공고명]" in text
        assert "[주관기관] 가상진흥원" in text
        assert "[지원분야] 사업화" in text
        assert "[접수기간] 2026-07-01 ~ 2026-07-31" in text
        assert "[상세페이지] https://example.test/view?pbancSn=900001" in text
        assert "확인된 조건" in text
        assert "- 지역: 통과" in text
        assert "- 업력: 통과" in text
        assert "- 신청자 유형: 통과" in text
        assert "- 대표자 연령: 통과" in text
        assert "판단 사유:" in text
        assert "추가 검토 사항" in text
        assert "중복수혜 제한" in text

    def test_ineligible_block_shows_reason(self):
        text = "\n".join(render_announcement_block(ineligible_evaluation()))
        assert "[판정] 지원 불가" in text
        assert "- 지역: 불일치" in text
        assert "포함되지 않습니다" in text  # 제외 결과에도 이유 표시
        # 지원 불가에는 상세 검토 항목(자부담 등)을 붙이지 않는다
        assert "자부담" not in text
        # 단, 규칙이 남긴 확인 사항(이전 예정 문구)은 표시한다
        assert "이전 예정" in text

    def test_excluded_target_excerpt_is_shown(self):
        evaluation = eligible_evaluation(aply_excl_trgt_ctnt="국세 체납 중인 자 " * 30)
        text = "\n".join(render_announcement_block(evaluation))
        assert "신청 제외 대상 (원문 발췌):" in text
        assert "…" in text  # 200자 초과 시 잘림

    def test_closed_marker(self):
        evaluation = eligible_evaluation(rcrt_prgs_yn="N")
        text = "\n".join(render_announcement_block(evaluation))
        assert "· 마감" in text

    def test_unparseable_date_is_visible(self):
        evaluation = eligible_evaluation(pbanc_rcpt_end_dt="미정", biz_enyy=None)
        text = "\n".join(render_announcement_block(evaluation))
        assert "미정 (해석 불가)" in text


class TestReportOrdering:
    def test_console_report_orders_by_decision_then_deadline(self):
        early = eligible_evaluation(
            pbanc_sn=1, biz_pbanc_nm="빠른 마감", pbanc_rcpt_end_dt="20260725"
        )
        late = eligible_evaluation(
            pbanc_sn=2, biz_pbanc_nm="늦은 마감", pbanc_rcpt_end_dt="20260731"
        )
        excluded = ineligible_evaluation(pbanc_sn=3, biz_pbanc_nm="제외 공고")
        closed = eligible_evaluation(pbanc_sn=4, biz_pbanc_nm="마감 공고", rcrt_prgs_yn="N")

        report = render_console_report([closed, excluded, late, early], make_company())
        assert "[판정 요약]" in report
        positions = {
            name: report.index(name)
            for name in ("빠른 마감", "늦은 마감", "제외 공고", "마감 공고")
        }
        assert (
            positions["빠른 마감"]
            < positions["늦은 마감"]
            < positions["제외 공고"]
            < positions["마감 공고"]
        )


class TestMarkdownReport:
    def test_markdown_report_has_header_and_disclaimer(self):
        generated_at = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)
        markdown = render_markdown_report([eligible_evaluation()], make_company(), generated_at)
        assert markdown.startswith("# Grant Radar KR 판정 보고서")
        assert DISCLAIMER in markdown
        assert "2026-07-21" in markdown
        assert "[판정] 지원 가능" in markdown
