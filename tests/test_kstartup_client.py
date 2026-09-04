"""K-Startup API 클라이언트 테스트.

모든 HTTP 요청은 httpx.MockTransport로 대체한다. 실제 API를 호출하지 않고,
실제 인증키도 사용하지 않는다.
"""

import json
from urllib.parse import quote

import httpx
import pytest

from grant_radar.api.kstartup import (
    AuthenticationError,
    BadRequestError,
    KStartupApiError,
    KStartupClient,
    RateLimitError,
    RequestTimeoutError,
    ResponseParseError,
    ServiceGoneError,
    ServiceUnavailableError,
    UnexpectedResponseError,
    mask_secret,
)

# URL 인코딩 시 형태가 달라지는 문자를 포함한 가짜 키 (실제 키 아님)
FAKE_KEY = "fake+key/with==special"

SAMPLE_BODY = {
    "currentCount": 2,
    "data": [
        {"pbanc_sn": "1001", "biz_pbanc_nm": "테스트 공고 1"},
        {"pbanc_sn": "1002", "biz_pbanc_nm": "테스트 공고 2"},
    ],
}

GATEWAY_ERROR_XML = """<OpenAPI_ServiceResponse>
  <cmmMsgHeader>
    <errMsg>SERVICE ERROR</errMsg>
    <returnAuthMsg>{auth_msg}</returnAuthMsg>
    <returnReasonCode>{code}</returnReasonCode>
  </cmmMsgHeader>
</OpenAPI_ServiceResponse>"""


def make_client(handler, api_key=FAKE_KEY, **kwargs):
    kwargs.setdefault("retry_wait", 0.0)
    transport = httpx.MockTransport(handler)
    return KStartupClient(api_key, transport=transport, **kwargs)


def gateway_error_response(code, auth_msg="ERROR", status=200):
    return httpx.Response(
        status,
        text=GATEWAY_ERROR_XML.format(code=code, auth_msg=auth_msg),
        headers={"Content-Type": "application/xml"},
    )


class TestSuccess:
    def test_parses_json_and_sends_expected_params(self):
        seen_requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_requests.append(request)
            return httpx.Response(200, json=SAMPLE_BODY)

        with make_client(handler) as client:
            result = client.fetch_announcements_page(page=1, per_page=5)

        assert result.status_code == 200
        assert result.data == SAMPLE_BODY
        assert result.page == 1
        assert result.per_page == 5
        assert result.fetched_at is not None

        (request,) = seen_requests
        query = request.url.params
        assert query["ServiceKey"] == FAKE_KEY  # httpx가 디코딩해 돌려준 값
        assert query["page"] == "1"
        assert query["perPage"] == "5"
        assert query["returnType"] == "json"
        # 특수문자 키가 원시 쿼리에서 URL 인코딩되었는지 확인
        assert quote(FAKE_KEY, safe="") in str(request.url.query.decode("ascii"))

    def test_empty_list_is_not_an_error(self):
        def handler(request):
            return httpx.Response(200, json={"currentCount": 0, "data": []})

        with make_client(handler) as client:
            result = client.fetch_announcements_page()
        assert result.data["data"] == []

    def test_raw_text_preserved(self):
        def handler(request):
            return httpx.Response(200, json=SAMPLE_BODY)

        with make_client(handler) as client:
            result = client.fetch_announcements_page()
        assert json.loads(result.raw_text) == SAMPLE_BODY


class TestGatewayErrors:
    @pytest.mark.parametrize(
        "code,expected",
        [
            (1, ServiceUnavailableError),
            (10, BadRequestError),
            (12, ServiceGoneError),
            (20, AuthenticationError),
            (22, RateLimitError),
            (30, AuthenticationError),
            (31, AuthenticationError),
            (32, AuthenticationError),
            (99, KStartupApiError),
        ],
    )
    def test_error_code_mapping(self, code, expected):
        def handler(request):
            return gateway_error_response(code)

        with make_client(handler) as client:
            with pytest.raises(expected) as exc_info:
                client.fetch_announcements_page()
        # 하위 클래스 매칭으로 오분류가 숨지 않도록 정확한 타입을 단언한다
        assert type(exc_info.value) is expected
        assert str(code) in str(exc_info.value)

    def test_service_gone_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return gateway_error_response(12)

        with make_client(handler, max_retries=3) as client:
            with pytest.raises(ServiceGoneError):
                client.fetch_announcements_page()
        assert len(calls) == 1

    def test_gateway_error_recognized_even_with_5xx_status(self):
        # 게이트웨이가 인증 오류 XML을 5xx 상태와 함께 반환해도 인증 오류로 분류
        def handler(request):
            return gateway_error_response(30, status=500)

        with make_client(handler, max_retries=0) as client:
            with pytest.raises(AuthenticationError):
                client.fetch_announcements_page()

    def test_auth_error_message_guides_user(self):
        def handler(request):
            return gateway_error_response(30, "SERVICE_KEY_IS_NOT_REGISTERED_ERROR")

        with make_client(handler) as client:
            with pytest.raises(AuthenticationError) as exc_info:
                client.fetch_announcements_page()
        message = str(exc_info.value)
        assert "KSTARTUP_API_KEY" in message
        assert FAKE_KEY not in message

    def test_xml_without_error_code_is_parse_error(self):
        def handler(request):
            return httpx.Response(200, text="<unknown><shape/></unknown>")

        with make_client(handler) as client:
            with pytest.raises(ResponseParseError):
                client.fetch_announcements_page()

    def test_success_xml_means_return_type_ignored(self):
        def handler(request):
            return httpx.Response(
                200,
                text="<response><header><resultCode>00</resultCode>"
                "<resultMsg>NORMAL SERVICE.</resultMsg></header></response>",
            )

        with make_client(handler) as client:
            with pytest.raises(UnexpectedResponseError):
                client.fetch_announcements_page()


