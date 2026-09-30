# Grant Radar KR

[![CI](https://github.com/Hanna-start/grant-radar-kr/actions/workflows/ci.yml/badge.svg)](https://github.com/Hanna-start/grant-radar-kr/actions/workflows/ci.yml)

K-Startup과 기업마당의 공개 지원사업 공고를 수집·정규화해 SQLite에 저장하고,
선택적으로 회사 조건과 비교하여 검토 우선순위를 만드는 **실험적 로컬 도구**입니다. CLI와 선택형 로컬 웹 화면을 제공합니다.

핵심 결과물은 재사용 가능한 공고 데이터베이스입니다. 회사별 판정, 보고서와 주간
이메일은 수집 결과를 활용하는 선택 기능이며 사용하지 않아도 됩니다.

이 시스템은 공식 자격 판정 도구가 아니며, 최종 의사결정이나 자동 신청을
수행하지 않습니다. 모든 판정 결과는 사람이 원문 공고를 확인하는 것을
전제로 합니다.

## 간편하게 시작하기 (Windows)

Python 3.12 이상 설치 후 **start-web.cmd**를 실행하면 로컬 웹 화면이 열립니다.
처음에는 웹 의존성을 설치하므로 인터넷이 필요합니다.

**회사 정보 확인 → API·메일 연결 → 시험 메일 → 공고 판정 → 주간 자동 메일** 순서로 설정합니다.
키 없이 결과를 보려면 화면의 **가상 회사로 체험하기**를 사용할 수 있습니다.

사업자등록증 PDF·사진에서 읽은 값은 직접 확인한 뒤 저장합니다. 사진·스캔 PDF에는
Tesseract와 한국어 데이터의 별도 설치가 필요합니다. 받는 이메일 외에 보내는 계정의
SMTP 인증정보도 필요하며, 자동 발송은 PC가 켜져 있고 로그인·인터넷 연결이 되어야 동작합니다.

설치, 정확한 입력 항목, OCR 준비와 저장 정보는 **[간편 시작 안내](docs/quickstart.md)**를 참고하세요.
기존 CLI 사용법은 아래에 유지합니다.

## 프로젝트 범위

```text
공공 API → 정규화 → SQLite 저장 → 신규·변경·마감 감지
                              └→ (선택) 회사별 규칙 판정 → Markdown/JSON → 이메일
```

- 포함: 공개 API 수집, 원본 보존, 공통 모델 변환, 변경 감지, 로컬 DB 저장
- 선택: 회사 프로필 판정, 관련도 A/B/C, 보고서, Windows 주간 실행과 SMTP 메일
- 제외: 인터넷에 공개하는 웹 서비스, 지원 자격 확정, 자동 신청, 공고 첨부파일 전문 분석

가상 데이터로 만든 출력 형태는 [examples/sample-results.md](examples/sample-results.md),
수집 범위와 완료 상태 정의는 [docs/sources.md](docs/sources.md)에서 볼 수 있습니다.

## 해결하려는 문제

정부·공공기관의 지원사업 공고는 여러 곳에 흩어져 있고, 각 공고의 자격
조건(지역, 업력, 신청자 유형 등)을 하나하나 확인하는 데 시간이 듭니다.
이 프로젝트는 공고를 정기적으로 수집하고, 변경하기 어려운 객관적 조건과
비교하여 "확인해 볼 가치가 있는 공고"를 먼저 골라내는 것을 목표로 합니다.

## 현재 개발 단계

**MVP 완료** — 수집·정규화·저장·판정 파이프라인과 재현성 테스트 구현

- [x] 프로젝트 구조 및 설정
- [x] K-Startup API 클라이언트 최소 구현 (한 페이지 조회)
- [x] 인증키 마스킹 및 오류 처리
- [x] 실제 API를 호출하지 않는 단위 테스트
- [x] 실제 API 첫 호출 및 응답 구조 확인 → [docs/api-observations.md](docs/api-observations.md)
- [x] 응답 정규화 모델 (실제 응답 기반, 원본은 `raw_data`에 보존)
- [x] SQLite 저장(`data/announcements.db`) 및 신규·변경 공고 감지
- [x] 가상회사 데이터와 1차 판정 규칙 (지역, 업력, 신청자 유형) →
  [docs/eligibility-rules.md](docs/eligibility-rules.md)
- [x] 판정 근거를 포함한 보고서 (`evaluate`, `run`, Markdown 저장)
- [x] 표본 검증 (실제 공고 100건, 규칙 판정과 수동 판정 비교) →
  [docs/validation-sample.md](docs/validation-sample.md)

## 데이터 원천

| 원천 | 공식 API 상세·활용 신청 | 사용하는 엔드포인트 |
|---|---|---|
| K-Startup | [창업진흥원 K-Startup 조회서비스](https://www.data.go.kr/data/15125364/openapi.do) | `https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01` |
| 기업마당 | [중소벤처기업부 중소기업 지원사업 공고 조회 서비스](https://www.data.go.kr/data/15157820/openapi.do) | `https://apis.data.go.kr/1421000/bizinfo/pblancBsnsService` |

## 설치

Python 3.12 이상이 필요합니다. Git으로 내려받고 저장소 폴더로 이동합니다.

```powershell
git clone https://github.com/Hanna-start/grant-radar-kr.git
cd grant-radar-kr
```

이미 내려받았다면 `pyproject.toml`과 `README.md`가 있는 폴더에서 다음을 실행합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

(macOS/Linux는 `.venv/bin/python`을 사용합니다.)

## 설정 (.env)

1. 위 표의 공식 API 페이지에서 사용할 원천별로 활용 신청을 하고
   **일반 인증키(Decoding)** 값을 발급받습니다.
2. `.env.example`을 `.env`로 복사한 뒤 인증키를 입력합니다.

```
KSTARTUP_API_KEY=발급받은_일반_인증키_Decoding_값
```

주의: 파일은 UTF-8 인코딩으로 저장하고, 값 뒤에 인라인 주석(`# ...`)을
붙이지 마세요 — 주석이 값의 일부로 취급되어 인증 오류가 발생합니다.

`.env`는 `.gitignore`에 의해 Git에서 제외됩니다. 인증키를 코드, 커밋,
로그, 이슈에 절대 포함하지 마세요. 프로그램은 로그와 오류 메시지에서
인증키를 `***`로 가립니다.

## 실행

프로젝트 루트( `.env`가 있는 곳)에서 실행합니다.

```powershell
.venv\Scripts\python.exe -m grant_radar fetch --page 1 --per-page 5
```

- 공고 목록 한 페이지를 조회해 응답 최상위 구조를 요약 출력합니다.
- 원본 응답은 `data/raw/` 아래 JSON 파일로 저장됩니다(인증키 미포함,
  Git 제외 대상). 원본 저장을 원하지 않으면 `--no-save`를 붙입니다.
- 공고는 `data/announcements.db`(SQLite, Git 제외)에 저장되며, 같은 공고를
  다시 수집하면 신규(`NEW`)·변경(`UPDATED`)·동일(`UNCHANGED`)·판단불가
  (`UNKNOWN`)로 구분해 보고합니다. 모집 종료가 확인되면 마감으로 표시합니다.

여러 페이지를 연속 수집하려면 `--pages`를 사용합니다 (전체 건수 도달 시
조기 종료):

```powershell
.venv\Scripts\python.exe -m grant_radar fetch --per-page 100 --pages 5
```

수집만 원하는 경우 여기까지 실행하면 됩니다. 결과는
`data/announcements.db`에 누적되며 회사 프로필이나 메일 설정은 필요하지 않습니다.
각 실행의 수집 범위와 종료 상태는 `data/run_manifest.json`에 기록됩니다.
`partial`은 부분 수집이며 전체 공고를 받았다는 뜻이 아닙니다. 요청한 시작 페이지,
마지막 응답 페이지와 실제 수집 건수가 기록됩니다. 중간 페이지부터 시작하면
마지막 페이지에 도달해도 `complete`로 표시하지 않습니다.
성공 종료 코드 `0`은 요청 범위의 정상 처리이며, 전체 수집 여부는 매니페스트로 확인합니다.

수집 원천은 `--source`로 선택합니다. 기본은 K-Startup(창업지원)이고,
`bizinfo`는 기업마당(중소기업 지원사업 — 인력·금융 분야 포함)입니다.
두 원천 모두 같은 인증키를 쓰지만, 공공데이터포털에서 데이터셋별
활용신청이 되어 있어야 합니다:

```powershell
.venv\Scripts\python.exe -m grant_radar fetch --source bizinfo --per-page 100 --pages 17
```

저장된 공고를 회사 기준으로 판정하려면 (API 호출 없음):

```powershell
.venv\Scripts\python.exe -m grant_radar evaluate
```

공고별로 판정(`우선 검토`/`판단 필요`/`지원 불가`)과 규칙별 근거(공고 조건,
회사 정보, 판단 사유), 사람이 추가로 확인할 사항을 출력합니다.

수집과 판정을 한 번에 수행하고 Markdown 보고서를 저장하려면:

```powershell
.venv\Scripts\python.exe -m grant_radar run --report reports\report.md
```

- `--company PATH`: 다른 회사 JSON 지정 (`is_fictional` 명시 필수)
- `--open-only`: 모집 중(마감되지 않은) 공고만 보고
- `--since YYYY-MM-DD`: 그 날짜 이후 처음 관측된 공고만 보고
- `--new-only` (run 전용): 이번 실행에서 처음 관측된 공고만 보고
- `--report PATH`: 판정 보고서를 Markdown 파일로 저장 (`reports/`는 Git 제외)

조건에 맞는 공고가 0건이어도 `--report`와 `--json`으로 지정한 파일을 새로 씁니다.
같은 경로를 다시 사용하면 이전 결과 대신 이번 0건 결과가 남습니다.

실무용 예시 (지정한 페이지 범위 수집 후 모집 중·신규만 보고):

```powershell
.venv\Scripts\python.exe -m grant_radar fetch --per-page 100 --pages 3
.venv\Scripts\python.exe -m grant_radar run --source bizinfo --per-page 100 --pages 17 --open-only --since 2026-08-22 --report reports\report.md
```

## 선택 기능: 정기 실행과 메일 보고

실행 시 언어모델을 호출하지 않습니다. 공고 수집에는 공공 API, 메일 발송에는
설정한 SMTP 서버를 사용합니다. Windows 작업 스케줄러가
`scripts/weekly_run.ps1`을 호출하고, 스크립트는 K-Startup·기업마당 수집 →
지난 실행 이후 신규·모집 중 공고 판정 → 보고서 메일 발송 순으로 돌며, 세 단계가
모두 성공했을 때만 `data/last_success.txt`(다음 실행의 `--since` 기준)를 갱신합니다.
실패한 주는 기준이 갱신되지 않아 다음 평가에도 이전 기준 이후의 신규 공고가 포함됩니다.
수집 페이지 수 자체가 늘어나거나 누락된 모든 기간을 자동으로 복구하는 것은 아닙니다.

기본 수집 범위는 K-Startup 3페이지, 기업마당 17페이지이며 페이지당 100건을 요청합니다.
**전체 공고 수집이나 신규 공고의 무누락을 보장하지 않습니다.** `partial`인 원천은
로그와 메일 본문에 수집 범위를 적고 메일 제목에 `[주의]`를 붙입니다. 부분 수집도
선택 범위 보고·발송이 성공하면 기준 시각을 갱신합니다. 전체 수집이 필요하면
`scripts/weekly_run.ps1`의 페이지 한도를 조정하고 매니페스트를 확인하세요.

`data/company.json`과 `data/company_*.json`을 자동으로 찾아 같은 공고를 각 회사
기준으로 판정하고, **한 통의 메일에 회사별 절을 나눠** 담습니다(추가 API 호출 없음).
회사 프로필이 없으면 저장소의 가상 샘플 프로필을 사용합니다.

1. `.env`에 메일 설정을 추가합니다 (`.env.example` 참고). `SMTP_PASSWORD`는 Google
   계정의 **앱 비밀번호**(2단계 인증 필요)이며 계정 비밀번호가 아닙니다.
2. 저장된 공고로 보고서를 생성한 뒤 메일 설정을 확인합니다 (아래 `mail`은 실제 발송):

   ```powershell
   .venv\Scripts\python.exe -m grant_radar evaluate --report reports\report.md --json reports\report.json
   .venv\Scripts\python.exe -m grant_radar mail --report reports\report.md --json reports\report.json
   ```

   지정한 보고서나 JSON 파일이 없으면 발송하지 않고 오류로 종료합니다.
   파일 부재를 ‘신규 공고 없음’으로 해석하지 않습니다. 0건 보고서는 ‘대상 공고 없음’으로 보냅니다.

3. 작업을 등록합니다 (한 번만, 사용자 본인이 실행):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
   ```

   월요일 09:00에 PC가 꺼져 있었으면 다음 켜진 직후 실행됩니다. 해제는
   `Unregister-ScheduledTask -TaskName "GrantRadar Weekly"`.

결과물: 프로필별 `reports/weekly/report-<프로필>-YYYYMMDD.md`,
`eval-<프로필>-YYYYMMDD.json`, `latest-<프로필>.md`, 로그
`logs/weekly-YYYYMMDD.log` (모두 Git 제외). 실패 시에는 로그 끝 30줄을 담은
실패 메일이 갑니다. 성공한 실행 끝에 `data/raw`의 30일 지난 원본 응답 파일을
삭제합니다(공고별 원본은 DB `raw_json`에 남아 판정 재현에 영향 없음).

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest
```

테스트는 모의(Mock) HTTP·SMTP를 사용하며 실제 API 호출이나 메일 발송을 하지 않습니다.
PowerShell이 있으면 주간 스크립트도 CLI 대역으로 실행해 부분 수집 경고와 실패 시
기준 시각 보존을 확인합니다. CI는 Linux와 Windows에서 실행합니다.

## 재현성 검증 (같은 입력 → 같은 결론)

판정 경로에는 랜덤·언어모델이 없어 결정론적입니다. 이를 상시 확인하는
장치가 세 가지 있습니다:

1. **골든 스냅샷** (`tests/test_golden.py`): 가상 공고 표본 12건(판정 경로
   전부 커버)의 기대 판정을 `tests/golden_expected.json`에 고정. 규칙 변경으로
   결론이 바뀌면 테스트가 diff로 드러냅니다. 의도된 변경이면
   `$env:UPDATE_GOLDEN="1"`로 재생성 후 diff를 검토·커밋합니다.
2. **독립 구현 크로스체크** (`scripts/cross_check.py`): grant_radar 코드를
   사용하지 않는 별도 로직으로 저장된 전체 공고를 재판정해 파이프라인과
   전수 대조합니다 (종료 코드 0=일치).

   ```powershell
   .venv\Scripts\python.exe scripts\cross_check.py
   .venv\Scripts\python.exe scripts\cross_check.py --company data\company_example.json
   ```

   회사 프로필별로 각각 돌려야 합니다 (범주·업력 분기가 회사마다 다름).

3. **JSON 출력** (`evaluate --json PATH`): 판정 결과를 기계가 읽는 형식으로
   저장해 실행 간 diff 비교나 외부 검토에 사용할 수 있습니다.

마감 여부 표시만 실행 시각(KST)에 의존하며, 자격 판정 결론은 저장된 원본
스냅샷·회사 데이터·규칙 버전에 의해서만 결정됩니다.

## 판정 상태

| 상태 | 의미 |
|---|---|
| `ELIGIBLE` (우선 검토) | 구조화 필드 조건을 모두 통과 — 자격 확인이 아니며 본문 확인 전제 |
| `REVIEW_REQUIRED` (판단 필요) | 정보가 부족하거나 사람의 판단이 필요 |
| `INELIGIBLE` (지원 불가) | 명확하고 객관적인 조건 불일치 (이유 함께 표시) |

정보가 없거나 표현이 모호하다는 이유만으로 공고를 제외하지 않는 것이
기본 원칙입니다. 마감된 공고는 자격 판정과 별도로 `마감`으로 표시됩니다.

자격 판정과 별개로 **관련도**(A 관련 높음 / B 보통 / C 관련 낮음)를 공고
내용(제목·사업개요·신청대상·분야)으로 매겨 보고서 배치를 정합니다
(`data/reference/relevance.json`). 공통 설정에는 자금·고용·판로처럼 범용적인
신호만 두고, 회사별 업종·관심·비관심 정책은 회사 JSON에서 지정합니다.
C는 제외가 아니라 배치이며 판정 결과는 바뀌지 않습니다.
규칙별 상세 기준은 [docs/eligibility-rules.md](docs/eligibility-rules.md)를
참고하세요.

## 회사 데이터

기본 회사는 `data/company.json`(실제 회사, **Git 제외**)이 있으면 그 파일,
없으면 저장소에 동봉된 가상회사
([data/sample_company.json](data/sample_company.json))입니다.

- 회사 데이터에는 `is_fictional`을 `true`/`false`로 반드시 명시해야 하며,
  명시가 없으면 프로그램이 거부합니다.
- 실제 회사 데이터는 `is_fictional: false`로 명시해야 합니다. 실제 회사
  정보 파일은 `.gitignore`로 제외하며 공개 저장소에 포함하지 않습니다.
- 값이 `null`인 항목은 정보 부족을 뜻하며 조건 불충족으로 해석하지
  않습니다. 테스트는 계속 가상회사 데이터만 사용합니다.
- 대표자 연령은 `representative_birth_date`(생년월일) 대신
  `representative_age`(기준일 시점 만 나이)로 줄 수 있습니다. 개인정보를
  줄이기 위한 대안이며, 둘 다 있으면 생년월일을 우선합니다.
- `business_districts`(사업장 소재 자치구·시군 목록)를 주면 제목에 다른
  자치구·시군이 한정된 공고(예: "[서울] 성동구 …", "과천시 …")를 관련도
  C로 내립니다. 자격 판정은 시·도 단위 지역 규칙 그대로입니다.
- `small_business`(소상공인 해당 여부)를 `true`로 주면 신청 대상이 "소상공인"인
  공고가 '우선 검토'로 올라옵니다. 상시근로자 산정 규칙 때문에 회사 데이터에서
  자동으로 파생하지 않고 확인된 값만 받습니다. 값이 없으면 해당 공고는
  제외되지 않고 '판단 필요'로 남습니다.
- `sme`는 확인된 중소기업 해당 여부입니다. `true`인 경우에만 "중소기업" 대상
  공고와 일치로 처리하며, 미입력·`null`이면 판단 필요로 남깁니다.
- `matching_keywords`는 회사 업종·제품·기술을 나타내는 명시 키워드입니다.
- `interest_keywords`는 `{주제: [키워드...]}` 형태의 회사별 관심 분야입니다.
- `exclude_keywords`, `exclude_categories`, `demote_keywords`는 선택적인 보고서
  배치 정책입니다. 자격 판정을 바꾸지 않습니다.

### 회사가 여럿일 때

같은 수집 데이터를 회사별로 다시 판정하면 됩니다 (`evaluate`는 API를 호출하지
않습니다). 회사 JSON은 `data/company_*.json`으로 두면 Git에서 제외됩니다.

```powershell
.venv\Scripts\python.exe -m grant_radar evaluate --company data\company_example.json --open-only --report reports\report-example.md
```

`scripts/weekly_run.ps1`은 `data/company.json`과 `data/company_*.json`을 자동으로
찾아 수집된 공고를 각각 판정합니다. 프로필이 없으면 가상 샘플을 사용합니다.

## 한계와 면책

- 이 도구의 결과는 공식 자격 판정이 아닙니다.
- API 데이터는 누락되거나 지연될 수 있습니다.
- 공고 본문과 첨부파일은 반드시 사람이 최종 확인해야 합니다.
- "우선 검토" 판정이 자격 확정이나 선정 가능성을 의미하지 않습니다.

자세한 내용은 [docs/limitations.md](docs/limitations.md)를 참고하세요.

## 참고한 프로젝트

수집 범위를 기계가 읽을 수 있는 실행 매니페스트로 남기는 아이디어는 MIT 라이선스의
[djfksjd/ir-search](https://github.com/djfksjd/ir-search)에서 영감을 받았습니다.
구현은 이 저장소의 API·SQLite 구조에 맞게 독립적으로 작성했습니다.

## 프로젝트 상태와 향후 범위

현재 버전은 공개 API 공고를 로컬에 수집하고 구조화하는 MVP입니다. 첨부파일
수집·텍스트 추출, 상세 자격 검토와 인터넷 공개형 웹 서비스는 구현 범위에 포함되지 않습니다. 로컬 웹 입력 화면은 선택 기능입니다.
정확도 한계와 검증 범위는 [docs/limitations.md](docs/limitations.md)에 공개합니다.

## 라이선스

[MIT License](LICENSE)

MIT는 이 저장소의 코드에 적용됩니다. 수집한 공고 데이터의 이용 조건은 각 원천의
공식 API 페이지를 확인하세요. 특히 [기업마당 API](https://www.data.go.kr/data/15157820/openapi.do)는
2026-09-28 확인 기준 ‘공공저작물 출처표시·변경금지(제3유형)’로 안내되어 있습니다.
공개 콘텐츠의 예시는 저장소에 동봉한 가상 데이터를 사용하세요.
