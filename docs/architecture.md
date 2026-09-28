# 아키텍처 (현재 단계)

## 설계 원칙

1. 지원 가능한 공고를 놓치지 않는 것을 우선한다 (과소 제외 > 과잉 제외).
2. 모든 판정에는 근거가 있어야 한다.
3. 결정론적 규칙 판정과 자연어 해석 판정을 분리한다.
4. 확인되지 않은 API 기능을 추측하지 않는다.
5. 공개 데이터와 내부 데이터(인증키, 실제 회사 정보)를 분리한다.

## 현재 구성

```
grant_radar/
├─ config.py                  # .env / 환경변수 로딩, 인증키 마스킹된 Settings
├─ api/kstartup.py            # K-Startup API 클라이언트 (페이지 조회, 오류 분류, 재시도)
├─ api/bizinfo.py             # 기업마당 API 클라이언트 (2026-08-21 추가, 오류·마스킹 공유)
├─ models/
│  ├─ announcement.py         # 정규화 공고 모델 (DateField, ApplicationMethod 포함)
│  ├─ company.py              # 회사 모델 (is_fictional true/false 명시제)
│  └─ decision.py             # RuleResult / EvaluationResult (근거 필수)
├─ normalization/kstartup.py  # K-Startup 응답 → 내부 모델 (실제 관찰 기반)
├─ normalization/bizinfo.py   # 기업마당 응답 → 내부 모델 (해시태그→시도 추출 포함)
├─ rules/                     # 결정론적 1차 규칙 (region/business_age/applicant_type/age/industry)
├─ storage/sqlite.py          # SQLite 저장, 해시 기반 변경 감지 (previous_hash 보존)
├─ services/
│  ├─ ingestion.py            # 수집: 정규화 → 저장 → 변경/마감 판별
│  └─ evaluation.py           # 판정: 규칙 실행 → 전체 판정 (자동 제외 목록 관리)
├─ relevance.py               # 관련도 층 A/B/C (보고서 배치용, 자격 판정과 별개)
├─ reporting/console.py       # 19절 형식 보고서 (콘솔 + Markdown + JSON, 관련도 섹션)
├─ notify/mail.py             # 주간 보고 메일 (smtplib, 본문은 Markdown에서 기계적으로 추출.
│                             #   ReportRef 목록 → 회사별 절을 한 통에)
└─ __main__.py                # CLI: fetch / evaluate / run / mail

scripts/
├─ weekly_run.ps1             # 정기 실행 본체 (1회 수집→회사 프로필별 판정→메일→상태 갱신)
├─ register_task.ps1          # Windows 작업 스케줄러 등록 (매주 월 09:00, 사용자가 실행)
└─ cross_check.py             # 독립 구현 크로스체크
```

정기 실행 원칙 (2026-08-23): 실행 경로에 언어모델이 없다. 실패 시 `data/last_success.txt`를
갱신하지 않아 다음 실행의 `--since`가 자동으로 공백을 메운다. 메일은 구성된 수신 범위
(`REPORT_MAIL_TO` 한 곳, 주간 보고)에서만 보낸다.

참조 데이터:

- `data/company.json` — 실제 회사 데이터 (Git 제외, 있으면 기본 회사)
- `data/company_*.json` — 추가 회사 프로필 (Git 제외). 같은 DB를 회사별로
  다시 판정할 뿐 수집을 반복하지 않는다
- `data/sample_company.json` — 가상회사 (실존 기업 아님, 테스트 기준)
- `data/reference/region_mapping.json` — 지역 매핑표 (시도명 별칭, 수도권 등 그룹)
- `data/reference/industry_keywords.json` — 업종·분야 키워드 표 (관찰 기반)
- `data/reference/relevance.json` — 회사 중립적인 관련도 기본 설정

수집 원천 (`--source`, 둘 다 같은 data.go.kr ServiceKey 인증):

- `kstartup` — K-Startup 창업지원 공고 (기본값)
- `bizinfo` — 기업마당 중소기업 지원사업 공고 (2026-08-21 추가.
  분야 필드에 인력·금융 포함 — 고용장려금류 공고가 이 경로로 수집된다)

데이터 흐름 (bizinfo도 동일 구조, 클라이언트·정규화기만 원천별):

```
.env (인증키) ─→ config.load_settings
                      │
K-Startup API ─→ api.kstartup.KStartupClient ─→ FetchResult
                      │                            │
                 오류 분류·마스킹              data/raw/*.json (원본 보존)
                                                   │
                                     services.ingestion.ingest_page
                                        │                      │
                          normalization.normalize_page   storage.AnnouncementStore
                                        │                (data/announcements.db)
                          NormalizedAnnouncement (+issues)     │
                                        │              NEW/UPDATED/UNCHANGED/UNKNOWN
                                        └──────→ IngestOutcome (+CLOSED 판별)
```

변경 감지 (지시서 12절):

- 식별자: `source + source_id` (`pbanc_sn`). source_id가 없으면 UNKNOWN으로
  보고하고 저장하지 않는다 (수집 결과에서는 유지).
- 주요 필드(제목, 내용, 지원 분야, 대상, 제외 대상, 지역, 접수 시작/종료 원본 문자열,
  상세 URL, 모집 여부, 업력·연령 조건, 신청자 유형, 우대 조건)의 SHA-256 해시를 비교한다.
- 기존 DB의 정규화 스냅샷도 같은 필드 집합으로 비교한다. 해시 기준 확장만으로
  모든 공고를 변경으로 세지 않으며, 동일 내용이면 저장 해시만 갱신하고 변경 이력은 유지한다.
- 변경 시 직전 해시(previous_hash)와 변경 시각(last_changed_at)을 남긴다.
- 해시 대상이 아닌 필드만 바뀌면 UNCHANGED로 보고하되 저장 본문은 최신으로 갱신한다.

마감(CLOSED) 정책 (지시서 14.5절):

- `rcrt_prgs_yn`이 명확히 N이거나, 접수 종료일이 지났으면 마감.
- 종료일은 날짜만 제공되므로(YYYYMMDD) 해당 날짜의 Asia/Seoul(UTC+9)
  하루가 끝날 때까지는 마감으로 보지 않는다.
- 종료일 파싱 실패·정보 부족은 마감 사유가 아니다.
- 마감은 저장 상태와 별개의 표시이며, 보고 시 CLOSED가 우선한다.

정규화 원칙 (실제 관찰 `docs/api-observations.md` 기반):

- 빈 값(null/빈 문자열) → `None` 또는 빈 목록. 빈 문자열로 임의 변환하지 않음
- 날짜(`YYYYMMDD`) 파싱 실패 시 공고를 버리지 않고 원본+오류를 `DateField`에 보존
- 쉼표 구분 다중 값(`biz_enyy` 등) → 목록으로 분해
- 프로토콜 없는 URL은 `https://` 보충 후 `issues`에 기록
- 필드명 대소문자 변형과 철자 별칭(`aply_excl_trgt_ctnt`/`aply_exclt_trgt_ctnt`) 흡수
- 원본 항목은 `raw_data`에 그대로 보존 (알 수 없는 필드 포함)
- 정규화 중 특이사항은 `issues`에 축적 → 이후 판정 단계의 REVIEW_REQUIRED 근거

## 구현 범위 밖

- 웹 UI와 사용자 계정
- 지원사업 자동 신청
- 공고 첨부파일 전문 수집·해석
- 법적·공식 자격 확정
