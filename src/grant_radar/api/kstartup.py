"""K-Startup(창업진흥원) 지원사업 공고 조회 API 클라이언트.

공공데이터포털 API:
  GET https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01

원칙:
- 인증키(ServiceKey)는 로그, 예외 메시지, 저장 파일 어디에도 포함하지 않는다.
- 실제 응답 구조는 아직 확인 전이므로, 이 모듈은 파싱된 JSON을 그대로 반환하고
  구조 해석(정규화)은 하지 않는다.
- 네트워크 오류와 일시적 서버 오류만 제한적으로 재시도한다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# httpx는 INFO 레벨에서 전체 요청 URL(ServiceKey 포함)을 로그로 남기므로 차단한다.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BASE_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01"
ANNOUNCEMENT_PATH = "/getAnnouncementInformation01"

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class KStartupApiError(Exception):
    """K-Startup API 호출 관련 오류의 기반 클래스."""


class NetworkError(KStartupApiError):
    """네트워크 연결 실패."""


class RequestTimeoutError(KStartupApiError):
    """요청 시간 초과."""


class AuthenticationError(KStartupApiError):
    """인증키 오류(미등록·기한 만료·접근 거부·미등록 IP)."""


class RateLimitError(KStartupApiError):
    """서비스 요청 제한 횟수 초과."""


class BadRequestError(KStartupApiError):
    """잘못된 요청 매개변수."""


class ServiceUnavailableError(KStartupApiError):
    """API 내부 오류 또는 서비스 이용 불가."""


class ResponseParseError(KStartupApiError):
    """응답 본문을 해석할 수 없음."""


class UnexpectedResponseError(KStartupApiError):
    """예상하지 못한 응답 구조 또는 상태 코드."""


# 공공데이터포털 게이트웨이 공통 오류 코드 (공식 가이드 기준)
ERROR_CODE_DESCRIPTIONS: dict[int, str] = {
    1: "애플리케이션 오류",
    10: "잘못된 요청 매개변수",
    12: "해당 오픈 API 서비스가 없거나 폐기됨",
    20: "서비스 접근 거부",
    22: "서비스 요청 제한 횟수 초과",
    30: "등록되지 않은 서비스키",
    31: "기한 만료된 서비스키",
    32: "등록되지 않은 IP",
    99: "기타 오류",
}

# 재시도 대상: 네트워크 오류와 일시적 서버 오류만. 인증·매개변수 오류는 재시도하지 않는다.
RETRYABLE_EXCEPTIONS = (NetworkError, RequestTimeoutError, ServiceUnavailableError)


def mask_secret(text: str, secret: str) -> str:
    """문자열에서 인증키(원문 및 URL 인코딩 형태)를 ***로 가린다."""
    if not secret:
        return text
    masked = text.replace(secret, "***")
    encoded = quote(secret, safe="")
    if encoded != secret:
        masked = masked.replace(encoded, "***")
    return masked


@dataclass(frozen=True)
class FetchResult:
    """단일 페이지 조회 결과. 원본 본문과 파싱 결과를 함께 보존한다."""

    page: int
    per_page: int
    status_code: int
    data: Any
    raw_text: str
    fetched_at: datetime


def _error_for_code(code: int, message: str) -> KStartupApiError:
    description = ERROR_CODE_DESCRIPTIONS.get(code, "알 수 없는 오류 코드")
    text = f"API 오류 코드 {code}: {description}"
    if message:
        text += f" ({message})"
    if code in (20, 30, 31, 32):
        return AuthenticationError(
            text + "\n.env의 KSTARTUP_API_KEY(일반 인증키 Decoding 값)를 확인하세요."
        )
    if code == 22:
        return RateLimitError(text)
    if code == 10:
        return BadRequestError(text)
    if code in (1, 12):
        return ServiceUnavailableError(text)
    return KStartupApiError(text)


def _parse_gateway_error(body_text: str) -> KStartupApiError | None:
    """공공데이터포털 게이트웨이의 XML 오류 응답을 해석한다.

    returnType=json을 요청해도 게이트웨이 수준 오류(인증 실패 등)는
    XML(OpenAPI_ServiceResponse)로 반환될 수 있다.
    body_text는 호출 측에서 인증키가 마스킹된 상태로 전달해야 한다.
    """
    stripped = body_text.lstrip()
    if not stripped.startswith("<"):
        return None
    code_match = re.search(r"<returnReasonCode>\s*(\d+)\s*</returnReasonCode>", stripped)
    msg_match = re.search(r"<returnAuthMsg>\s*([^<]*?)\s*</returnAuthMsg>", stripped)
    if code_match is None:
        code_match = re.search(r"<resultCode>\s*(\d+)\s*</resultCode>", stripped)
        msg_match = re.search(r"<resultMsg>\s*([^<]*?)\s*</resultMsg>", stripped)
    if code_match is None:
        return ResponseParseError(
            f"XML 응답을 받았지만 오류 코드를 확인하지 못했습니다. (본문 일부: {stripped[:200]!r})"
        )
    code = int(code_match.group(1))
    if code == 0:
        # resultCode 00 등 성공 코드인데 XML이면 returnType이 반영되지 않은 경우다.
        return UnexpectedResponseError(
            "returnType=json을 요청했지만 XML 응답을 받았습니다. "
            "요청 매개변수 처리 방식을 확인해야 합니다."
        )
    message = msg_match.group(1).strip() if msg_match else ""
    return _error_for_code(code, message)


class KStartupClient:
    """지원사업 공고 조회 클라이언트.

    transport 주입은 테스트에서 httpx.MockTransport를 쓰기 위한 것이다.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = 1,
        retry_wait: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key가 비어 있습니다.")
        self._api_key = api_key
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KStartupClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _mask(self, text: str) -> str:
        return mask_secret(text, self._api_key)

    def fetch_announcements_page(self, page: int = 1, per_page: int = 10) -> FetchResult:
        """공고 목록 한 페이지를 조회한다.

        재시도는 네트워크 오류·시간 초과·일시적 서버 오류에만 적용한다.
        """
        last_error: KStartupApiError | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                logger.info(
                    "재시도 %d/%d (대기 %.1fs)", attempt, self._max_retries, self._retry_wait
                )
                time.sleep(self._retry_wait)
            try:
                return self._fetch_once(page=page, per_page=per_page)
            except RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _fetch_once(self, *, page: int, per_page: int) -> FetchResult:
        params = {
            "ServiceKey": self._api_key,
            "page": page,
            "perPage": per_page,
            "returnType": "json",
        }
        logger.info(
            "GET %s page=%s perPage=%s returnType=json ServiceKey=***",
            ANNOUNCEMENT_PATH,
            page,
            per_page,
        )
        try:
            response = self._client.get(ANNOUNCEMENT_PATH, params=params)
        except httpx.TimeoutException as exc:
            # `from None`: 원본 예외에 인증키가 포함된 URL이 남는 것을 차단한다.
            raise RequestTimeoutError(
                f"요청 시간 초과: {type(exc).__name__}: {self._mask(str(exc))}"
            ) from None
        except httpx.HTTPError as exc:
            raise NetworkError(
                f"네트워크 오류: {type(exc).__name__}: {self._mask(str(exc))}"
            ) from None

        safe_text = self._mask(response.text)

        gateway_error = _parse_gateway_error(safe_text)
        if gateway_error is not None:
            raise gateway_error

        if response.status_code >= 500:
            raise ServiceUnavailableError(
                f"서버 오류: HTTP {response.status_code} (본문 일부: {safe_text[:200]!r})"
            )
        if response.status_code != 200:
            raise UnexpectedResponseError(
                f"예상하지 못한 HTTP 상태 코드 {response.status_code} "
                f"(본문 일부: {safe_text[:200]!r})"
            )

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ResponseParseError(
                f"JSON 파싱 실패: {exc.msg} (본문 일부: {safe_text[:200]!r})"
            ) from None

        return FetchResult(
            page=page,
            per_page=per_page,
            status_code=response.status_code,
            data=data,
            raw_text=safe_text,
            fetched_at=datetime.now(timezone.utc),
        )
