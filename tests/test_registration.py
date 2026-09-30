import io

import pytest

from grant_radar import registration as document

TEXT = """사업자등록증
법인명(단체명) : 토끼랩 주식회사
대표자 : 가상대표
개업연월일 : 2022년 03월 15일
사업장 소재지 : 서울특별시 마포구 가상로 1
업태 : 정보통신업  종목 : 소프트웨어 개발
"""


def test_explicit_fields_only_no_inferred_private_data():
    fields = document.suggest_fields(TEXT)
    assert fields["name"] == "토끼랩 주식회사"
    assert fields["opening_date"] == "2022-03-15"
    assert fields["business_categories"] == "정보통신업"
    assert fields["industry_names"] == "소프트웨어 개발"
    assert "established_date" not in fields
    assert "representative_age" not in fields
    assert "representative_birth_date" not in fields
    assert "sme" not in fields


def test_spaced_ocr_labels_and_invalid_date():
    fields = document.suggest_fields("상 호 : 토끼랩\n개 업 연 월 일 : 2026년 02월 31일")
    assert fields["name"] == "토끼랩"
    assert "opening_date" not in fields


@pytest.mark.parametrize(
    "data,name",
    [(b"", "a.png"), (b"x", "a.exe"), (b"x" * (document.MAX_BYTES + 1), "a.pdf")],
    ids=["empty", "extension", "oversize"],
)
def test_rejects_invalid_uploads(data, name):
    with pytest.raises(document.DocumentError):
        document.read_registration(data, name)


def test_image_uses_local_ocr_and_returns_draft(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (400, 300), "white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    monkeypatch.setattr(document, "_ocr", lambda image: TEXT)
    draft = document.read_registration(buf.getvalue(), "registration.png")
    assert draft.fields["name"] == "토끼랩 주식회사"
    assert draft.method == "로컬 OCR"


def test_broken_pdf_does_not_echo_contents():
    pytest.importorskip("pypdf")
    with pytest.raises(document.DocumentError) as caught:
        document.read_registration(b"private-document-sample", "a.pdf")
    assert "private-document-sample" not in str(caught.value)


def test_pdf_page_limit_precedes_ocr(monkeypatch):
    pdf = pytest.importorskip("pypdf")
    writer = pdf.PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(document.DocumentError, match="1~3"):
        document.read_registration(buf.getvalue(), "a.pdf")


def test_ocr_does_not_pair_a_label_with_an_unrelated_later_line():
    assert "address" not in document.suggest_fields("사업장 소재지\n\n: 소프트웨어 개발")
