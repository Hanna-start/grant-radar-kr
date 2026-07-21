"""대표자 연령 규칙 테스트 (지시서 14.4절).

표본 검증(2026-07-21, 100건)에서 발견된 오판 사례의 회귀 테스트를 포함한다:
청년 전용(만 20세 이상 ~ 만 39세 이하) 공고가 연령 규칙 없이 ELIGIBLE로
통과되던 문제.
"""

from datetime import date

from grant_radar.models.decision import RuleStatus
from grant_radar.rules.age import AgeRule, age_on, parse_age_band

from tests.factories import make_announcement, make_company

RULE = AgeRule()

FULL_BANDS = "만 20세 미만,만 20세 이상 ~ 만 39세 이하,만 40세 이상"
ADULT_BANDS = "만 20세 이상 ~ 만 39세 이하,만 40세 이상"
YOUTH_ONLY = "만 20세 이상 ~ 만 39세 이하"


def evaluate(age_conditions, birth=None, **announcement_overrides):
    announcement = make_announcement(biz_trgt_age=age_conditions, **announcement_overrides)
    company = make_company(representative_birth_date=birth)
    return RULE.evaluate(announcement, company)


class TestParsing:
    def test_parse_observed_tokens(self):
        assert parse_age_band("만 20세 미만") == (0, 19)
        assert parse_age_band("만 20세 이상 ~ 만 39세 이하") == (20, 39)
        assert parse_age_band("만 40세 이상")[0] == 40
        assert parse_age_band("모름") is None

    def test_age_on_exact_dates(self):
        assert age_on(date(1990, 7, 15), date(2026, 7, 14)) == 35
        assert age_on(date(1990, 7, 15), date(2026, 7, 15)) == 36


class TestNoRealRestriction:
    def test_all_three_bands_pass(self):
        result = evaluate(FULL_BANDS)
        assert result.status == RuleStatus.PASS

    def test_adult_bands_pass_without_birth_date(self):
        # 미성년만 제외된 경우 법인 대표 기준 실질 제한 없음
        result = evaluate(ADULT_BANDS)
        assert result.status == RuleStatus.PASS

    def test_no_conditions_is_not_applicable(self):
        result = evaluate(None)
        assert result.status == RuleStatus.NOT_APPLICABLE


class TestRealRestriction:
    def test_youth_only_without_birth_date_is_review(self):
        # 표본 검증에서 발견된 오판 회귀 테스트: 청년 전용 공고가
        # 생년월일 정보 없이 통과되면 안 된다
        result = evaluate(YOUTH_ONLY)
        assert result.status == RuleStatus.REVIEW
        assert "생년월일" in result.reason

    def test_no_over_forty_band_without_birth_date_is_review(self):
        result = evaluate("만 20세 미만,만 20세 이상 ~ 만 39세 이하")
        assert result.status == RuleStatus.REVIEW

    def test_birth_date_within_band_passes(self):
        # 접수기간 2026-07-01~31, 1990-01-01생 → 만 36세
        result = evaluate(YOUTH_ONLY, birth=date(1990, 1, 1))
        assert result.status == RuleStatus.PASS
        assert "36세" in result.reason

    def test_birth_date_outside_band_is_review_not_fail(self):
        # 기준일이 데이터에 없으므로 자동 제외하지 않는다 (지시서 14.4)
        result = evaluate(YOUTH_ONLY, birth=date(1980, 1, 1))
        assert result.status == RuleStatus.REVIEW
        assert result.status != RuleStatus.FAIL

    def test_boundary_crossing_during_window_is_review(self):
        # 접수기간(7/1~7/31) 중 만 40세가 되어 구간을 벗어나는 경우
        result = evaluate(YOUTH_ONLY, birth=date(1986, 7, 15))
        assert result.status == RuleStatus.REVIEW

    def test_unknown_token_is_review(self):
        result = evaluate("40세 이하", birth=date(1990, 1, 1))
        assert result.status == RuleStatus.REVIEW

    def test_unparseable_dates_is_review(self):
        result = evaluate(
            YOUTH_ONLY,
            birth=date(1990, 1, 1),
            pbanc_rcpt_bgng_dt="미정",
            pbanc_rcpt_end_dt="미정",
        )
        assert result.status == RuleStatus.REVIEW
