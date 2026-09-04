"""기업마당(bizinfo) 중소기업 지원사업 공고 조회 API 클라이언트.

공공데이터포털 API (2026-08-21 관찰, docs/api-observations.md):
  GET https://apis.data.go.kr/1421000/bizinfo/pblancBsnsService

K-Startup과 같은 data.go.kr 게이트웨이를 쓰므로 인증키 마스킹, 게이트웨이
XML 오류 해석, 예외 유형, 재시도 정책을 kstartup 모듈에서 그대로 가져와
쓴다. 응답 구조만 다르다: {response: {header: {resultCode}, body: {...}}} —
성공 시 resultCode "00".
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Self

import httpx

from grant_radar.api.kstartup import (
    DEFAULT_TIMEOUT,
    RETRYABLE_EXCEPTIONS,
    FetchResult,
    KStartupApiError,
    NetworkError,
    RequestTimeoutError,
    ResponseParseError,
    ServiceUnavailableError,
    UnexpectedResponseError,
    _parse_gateway_error,
    mask_secret,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://apis.data.go.kr/1421000/bizinfo"
ANNOUNCEMENT_PATH = "/pblancBsnsService"


class BizinfoClient:
    """기업마당 지원사업 공고 조회 클라이언트.

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
        if max_retries < 0:
            raise ValueError("max_retries는 0 이상이어야 합니다.")
        self._api_key = api_key
        self._max_retries = max_retries
        self._retry_wait = retry_wait
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _mask(self, text: str) -> str:
        return mask_secret(text, self._api_key)

    def fetch_announcements_page(self, page: int = 1, per_page: int = 10) -> FetchResult:
        """공고 목록 한 페이지를 조회한다. 재시도 정책은 K-Startup과 동일."""
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
            "serviceKey": self._api_key,
            "dataType": "json",
            "pageNo": page,
            "numOfRows": per_page,
        }
        logger.info(
            "GET %s pageNo=%s numOfRows=%s dataType=json ServiceKey=***",
            ANNOUNCEMENT_PATH,
            page,
            per_page,
        )
        # 원본 httpx 예외에는 인증키가 포함된 URL이 있다 — except 밖에서 raise
        # (kstartup._fetch_once와 같은 이유).
        transport_error: KStartupApiError | None = None
        try:
            response = self._client.get(ANNOUNCEMENT_PATH, params=params)
        except httpx.TimeoutException as exc:
            transport_error = RequestTimeoutError(
                f"요청 시간 초과: {type(exc).__name__}: {self._mask(str(exc))}"
            )
        except httpx.HTTPError as exc:
            transport_error = NetworkError(
                f"네트워크 오류: {type(exc).__name__}: {self._mask(str(exc))}"
            )
        if transport_error is not None:
            raise transport_error

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
        if safe_text.lstrip().startswith("<"):
            raise ResponseParseError(
                f"XML/HTML 응답을 받았지만 오류 코드를 확인하지 못했습니다. "
                f"(본문 일부: {safe_text[:200]!r})"
            )

        parse_error: KStartupApiError | None = None
        try:
            data = json.loads(safe_text)
        except json.JSONDecodeError as exc:
            parse_error = ResponseParseError(
                f"JSON 파싱 실패: {exc.msg} (본문 일부: {safe_text[:200]!r})"
            )
        if parse_error is not None:
            raise parse_error

        # 관찰된 성공 응답: response.header.resultCode == "00"
        header = data.get("response", {}).get("header", {}) if isinstance(data, dict) else {}
        result_code = str(header.get("resultCode", "")).strip()
        if result_code and result_code not in ("0", "00"):
            raise UnexpectedResponseError(
                f"기업마당 응답 오류 코드 {result_code}: {str(header.get('resultMsg', '')).strip()}"
            )

        return FetchResult(
            page=page,
            per_page=per_page,
            status_code=response.status_code,
            data=data,
            raw_text=safe_text,
            fetched_at=datetime.now(UTC),
        )
