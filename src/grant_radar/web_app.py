"""Streamlit entry point; run only on 127.0.0.1."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from grant_radar import local_setup as setup
from grant_radar.registration import DocumentError, ocr_status, read_registration
from grant_radar.services.ingestion import KST

ROOT = Path(__file__).resolve().parents[2]
REGIONS = [
    "확인 안 됨",
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
]
BOOL_LABELS = ["확인 안 됨", "해당", "해당하지 않음"]
BOOL_VALUES = [None, True, False]
BUSINESS_LABELS = ["확인 안 됨", "법인사업자", "개인사업자"]
BUSINESS_VALUES = [None, "corporation", "individual"]


def tokens(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def number(value: str) -> int | None:
    if not value.strip():
        return None
    try:
        result = int(value)
    except ValueError:
        raise setup.SetupError("나이와 직원 수는 숫자로 입력하거나 비워주세요.") from None
    return result


def notice_error(exc) -> None:
    # Known validation messages only; never display raw document/SMTP/parser exceptions.
    if isinstance(exc, (setup.SetupError, DocumentError)):
        st.error(str(exc))
    else:
        st.error("처리하지 못했습니다. 입력과 설치 상태를 확인한 뒤 다시 시도해주세요.")


def main() -> None:
    st.set_page_config(
        page_title="Grant Radar · 우리 회사 지원사업", page_icon="🌱", layout="centered"
    )
    st.markdown(
        """<style>
    .stApp {background: #faf9f5;}
    .block-container {max-width: 920px; padding-top: 2.6rem;}
    h1 {letter-spacing: -0.06em;}
    [data-testid="stMetric"] {background: #eef4eb; border-radius: 16px; padding: 14px;}
    </style>""",
        unsafe_allow_html=True,
    )
    st.caption("GRANT RADAR KR  ·  내 PC에서 사용하는 지원사업 알림")
    st.title("우리 회사의 다음 기회 🌱")
    st.write("회사 정보를 한 번 정리하고, 확인할 지원사업을 메일로 받아보세요.")
    st.caption("공식 자격 판정이 아닙니다. 최종 지원 조건은 공고 원문에서 확인해주세요.")
    try:
        company = setup.read_company(ROOT)
        values = setup.connection(ROOT)
    except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
        notice_error(exc)
        st.stop()

    page = st.radio(
        "메뉴",
        ["회사 정보", "API·메일", "실행·예약"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if page == "회사 정보":
        company_page(company)
    elif page == "API·메일":
        connection_page(values)
    else:
        run_page(company, values)


def reset_document() -> None:
    st.session_state.pop("registration_draft", None)
    st.session_state["company_confirmed"] = False


def company_page(existing: dict) -> None:
    st.subheader("1. 우리 회사 알려주기")
    st.write("사업자등록증을 읽거나 아래에 직접 입력하세요. 모르는 항목은 비워두면 됩니다.")
    defaults = {
        "co_name": existing.get("name", ""),
        "co_date": existing.get("established_date") or "",
        "co_industry": ", ".join(existing.get("industry_names", [])),
        "co_categories": ", ".join(existing.get("business_categories", [])),
        "co_keywords": ", ".join(existing.get("matching_keywords", [])),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    with st.expander("사업자등록증으로 채우기 · PDF / 사진", expanded=not bool(existing)):
        uploaded = st.file_uploader(
            "사업자등록증",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            key="registration_upload",
            on_change=reset_document,
        )
        st.caption(
            "최대 10MB · PDF 3쪽 이하. 파일은 내 PC에서 읽고 원본을 장기 보관하지 않습니다. OCR용 임시 파일은 처리 후 정리됩니다."
        )
        if st.button("문서 읽기", disabled=uploaded is None):
            try:
                with st.spinner("문서를 읽고 있습니다…"):
                    draft = read_registration(uploaded.getvalue(), uploaded.name)
                st.session_state["registration_draft"] = draft
                st.session_state["company_confirmed"] = False
                for field, key in [
                    ("name", "co_name"),
                    ("established_date", "co_date"),
                    ("industry_names", "co_industry"),
                    ("business_categories", "co_categories"),
                ]:
                    st.session_state[key] = draft.fields.get(field, "")
                st.success(f"{draft.method}로 읽었습니다. 아래 입력란을 원본과 대조해주세요.")
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)
        draft = st.session_state.get("registration_draft")
        if draft:
            if draft.fields.get("address"):
                st.info(
                    "문서에서 읽은 소재지: "
                    + draft.fields["address"]
                    + " — 아래 지역을 직접 확인해주세요."
                )
            if draft.fields.get("opening_date"):
                st.info(
                    "문서의 개업연월일: "
                    + draft.fields["opening_date"]
                    + ". 법인의 설립일과 같다고 자동 판단하지 않습니다. 아래 설립일을 확인해주세요."
                )
            with st.expander("읽은 원문 확인"):
                st.text(draft.text)
        with st.expander("사진을 읽지 못한다면"):
            ok, message = ocr_status()
            st.write(("✓ " if ok else "") + message)
            st.markdown(
                "[Tesseract 설치 안내](https://tesseract-ocr.github.io/tessdoc/Installation.html)"
            )
            st.caption(
                "Windows 설치 시 한국어 언어 데이터를 포함하세요. 텍스트 PDF는 OCR 설치 없이도 읽습니다."
            )

    with st.form("company"):
        name = st.text_input("회사 이름 *", key="co_name")
        left, right = st.columns(2)
        with left:
            kind = st.selectbox(
                "사업자 형태",
                BUSINESS_LABELS,
                index=BUSINESS_VALUES.index(existing.get("business_type"))
                if existing.get("business_type") in BUSINESS_VALUES
                else 0,
            )
            established = st.text_input(
                "설립일 / 개인사업자 개업일",
                key="co_date",
                placeholder="2022-03-15",
                help="법인은 법인 설립일을 확인해주세요.",
            )
        with right:
            old_region = existing.get("headquarters_region")
            aliases = json.loads(
                (ROOT / "data/reference/region_mapping.json").read_text(encoding="utf-8")
            )["aliases"]
            old_region = aliases.get(old_region, old_region)
            region = st.selectbox(
                "본점 지역",
                REGIONS,
                index=REGIONS.index(old_region) if old_region in REGIONS else 0,
            )
            districts = st.text_input(
                "사업장 시·군·구",
                value=", ".join(existing.get("business_districts", [])),
                placeholder="예: 마포구",
            )
        locations = st.text_input(
            "추가 사업장 시·도 (쉼표 구분)",
            value=", ".join(existing.get("business_locations", [])),
            placeholder="예: 서울, 경기",
        )
        categories = st.text_input("업태", key="co_categories")
        industries = st.text_input("종목 / 업종", key="co_industry")
        keywords = st.text_input(
            "제품·서비스 키워드 (쉼표 구분)",
            key="co_keywords",
            placeholder="예: 소프트웨어, SaaS, 클라우드",
        )
        left, right = st.columns(2)
        with left:
            age = st.text_input(
                "대표자 만 나이 (선택)",
                value=str(existing.get("representative_age") or ""),
                help="입력한 오늘 날짜를 나이 기준일로 기록합니다. 대표자 이름·주민번호는 필요하지 않습니다.",
            )
            employees = st.text_input(
                "직원 수 (선택)", value=str(existing.get("employee_count") or "")
            )
        with right:
            sme = st.selectbox(
                "중소기업 해당 여부", BOOL_LABELS, index=BOOL_VALUES.index(existing.get("sme"))
            )
            small = st.selectbox(
                "소상공인 해당 여부",
                BOOL_LABELS,
                index=BOOL_VALUES.index(existing.get("small_business")),
            )
        st.caption("중소기업·소상공인 여부는 직원 수나 사업자 형태에서 자동 추정하지 않습니다.")
        fictional = st.checkbox("가상 회사로 사용", value=existing.get("is_fictional", False))
        confirmed = st.checkbox(
            "문서에서 읽은 값과 입력 내용을 확인했습니다.", key="company_confirmed"
        )
        submitted = st.form_submit_button("회사 정보 저장", type="primary")
        if submitted:
            try:
                data = dict(existing)
                data.update(
                    company_id=existing.get("company_id") or "company-" + uuid4().hex[:12],
                    name=name.strip(),
                    is_fictional=fictional,
                    business_type=BUSINESS_VALUES[BUSINESS_LABELS.index(kind)],
                    established_date=established.strip() or None,
                    headquarters_region=None if region == "확인 안 됨" else region,
                    business_locations=tokens(locations),
                    business_districts=tokens(districts),
                    business_categories=tokens(categories),
                    industry_names=tokens(industries),
                    matching_keywords=tokens(keywords),
                    representative_age=number(age),
                    employee_count=number(employees),
                    sme=BOOL_VALUES[BOOL_LABELS.index(sme)],
                    small_business=BOOL_VALUES[BOOL_LABELS.index(small)],
                    data_as_of=datetime.now(KST).date().isoformat(),
                )
                # An explicit age replaces a previously supplied birth date.
                if age.strip():
                    data["representative_birth_date"] = None
                setup.save_company(ROOT, data, confirmed=confirmed)
                st.session_state.pop("report", None)
                st.success("회사 정보를 저장했습니다. 다음으로 ‘API·메일’을 열어주세요.")
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)


def connection_page(saved: dict) -> None:
    st.subheader("2. API와 메일 연결하기")
    st.markdown(
        "공공데이터포털에서 **두 서비스 모두 활용 신청**한 일반 인증키(Decoding)를 준비해주세요."
    )
    st.markdown(
        "[K-Startup API 신청](https://www.data.go.kr/data/15125364/openapi.do) · "
        "[기업마당 API 신청](https://www.data.go.kr/data/15157820/openapi.do)"
    )
    with st.form("connection", clear_on_submit=True):
        api = st.text_input(
            "공공데이터포털 API 키",
            type="password",
            help="저장된 키가 있으면 비워둘 때 유지합니다.",
        )
        st.caption("API 키 저장됨" if saved["KSTARTUP_API_KEY"] else "API 키 미설정")
        st.markdown("**메일 발송 설정**")
        st.caption(
            "받는 주소만으로는 메일을 보낼 수 없습니다. 보내는 계정의 발송용 인증정보도 필요합니다."
        )
        user = st.text_input("보내는 이메일", value=saved["SMTP_USER"])
        password = st.text_input(
            "메일 앱 비밀번호 / SMTP 비밀번호",
            type="password",
            help="일반 로그인 비밀번호가 아닙니다. 비워두면 저장된 값을 유지합니다.",
        )
        recipient = st.text_input("보고서 받을 이메일", value=saved["REPORT_MAIL_TO"])
        with st.expander("메일 서버 (기본: Gmail STARTTLS)"):
            host = st.text_input("SMTP 서버", value=saved["SMTP_HOST"])
            port = st.text_input("SMTP 포트", value=saved["SMTP_PORT"])
            st.caption("STARTTLS 방식만 지원합니다. SSL 전용 465 포트는 지원하지 않습니다.")
        st.markdown(
            "[Gmail 앱 비밀번호 안내](https://support.google.com/accounts/answer/185833?hl=ko)"
        )
        st.caption(
            "설정은 이 PC의 .env에 저장됩니다. 키와 비밀번호는 다시 화면에 표시하지 않습니다."
        )
        if st.form_submit_button("연결 정보 저장", type="primary"):
            try:
                setup.save_connection(
                    ROOT,
                    {
                        "KSTARTUP_API_KEY": api,
                        "SMTP_USER": user,
                        "SMTP_PASSWORD": password,
                        "REPORT_MAIL_TO": recipient,
                        "SMTP_HOST": host,
                        "SMTP_PORT": port,
                    },
                )
                st.success("저장했습니다. 아래에서 연결을 확인해주세요.")
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)
    left, right = st.columns(2)
    with left:
        if st.button("API 연결 확인"):
            try:
                with st.spinner("각 API에서 공고 1건씩 조회합니다…"):
                    statuses = setup.probe_api(ROOT)
                for source, status in statuses.items():
                    if status == "연결 확인":
                        st.success(f"{source}: {status}")
                    else:
                        st.error(f"{source}: {status}")
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)
    with right:
        current = setup.connection(ROOT)
        st.caption("시험 메일 수신: " + (current["REPORT_MAIL_TO"] or "미설정"))
        if st.button("시험 메일 1통 보내기"):
            try:
                with st.spinner("시험 메일을 보내고 있습니다…"):
                    setup.send_test_email(ROOT)
                st.success(
                    "SMTP 서버가 발송 요청을 수락했습니다. 받은편지함·스팸함에서 수신을 확인해주세요."
                )
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)


def run_page(company: dict, saved: dict) -> None:
    st.subheader("3. 실행하고 받아보기")
    st.caption("회사: " + (company.get("name") or "아직 설정하지 않았습니다."))
    st.write("먼저 보고서를 확인한 뒤 메일을 보내거나, 매주 자동 실행을 켤 수 있습니다.")
    left, right = st.columns(2)
    with left:
        if st.button("가상 회사로 체험하기"):
            try:
                with st.spinner("토끼랩과 가상 공고로 실제 판정 코드를 실행합니다…"):
                    st.session_state["report"] = setup.demo_report(ROOT)
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)
    with right:
        if st.button(
            "지금 공고 찾아보기",
            type="primary",
            disabled=not company or not saved["KSTARTUP_API_KEY"],
        ):
            try:
                # Invalidate old results before a new attempt; a failed run must not offer stale mail.
                st.session_state.pop("report", None)
                with st.spinner(
                    "K-Startup·기업마당 공고를 수집하고 판정합니다. 몇 분 걸릴 수 있습니다…"
                ):
                    st.session_state["report"] = setup.collect_report(ROOT)
            except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                notice_error(exc)
    st.caption(
        "수집 범위: K-Startup 최대 300건 · 기업마당 최대 1,700건 요청. 전체 공고 수집을 보장하지 않습니다."
    )
    bundle = st.session_state.get("report")
    if bundle:
        try:
            payload = json.loads((bundle.directory / "report.json").read_text(encoding="utf-8"))
            markdown = (bundle.directory / "report.md").read_text(encoding="utf-8")
            if bundle.fictional:
                st.info("토끼랩 체험: 회사와 공고 모두 가상입니다. 실제 API를 호출하지 않았습니다.")
            for warning in bundle.warnings:
                st.warning(warning)
            stats = payload["summary"]
            a, b, c = st.columns(3)
            a.metric("우선 검토", stats["ELIGIBLE"])
            b.metric("판단 필요", stats["REVIEW_REQUIRED"])
            c.metric("지원 불가", stats["INELIGIBLE"])
            st.caption(f"총 {stats['total']}건 · 마감 표시는 별도이며 위 판정에 포함됩니다.")
            with st.expander("판정 근거와 보고서 보기", expanded=True):
                st.markdown(markdown)
            st.download_button(
                "보고서 내려받기", markdown, "grant-radar-report.md", "text/markdown"
            )
            if not bundle.fictional:
                st.caption("발송 대상: " + (saved["REPORT_MAIL_TO"] or "미설정"))
                if st.button("이 보고서를 메일로 보내기"):
                    with st.spinner("메일을 발송합니다…"):
                        setup.send_report_email(ROOT, bundle)
                    st.success("발송 요청을 수락했습니다. 받은편지함에서 확인해주세요.")
        except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
            notice_error(exc)
    st.divider()
    st.markdown("**매주 월요일 오전 9시 · 자동 메일**")
    st.caption(
        "Windows에서 지원합니다. PC가 켜져 있고 로그인·인터넷 연결이 필요합니다. "
        "예약 시각을 놓치면 사용 가능해진 뒤 실행합니다. 컴퓨터가 꺼져 있는 동안에는 발송하지 않습니다."
    )
    st.caption(
        "현재 폴더의 회사 프로필들을 주간 실행에서 함께 평가합니다. 새 설정은 예약 실행에도 반영됩니다."
    )
    confirmed = st.checkbox("저장한 수신 주소로 매주 자동 발송하도록 설정합니다.")
    a, b, c = st.columns(3)
    for column, label, mode in [
        (a, "자동 메일 켜기", "enable"),
        (b, "예약 상태 확인", "status"),
        (c, "자동 메일 끄기", "disable"),
    ]:
        with column:
            if st.button(label, disabled=mode == "enable" and not confirmed):
                try:
                    st.info(setup.scheduled_task(ROOT, mode))
                except Exception as exc:  # noqa: BLE001 - UI boundary; do not expose raw errors
                    notice_error(exc)


if __name__ == "__main__":
    main()
