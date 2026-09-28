# 가상 데이터 실행 결과 예시

아래 결과는 실존 공고나 실제 회사 자료가 아니다. 저장소의 가상회사와 골든 fixture로
판정 상태가 어떻게 표현되는지 보여주는 축약 예시다.

| 공고 | 판정 | 핵심 근거 |
|---|---|---|
| `[골든] 전 조건 통과 — 전국 사업화 지원` | `ELIGIBLE` | 지역·업력·신청자 유형 조건 통과 |
| `[골든] 지역 불일치 — 부산 한정` | `INELIGIBLE` | 회사 소재지와 지원지역이 명확히 불일치 |
| `[골든] 청년 전용 — 연령 제한` | `REVIEW_REQUIRED` | 대표자 연령 정보가 없어 사람 확인 필요 |
| `[골든] 최소 정보 — 필드 대부분 누락` | `REVIEW_REQUIRED` | 누락 정보를 불합격으로 추정하지 않음 |

실제 실행에서는 각 규칙의 공고 값, 회사 값, 판정 사유, 원문 URL과 사람이 확인할 항목을
Markdown 및 JSON으로 함께 출력할 수 있다.

## 실행 매니페스트 예시

```json
{
  "schema_version": 1,
  "runs": [
    {
      "source": "kstartup",
      "started_at": "2026-09-04T16:30:00+09:00",
      "status": "complete",
      "pages_fetched": 11,
      "start_page": 1,
      "last_page": 11,
      "requested_pages": 20,
      "requested_per_page": 100,
      "range_complete": true,
      "collected": 1099,
      "reported_total": 1099,
      "stop_reason": "reported-total-reached",
      "errors": []
    }
  ]
}
```

위 숫자는 형식을 설명하기 위한 예시다. 실제 범위는 실행 시 생성되는
`data/run_manifest.json`에서 확인한다.
