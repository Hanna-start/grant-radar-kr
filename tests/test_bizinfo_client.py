"""기업마당 API 클라이언트 테스트. 실제 API를 호출하지 않는다 (MockTransport)."""

import httpx
import pytest

from grant_radar.api.bizinfo import BizinfoClient
from grant_radar.api.kstartup import (
    AuthenticationError,
    ResponseParseError,
    UnexpectedResponseError,
)

FAKE_KEY = "fake-bizinfo-test-key"


def make_client(handler, **kwargs):
    return BizinfoClient(
        FAKE_KEY, transport=httpx.MockTransport(handler), retry_wait=0.0, **kwargs
    )


def success_body():
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
            "body": {
                "items": {"item": [{"pblancId": "PBLN_1", "pblancNm": "가상 공고"}]},
                "numOfRows": 1,
                "pageNo": 1,
                "totalCount": 1,
            },
        }
    }


def test_success_returns_parsed_json():
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=success_body())

    with make_client(handler) as client:
        result = client.fetch_announcements_page(page=2, per_page=50)
    assert result.status_code == 200
    assert result.data["response"]["body"]["totalCount"] == 1
    # 관찰된 요청 변수명(pageNo/numOfRows/dataType)을 사용한다
    assert captured["params"]["pageNo"] == "2"
    assert captured["params"]["numOfRows"] == "50"
    assert captured["params"]["dataType"] == "json"


def test_gateway_xml_error_maps_to_exception():
    def handler(request):
        return httpx.Response(
            200,
            text=(
                "<OpenAPI_ServiceResponse><cmmMsgHeader>"
                "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
                "<returnReasonCode>30</returnReasonCode>"
                "</cmmMsgHeader></OpenAPI_ServiceResponse>"
            ),
        )

    with make_client(handler) as client:
        with pytest.raises(AuthenticationError):
            client.fetch_announcements_page()


def test_non_success_result_code_raises():
    body = success_body()
    body["response"]["header"] = {"resultCode": "03", "resultMsg": "NODATA_ERROR"}

    def handler(request):
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        with pytest.raises(UnexpectedResponseError) as exc_info:
            client.fetch_announcements_page()
    assert "03" in str(exc_info.value)


def test_error_messages_never_contain_key():
    def handler(request):
        return httpx.Response(200, text=f"invalid json echo {FAKE_KEY}")

    with make_client(handler) as client:
        with pytest.raises(ResponseParseError) as exc_info:
            client.fetch_announcements_page()
    assert FAKE_KEY not in str(exc_info.value)
    assert "***" in str(exc_info.value)


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        BizinfoClient("")
