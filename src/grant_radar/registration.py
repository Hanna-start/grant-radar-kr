"""Local document reading. Extracted fields are suggestions, never verified facts."""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 3
MAX_PIXELS = 24_000_000


class DocumentError(ValueError):
    pass


@dataclass(frozen=True)
class RegistrationDraft:
    text: str
    fields: dict[str, str]
    method: str


def ocr_status() -> tuple[bool, str]:
    try:
        import pytesseract

        executable = shutil.which("tesseract")
        fallback = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
        if not executable and fallback.is_file():
            executable = str(fallback)
        if not executable:
            return False, "Tesseract OCR을 설치하면 사진·스캔 PDF를 읽을 수 있습니다."
        pytesseract.pytesseract.tesseract_cmd = executable
        languages = pytesseract.get_languages(config="")
        if not {"kor", "eng"} <= set(languages):
            return False, "Tesseract의 한국어(kor)·영어(eng) 언어 데이터가 필요합니다."
        return True, "한국어 OCR 사용 가능"
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        return False, "OCR 설치 상태를 확인하지 못했습니다. 설치 안내를 확인하세요."


def _ocr(image) -> str:
    import pytesseract

    ok, message = ocr_status()
    if not ok:
        raise DocumentError(message)
    if image.width * image.height > MAX_PIXELS:
        raise DocumentError("이미지가 너무 큽니다. 2,400만 화소 이하로 줄여주세요.")
    try:
        return pytesseract.image_to_string(image, lang="kor+eng", config="--psm 6", timeout=40)
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise DocumentError(
            "사진의 글자를 읽지 못했습니다. 선명한 정면 사진으로 다시 시도하세요."
        ) from None


def _read_pdf(data: bytes) -> tuple[str, str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("암호를 해제한 PDF를 올려주세요.")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise DocumentError("사업자등록증은 1~3쪽 PDF로 올려주세요.")
        texts = [page.extract_text() or "" for page in reader.pages]
    except DocumentError:
        raise
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise DocumentError("PDF를 읽지 못했습니다. 다른 PDF나 사진으로 다시 시도하세요.") from None

    # A scan can contain a tiny watermark text layer; do not mistake it for the document.
    needs_ocr = [len(re.findall(r"[가-힣]", text)) < 20 for text in texts]
    if not any(needs_ocr):
        return "\n".join(texts), "PDF 텍스트"
    import pypdfium2 as pdfium

    try:
        with pdfium.PdfDocument(data) as document:
            for index, needed in enumerate(needs_ocr):
                if not needed:
                    continue
                page = document[index]
                try:
                    width, height = page.get_size()
                    if width * height * 9 > MAX_PIXELS:
                        raise DocumentError(
                            "PDF 페이지가 너무 큽니다. A4 크기로 다시 저장해주세요."
                        )
                    bitmap = page.render(scale=3)
                    try:
                        with bitmap.to_pil() as image:
                            texts[index] = _ocr(image)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    except DocumentError:
        raise
    except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
        raise DocumentError(
            "스캔 PDF를 읽지 못했습니다. 선명한 사진으로 다시 시도하세요."
        ) from None
    return "\n".join(texts), "로컬 OCR"


def _label(pattern: str) -> str:
    return r"[ \t]*".join(re.escape(char) for char in pattern)


def suggest_fields(text: str) -> dict[str, str]:
    """Only explicit labels. Opening date is NOT silently mapped to incorporation date."""
    fields: dict[str, str] = {}
    labels = {
        "name": ["법인명(단체명)", "법인명", "상호(법인명)", "상호"],
        "address": ["사업장소재지", "사업장 소재지", "본점소재지"],
        "industry_names": ["종목"],
        "business_categories": ["업태"],
    }
    stop = r"(?=\s*(?:성\s*명|대\s*표\s*자|업\s*태|종\s*목|개\s*업|법\s*인\s*등\s*록|사\s*업\s*장\s*소\s*재\s*지)\s*[:：]|$)"
    for field, alternatives in labels.items():
        for label in alternatives:
            match = re.search(_label(label) + r"[ \t]*[:：][ \t]*([^\r\n]+)", text)
            if match:
                value = re.split(stop, match.group(1))[0].strip()
                if value:
                    fields[field] = value[:200]
                    break
    for label, key in [("개업연월일", "opening_date"), ("법인설립일", "established_date")]:
        match = re.search(
            _label(label) + r"\s*[:：]?\s*(\d{4})\s*[년./-]\s*(\d{1,2})\s*[월./-]\s*(\d{1,2})",
            text,
        )
        if match:
            try:
                fields[key] = date(*map(int, match.groups())).isoformat()
            except ValueError:
                pass
    return fields


def read_registration(data: bytes, filename: str) -> RegistrationDraft:
    if not data or len(data) > MAX_BYTES:
        raise DocumentError("비어 있지 않은 10MB 이하 파일을 올려주세요.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text, method = _read_pdf(data)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        from PIL import Image, ImageOps

        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise DocumentError("이미지를 2,400만 화소 이하로 줄여주세요.")
                with ImageOps.exif_transpose(image).convert("RGB") as normalized:
                    text = _ocr(normalized)
        except DocumentError:
            raise
        except Exception:  # noqa: BLE001 - sanitize third-party and private-data errors
            raise DocumentError(
                "이미지 파일을 읽지 못했습니다. PNG 또는 JPG로 다시 저장해주세요."
            ) from None
        method = "로컬 OCR"
    else:
        raise DocumentError("PDF, PNG, JPG, WEBP 파일만 지원합니다.")
    if not text.strip():
        raise DocumentError("읽힌 글자가 없습니다. 선명한 문서로 다시 시도하거나 직접 입력하세요.")
    return RegistrationDraft(text=text, fields=suggest_fields(text), method=method)
