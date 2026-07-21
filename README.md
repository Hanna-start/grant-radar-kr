# Grant Radar KR

K-Startup 공개 데이터를 수집하고, 가상회사의 객관적 조건과 비교하여 검토
우선순위를 제공하는 **실험적 의사결정 보조 프로젝트**입니다.

이 시스템은 공식 자격 판정 도구가 아니며, 최종 의사결정이나 자동 신청을
수행하지 않습니다. 모든 판정 결과는 사람이 원문 공고를 확인하는 것을
전제로 합니다.

## 해결하려는 문제

정부·공공기관의 지원사업 공고는 여러 곳에 흩어져 있고, 각 공고의 자격
조건(지역, 업력, 신청자 유형 등)을 하나하나 확인하는 데 시간이 듭니다.
이 프로젝트는 공고를 정기적으로 수집하고, 변경하기 어려운 객관적 조건과
비교하여 "확인해 볼 가치가 있는 공고"를 먼저 골라내는 것을 목표로 합니다.

## 현재 개발 단계

**7단계: 표본 검증 완료** — 초기 MVP 완료 기준 충족

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

- 공공데이터포털: 창업진흥원_K-Startup(사업소개, 사업공고, 콘텐츠 등)_조회서비스
- 엔드포인트: `GET https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01`

## 설치

Python 3.12 이상이 필요합니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

(macOS/Linux는 `.venv/bin/python`을 사용합니다.)

## 설정 (.env)

1. [공공데이터포털](https://www.data.go.kr)에서 위 서비스의 활용 신청을 하고
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

저장된 공고를 가상회사 기준으로 판정하려면 (API 호출 없음):

```powershell
.venv\Scripts\python.exe -m grant_radar evaluate
```

공고별로 판정(`지원 가능`/`판단 필요`/`지원 불가`)과 규칙별 근거(공고 조건,
회사 정보, 판단 사유), 사람이 추가로 확인할 사항을 출력합니다.

수집과 판정을 한 번에 수행하고 Markdown 보고서를 저장하려면:

```powershell
.venv\Scripts\python.exe -m grant_radar run --report reports\report.md
```

- `--company PATH`: 다른 가상회사 JSON 지정 (`is_fictional: true`가 아니면 거부)
- `--report PATH`: 판정 보고서를 Markdown 파일로 저장 (`reports/`는 Git 제외)

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest
```

모든 테스트는 모의(Mock) HTTP 응답을 사용하며 실제 API를 호출하지 않습니다.

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
   ```

3. **JSON 출력** (`evaluate --json PATH`): 판정 결과를 기계가 읽는 형식으로
   저장해 실행 간 diff 비교나 외부 검토에 사용할 수 있습니다.

마감 여부 표시만 실행 시각(KST)에 의존하며, 자격 판정 결론은 저장된 원본
스냅샷·회사 데이터·규칙 버전에 의해서만 결정됩니다.

## 판정 상태

| 상태 | 의미 |
|---|---|
| `ELIGIBLE` (지원 가능) | 현재 확인된 필드 정보로 지원 가능 — 본문 확인 전제 |
| `REVIEW_REQUIRED` (판단 필요) | 정보가 부족하거나 사람의 판단이 필요 |
| `INELIGIBLE` (지원 불가) | 명확하고 객관적인 조건 불일치 (이유 함께 표시) |

정보가 없거나 표현이 모호하다는 이유만으로 공고를 제외하지 않는 것이
기본 원칙입니다. 마감된 공고는 자격 판정과 별도로 `마감`으로 표시됩니다.
규칙별 상세 기준은 [docs/eligibility-rules.md](docs/eligibility-rules.md)를
참고하세요.

## 가상회사 데이터

초기 버전은 실제 기업정보가 아닌 가상회사 데이터
([data/sample_company.json](data/sample_company.json))를 사용합니다.
`is_fictional: true`가 아닌 회사 데이터는 프로그램이 거부합니다.
실제 회사 정보는 공개 저장소에 포함하지 않습니다. 값이 `null`인 항목은
정보 부족을 뜻하며 조건 불충족으로 해석하지 않습니다.

## 한계와 면책

- 이 도구의 결과는 공식 자격 판정이 아닙니다.
- API 데이터는 누락되거나 지연될 수 있습니다.
- 공고 본문과 첨부파일은 반드시 사람이 최종 확인해야 합니다.
- "지원 가능" 판정이 선정 가능성을 의미하지 않습니다.

자세한 내용은 [docs/limitations.md](docs/limitations.md)를 참고하세요.

## 향후 계획

첨부파일 수집·텍스트 추출, 상세 검토 항목 추출, 알림(이메일/Slack) 등은
1차 판정이 검증된 후 단계적으로 검토합니다.

## 라이선스

[MIT License](LICENSE)
