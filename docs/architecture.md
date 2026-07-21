# 아키텍처 (현재 단계)

## 설계 원칙

1. 지원 가능한 공고를 놓치지 않는 것을 우선한다 (과소 제외 > 과잉 제외).
2. 모든 판정에는 근거가 있어야 한다.
3. 결정론적 규칙 판정과 자연어 해석 판정을 분리한다.
4. 확인되지 않은 API 기능을 추측하지 않는다.
5. 공개 데이터와 내부 데이터(인증키, 실제 회사 정보)를 분리한다.

## 현재 구성 (3단계까지)

```
grant_radar/
├─ config.py                  # .env / 환경변수 로딩, 인증키 마스킹된 Settings
├─ api/kstartup.py            # K-Startup API 클라이언트 (한 페이지 조회, 오류 분류, 재시도)
├─ models/announcement.py     # 정규화 공고 모델 (DateField, ApplicationMethod 포함)
├─ normalization/kstartup.py  # 원본 응답 → 내부 모델 (실제 관찰 기반)
└─ __main__.py                # CLI: fetch (조회 + data/raw/ 원본 저장 + 구조 요약)
```

데이터 흐름:

```
.env (인증키) ─→ config.load_settings
                      │
K-Startup API ─→ api.kstartup.KStartupClient ─→ FetchResult
                      │                            │
                 오류 분류·마스킹              data/raw/*.json (원본 보존)
                                                   │
                                     normalization.normalize_page
                                                   │
                                     NormalizedAnnouncement (+issues)
```

정규화 원칙 (실제 관찰 `docs/api-observations.md` 기반):

- 빈 값(null/빈 문자열) → `None` 또는 빈 목록. 빈 문자열로 임의 변환하지 않음
- 날짜(`YYYYMMDD`) 파싱 실패 시 공고를 버리지 않고 원본+오류를 `DateField`에 보존
- 쉼표 구분 다중 값(`biz_enyy` 등) → 목록으로 분해
- 프로토콜 없는 URL은 `https://` 보충 후 `issues`에 기록
- 필드명 대소문자 변형과 철자 별칭(`aply_excl_trgt_ctnt`/`aply_exclt_trgt_ctnt`) 흡수
- 원본 항목은 `raw_data`에 그대로 보존 (알 수 없는 필드 포함)
- 정규화 중 특이사항은 `issues`에 축적 → 이후 판정 단계의 REVIEW_REQUIRED 근거

## 이후 단계에서 추가될 구성

- `storage/` — SQLite 저장, 신규/변경/동일 상태 판별
- `models/company.py`, `models/decision.py` — 가상회사, 판정 결과 모델
- `rules/` — 지역, 업력, 신청자 유형 등 결정론적 1차 규칙
- `services/` — 수집(ingestion), 판정(evaluation) 오케스트레이션
- `reporting/` — 판정 근거를 포함한 사람이 읽을 수 있는 보고서