class TestTransportFailures:
    def test_invalid_json_raises_parse_error(self):
        def handler(request):
            return httpx.Response(200, text="this is not json {")

        with make_client(handler) as client:
            with pytest.raises(ResponseParseError):
                client.fetch_announcements_page()

    def test_timeout_raises_after_retries(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("timed out")

        with make_client(handler, max_retries=1) as client:
            with pytest.raises(RequestTimeoutError):
                client.fetch_announcements_page()
        assert len(calls) == 2  # 최초 1회 + 재시도 1회

    def test_network_error_then_success_retries(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json=SAMPLE_BODY)

        with make_client(handler, max_retries=1) as client:
            result = client.fetch_announcements_page()
        assert result.data == SAMPLE_BODY
        assert len(calls) == 2

    def test_http_5xx_is_retryable(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500, text="Internal Server Error")
            return httpx.Response(200, json=SAMPLE_BODY)

        with make_client(handler, max_retries=1) as client:
            result = client.fetch_announcements_page()
        assert result.data == SAMPLE_BODY
        assert len(calls) == 2

    def test_http_5xx_exhausts_retries(self):
        def handler(request):
            return httpx.Response(503, text="Service Unavailable")

        with make_client(handler, max_retries=1) as client:
            with pytest.raises(ServiceUnavailableError):
                client.fetch_announcements_page()

    def test_html_error_page_5xx_is_still_retryable(self):
        # 프록시/게이트웨이 장애 시 흔한 HTML 오류 페이지가
        # ResponseParseError로 오분류되어 재시도가 무력화되지 않아야 한다
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(
                    502, text="<html><body><h1>502 Bad Gateway</h1></body></html>"
                )
            return httpx.Response(200, json=SAMPLE_BODY)

        with make_client(handler, max_retries=1) as client:
            result = client.fetch_announcements_page()
        assert result.data == SAMPLE_BODY
        assert len(calls) == 2

    def test_html_error_page_404_is_unexpected_response(self):
        def handler(request):
            return httpx.Response(404, text="<html><body>Not Found</body></html>")

        with make_client(handler) as client:
            with pytest.raises(UnexpectedResponseError) as exc_info:
                client.fetch_announcements_page()
        assert "404" in str(exc_info.value)

    def test_auth_error_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return gateway_error_response(30)

        with make_client(handler, max_retries=3) as client:
            with pytest.raises(AuthenticationError):
                client.fetch_announcements_page()
        assert len(calls) == 1

    def test_unexpected_status_code(self):
        def handler(request):
            return httpx.Response(404, text="Not Found")

        with make_client(handler) as client:
            with pytest.raises(UnexpectedResponseError):
                client.fetch_announcements_page()


class TestKeyMasking:
    def test_mask_secret_plain_and_encoded(self):
        text = f"url?ServiceKey={FAKE_KEY}&raw={quote(FAKE_KEY, safe='')}"
        masked = mask_secret(text, FAKE_KEY)
        assert FAKE_KEY not in masked
        assert quote(FAKE_KEY, safe="") not in masked
        assert "***" in masked

    def test_mask_secret_empty_secret_is_noop(self):
        assert mask_secret("text", "") == "text"

    def test_mask_secret_covers_quote_plus_wire_encoding(self):
        # httpx는 urlencode(quote_plus)로 인코딩하므로 공백이 '+'가 된다
        from urllib.parse import quote_plus

        secret = "fake key with space+/="
        wire = quote_plus(secret)
        assert mask_secret(f"url?ServiceKey={wire}", secret) == "url?ServiceKey=***"

    @pytest.mark.parametrize(
        "handler_factory",
        [
            lambda: lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom")),
            lambda: lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
            lambda: lambda request: httpx.Response(200, text="not json {"),
            lambda: lambda request: gateway_error_response(30),
            lambda: lambda request: httpx.Response(500, text="oops"),
        ],
    )
    def test_exceptions_never_contain_key(self, handler_factory):
        with make_client(handler_factory(), max_retries=0) as client:
            with pytest.raises(Exception) as exc_info:
                client.fetch_announcements_page()
        message = str(exc_info.value) + repr(exc_info.value)
        assert FAKE_KEY not in message
        assert quote(FAKE_KEY, safe="") not in message
        # 예외 체인 자체에 원본 예외(키 포함 URL 보유)가 남지 않아야 한다.
        # from None은 __suppress_context__만 켜고 __context__를 지우지 않으므로
        # __context__까지 비어 있는지 확인한다.
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    def test_result_is_masked_even_if_body_echoes_key(self):
        def handler(request):
            # 서버가 요청 키를 본문에 되돌려주는 극단적 상황을 가정
            return httpx.Response(200, json={"echo": FAKE_KEY, "data": []})

        with make_client(handler) as client:
            result = client.fetch_announcements_page()
        # raw_text뿐 아니라 파싱된 data(저장 파일의 body가 되는 값)에도 키가 없어야 한다
        assert FAKE_KEY not in result.raw_text
        assert FAKE_KEY not in json.dumps(result.data)
        assert FAKE_KEY not in repr(result)
        assert result.data["echo"] == "***"

    def test_httpx_logger_is_suppressed(self):
        import logging

        assert logging.getLogger("httpx").level >= logging.WARNING


class TestClientBasics:
    def test_empty_api_key_rejected(self):
        with pytest.raises(ValueError):
            KStartupClient("")

    def test_negative_max_retries_rejected(self):
        with pytest.raises(ValueError):
            KStartupClient(FAKE_KEY, max_retries=-1)
