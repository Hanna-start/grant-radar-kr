# 아키텍처 (현재 단계)

## 설계 원칙

1. 지원 가능한 공고를 놓치지 않는 것을 우선한다 (과소 제외 > 과잉 제외).
2. 모든 판정에는 근거가 있어야 한다.
3. 결정론적 규칙 판정과 자연어 해석 판정을 분리한다.
4. 확인되지 않은 API 기능을 추측하지 않는다.
5. 공개 데이터와 내부 데이터(인증키, 실제 회사 정보)를 분리한다.

## 현재 구성 (1단계)

```
grant_radar/
├─ config.py          # .env / 환경변수 로딩, 인증키 마스킹된 Settings
├─ api/kstartup.py    # K-Startup API 클라이언트 (한 페이지 조회, 오류 분류, 재시도)
└─ __main__.py        # CLI: fetch (조회 + data/raw/ 원본 저장 + 구조 요약)
```

데이터 흐름:

```
.env (인증키) ─→ config.load_settings
                      │
K-Startup API ─→ api.kstartup.KStartupClient ─→ FetchResult
                      │                            │
                 오류 분류·마스킹              data/raw/*.json (원본 보존)
                                                   │
                                              콘솔 구조 요약
```

## 이후 단계에서 추가될 구성

실제 API 응답을 확인한 뒤에 결정한다 (추측 금지 원칙).

- `normalization/` — 원본 응답 → 내부 공고 모델 (필드 변형 처리, 원본 보존)
- `storage/` — SQLite 저장, 신규/변경/동일 상태 판별
- `models/` — 공고, 가상회사, 판정 결과 모델
- `rules/` — 지역, 업력, 신청자 유형 등 결정론적 1차 규칙
- `services/` — 수집(ingestion), 판정(evaluation) 오케스트레이션
- `reporting/` — 판정 근거를 포함한 사람이 읽을 수 있는 보고서
