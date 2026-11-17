# InvestLab AI

Python 3.12 + FastAPI로 DART·미국 SEC 공시와 토스 한국·미국 시세를 읽고 OpenAI GPT API로 분석과 질문 답변을 제공합니다.
실제 공시·API 키가 없으면 샘플 분석을 성공 응답으로 반환하지 않습니다.

## 채팅으로 사용하기

환경 설정을 마쳤다면 **`start-chat.cmd`를 더블클릭**하세요. 서버를 시작하고 브라우저의 채팅 화면을
자동으로 엽니다. `.env.neon`이 있는 이 PC에서는 공용 Neon에 연결하며 http://127.0.0.1:8002/ 를 사용합니다.
기존 로컬 DB 실행은 `start-local-chat.cmd`이며 8000~8002 중 사용할 수 있는 주소를 화면에 표시합니다.

채팅창에 `삼성전자 최신 공시 설명해줘`, `SK하이닉스 2025년 사업보고서 설명해줘`처럼 입력합니다.
회사와 최근 3년의 사업·반기·분기보고서를 자동으로 찾고 원문을 준비합니다. 이름이 모호하면 기업 선택을
안내합니다. 이어서 `매출이 감소한 이유는?`, `그럼 주요 위험요인은?`처럼 질문할 수 있습니다.
공시 번호·서비스 토큰·JSON을 일반 채팅 화면에 입력할 필요가 없습니다.
`브이원택`처럼 한 글자 정도 틀린 이름은 비슷한 기업 후보를 보여주며, 사용자가 선택한 뒤에만 분석합니다.
`분석 가능한 기업은 몇 개야?`라고 물으면 현재 DB의 기본 분석 기업 수와 공시 수를 답합니다.
미리 분석한 시총 상위 100개 기업으로 사용 대상을 제한하지 않습니다. 다른 기업도 DART 목록의 정확한
기업명이나 6자리 종목코드로 요청하면 정기보고서를 새로 수집하고 기본 분석을 저장합니다.

기본 분석은 **공시 접수번호·원문 해시·모델·분석 버전**을 기준으로 DB에 저장해 다른 사용자/새 대화에서도
재사용합니다. 저장된 기본 분석 조회에는 GPT 호출이 없습니다. 원문 변경·정정 접수번호·모델 변경은 별도로
구분합니다. 기본 분석은 사업·실적·위험의 핵심 발췌를 고르게 선택한 **개요**이며 문서 전체 정밀 검토를
뜻하지 않습니다. 추가 질문은 해당 원문을 검색해 새 GPT 답변을 생성합니다. 원문 임베딩은 추가 질문에
필요할 때 한 번 생성하며 다시 사용합니다. `/analyze`의 전체 부분별 분석도 별도 캐시로 저장합니다.

채팅은 로컬 개발 화면입니다. loopback Host만 허용하고 HttpOnly/SameSite 쿠키와 동일 Origin 검사로
서버의 서비스 토큰을 브라우저에 노출하지 않습니다. 공개 서비스 로그인·사용자 권한은 기존 백엔드와
후속 연결해야 하며, 채팅 화면 자체를 외부에 공개하는 방식으로 사용하지 않습니다.

## 시총 100개 기업 / 최근 3년 사전 수집

코스피·코스닥 **보통주 시가총액**으로 DART 기업을 한 번씩 선정합니다. 순위 출처는 네이버 증권이며
조회시각·종목별 시세시각·원화 시총을 저장합니다. 과거 시점의 시총 순위를 복원한 데이터는 아닙니다.
최근 3년은 수집 실행일로부터 3년 전까지의 **공시 접수일** 기준이고 사업·반기·분기보고서와 정정본을
포함합니다. 수시공시는 제외합니다. 원문이 제공되지 않는 공시는 실패 사유로 남기며 성공으로 표시하지 않습니다.
OpenDART 원본 파일이 없는 경우 공개 DART 뷰어의 **동일 접수번호** 본문을 보완 수집합니다.
이 경우 ZIP에 뷰어 문서 식별자·구간·출처 manifest를 함께 보관합니다. 다른 접수번호로 대체하지 않습니다.

```powershell
# 목록 수집 → 원문 저장 → 공시별 기본 분석. 실제 GPT API 사용료가 발생합니다.
.\.venv\Scripts\python.exe -m investlab_ai.bulk --phase all --workers 3
# 원문만 먼저 보관(GPT 호출 없음)
.\.venv\Scripts\python.exe -m investlab_ai.bulk --run-id 실제실행ID --phase collect --workers 4
# 미완료 분석만 이어서 실행
.\.venv\Scripts\python.exe -m investlab_ai.bulk --run-id 실제실행ID --phase analyze --workers 3
# 기존 작업을 종료한 뒤 중단된 진행 상태 복구가 필요한 경우 --recover-running 추가
```

진행 상태는 연구 DB와 `.local/bulk-progress.json`에 저장합니다. 완료된 분석은 다시 생성하지 않습니다.
기본 분석 생성은 DB 임대 잠금으로 프로세스 간 중복을 막고 성공 결과만 캐시에 기록합니다. 프로세스가
강제 종료되면 임대는 10분 뒤 만료됩니다. 출력 일부만 받은 실패/타임아웃 비용은 복구할 수 없습니다.

초기 사전 수집으로 이 PC에 2026-11-14에 선정한 100개 기업의 2023-11-14~2026-11-14 접수 정기보고서
**1,474건 원문과 기본 분석 1,474건**이 저장되어 있습니다. 정정본을 포함하며 미완료 건은 없습니다.
대상 중 맥쿼리인프라는 해당 기간 DART 정기보고서 조회 결과가 0건입니다. 초기 DB는 `.local/research.db`이고
Docker PostgreSQL 전환 후에는 Docker 볼륨에 저장합니다. 서버를 껐다 켜도 유지됩니다.
이 PC에서 사용하기 위해 사전 수집 명령을 다시 실행할 필요는 없습니다.
코드를 GitHub에 올릴 때 이 로컬 DB와 API 키는 포함되지 않으므로 다른 PC로 분석 데이터가 자동 복사되지는 않습니다.

## 해외주식: 미국 상장기업·SEC 정기보고서

`엔비디아 최신 공시 설명해줘`, `Apple 공시 설명해줘`, `AAPL 현재 주가 얼마야?`,
`테슬라 주가 앞으로 한 달 동안 오를까?`처럼 질문합니다. 기업명이 모호하면 선택을 안내합니다.
`그럼 매출은?`처럼 이어서 물으면 같은 SEC 보고서 원문에서 근거를 검색합니다.
토스는 가격·일봉 조회에, SEC EDGAR는 공시에 사용합니다. 토스에서 공시를 가져오는 기능은 없습니다.

사전 수집 범위는 **NYSE·Nasdaq·AMEX 상장 기업 중 기업 단위 시총 상위 100개**(ADR 포함)입니다.
CompaniesMarketCap의 공개 USD 시총을 SEC 거래소·기업 목록과 대조하며 CIK당 한 기업만 선택합니다.
주식 종류가 여러 개여도 같은 기업을 중복 선정하지 않고 ETF는 제외합니다. 전 세계 모든 거래소의 100개는 아닙니다.
선정시각·시총·출처·CIK·티커를 스냅샷으로 남기며 현재 순위는 과거 백테스트용 순위가 아닙니다.

최근 3년은 뉴욕 기준 수집일로부터 3년 전까지의 **접수일** 기준입니다.
`10-K`, `10-Q`, `20-F`, `40-F`와 해당 `/A` 정정본을 모두 수집하며 역사 제출 목록까지 확인합니다.
`8-K`·`6-K` 수시공시는 제외합니다. 해외 발행사는 미국 기업처럼 분기 10-Q를 제출하지 않을 수 있습니다.
2026-11-16 최초 목록은 100개 기업·1,012건입니다. 실제 완료 수는 채팅 왼쪽이나 `.local/sec-progress.json`에서 확인합니다.

`.env.sec.example`을 비공개 `.env.sec`으로 복사하고 `YOUR_CONTACT_EMAIL`만 실제 연락 가능한 주소로 바꿉니다.
SEC 요청자 식별 헤더에만 사용하고 DB·이미지·채팅 응답에는 저장하지 않습니다. SEC API 키는 필요하지 않습니다.
요청은 PC의 파일 잠금으로 공유하고 초당 2건으로 제한합니다. 403은 우회하지 않고 중단 사유를 기록합니다.

```powershell
# 기존 작업 이어서: 새 보고서 분석에는 OpenAI API 비용이 발생합니다.
.\collect-us-top100.cmd
# GPT 호출 없이 원문만 수집
.\collect-us-top100.cmd --stage collect
# 미완료 분석만 이어서 생성
.\collect-us-top100.cmd --stage analyze
# 새 선정일의 상위 100개·최근 3년 목록을 만든 뒤 재개
.\collect-us-top100.cmd --refresh-universe
```

공시 원문 HTML(40-F는 관련 HTML 재무 첨부 최대 6개 포함)을 manifest와 ZIP으로 보관하고,
본문·표를 검색 청크로 만들어 압축 파일에 저장합니다. 원문·청크는 Neon의 비공개 `disclosures` 버킷
`sec/CIK/accession/` 경로에 있으며 저장 후 다시 읽어 SHA-256을 확인합니다.
`investlab_research`의 `rag_sec_issuers/filings/analyses/runs/items`는 기업·접수번호·파일 키·분석·진행 상태를 보관합니다.
SEC의 CIK/accession과 DART 기업코드/접수번호는 별도 식별자로 유지하며 SEC 기업에 임의 DART 번호를 만들지 않습니다.
이번 SEC 자료는 이 `rag_sec_*`에 저장하며 기존 DART 전용 ERD 동기화 트리거에는 넣지 않습니다.

기본 분석은 공시·원문 해시·파서·모델·발췌 예산 기준으로 공유 저장하고 중복 유료 생성을 임대 잠금으로 막습니다.
보고서당 최대 10,000토큰 예산에서 핵심 발췌를 골라 GPT를 한 번 호출합니다. 문서 전체 정밀 검토는 아닙니다.
추가 질문은 전체 저장 청크를 한영 재무 검색어로 검색해 관련 발췌를 GPT에 제공하는 RAG입니다.
SEC 경로는 현재 어휘 검색을 사용하며 임베딩 API 비용은 발생하지 않습니다. PDF·이미지 OCR·외부 링크는 분석하지 않습니다.
회계연도·분기/누적·USD/백만USD/주당금액이 불명확하면 답변에서 확인사항으로 남깁니다.
분석 실패는 성공으로 저장하지 않으며 크레딧/사용한도 소진 시 대량 GPT 호출을 중지하고 원문은 보존합니다.
같은 실행을 재개할 때 분석 모델·발췌 예산은 유지합니다. 바꾸려면 새 실행을 만들고 기존 모델의 캐시는 그대로 둡니다.
공용 파일 저장소를 설정하지 않은 개발 환경은 `.local/private/sec-public-evidence`에 원문을 보관합니다.

미국 가격은 USD 소수점을 유지하고 일봉은 `America/New_York` 날짜로 당일 봉을 제외합니다(서머타임 포함).
완료된 실제 거래일 자료를 사용하므로 휴장일에 새 봉을 만들지 않습니다. 토스 미국 시세는 일부 거래소 체결이며
전체 시장 통합 최우선 호가(NBBO)가 아닙니다. 시세 시각·통화·자료 지연을 화면에 표시합니다.
주가 질문은 기술 지표와 확보한 SEC 원문 근거의 조건부 시나리오이며 상승확률·목표가를 생성하지 않습니다.
저장된 공시가 없는 기업은 SEC 정기보고서를 새로 준비하며, 최초 공시 설명에는 분석 비용이 발생합니다.
시장 전망은 원문 발췌를 직접 사용하므로 별도의 기본 요약 GPT 호출을 강제하지 않습니다.
토스 가격·캔들·전망·종목 한글명은 개인 로컬 캐시에만 남기며 SEC 원문·기본 분석만 팀 Neon에서 공유합니다.

기준: [SEC 데이터 API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces),
[SEC 공정 접근](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data),
[토스 데이터 안내](https://openapi.tossinvest.com/openapi-docs/faq.md).

## Docker PostgreSQL로 전환하기

코드는 PostgreSQL 16을 지원하며 로컬 개발용 Compose 설정은 [compose.yaml](compose.yaml)입니다.
이 PC는 2026-11-15에 PostgreSQL 16.16으로 전환했습니다. 브이원텍 추가 분석까지 포함해 공시 원문
1,475건, 기본 분석 1,475건, 검색용 문서 조각 981,348개를 이전하고 테이블별 내용 해시를 검증했습니다.
실제 데이터는 `investlab-ai-local_research-data` 볼륨에 있고 기존 SQLite 파일은 원본 백업으로 보존합니다.
**GitHub에는 코드만**, 공시 원문·분석·검색 자료는 PostgreSQL의 `research-data` 이름 볼륨에 보관합니다.
컨테이너 이미지에 수집 데이터를 넣지 않으며 팀원이 이미지를 받는 것만으로 데이터가 복사되지 않습니다.
팀에서 같은 데이터를 쓰려면 팀 서버 한 곳의 DB를 함께 사용하거나 백업을 별도로 전달·복원하세요.

1. Windows에서 WSL 2와 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)을
   설치하고 Docker Desktop을 실행합니다. Windows 기능 활성화에는 관리자 권한과 재부팅이 필요할 수 있습니다.
2. 채팅 서버를 종료한 다음 **`setup-postgres.cmd`**를 한 번 실행합니다.
3. 이 도구는 앱/관리자의 서로 다른 DB 비밀번호 생성 → 컨테이너 시작 → Alembic 테이블 생성 →
   SQLite 읽기 전용 이전 → 테이블별 행 수와 내용 SHA-256 검증 순서로 처리합니다.
4. 검증이 성공한 뒤에만 `.env`의 SQLite 설정을 제거하고 Docker PostgreSQL 연결을 활성화합니다.
   기존 `.local/research.db`와 `.local/backups/env-before-postgres.env`는 보관합니다.
   진행·검증 결과는 `.local/postgres-transfer.json`에 기록하며 DART/GPT를 재호출하지 않습니다.
5. 이후 **`start-chat.cmd`**로 PostgreSQL 컨테이너와 채팅을 함께 켭니다. Ctrl+C로 채팅을 종료하면
   도우미가 이번 실행에서 켠 DB 컨테이너도 종료합니다. 이미 켜져 있던 DB는 그대로 둡니다.
   DB만 별도로 끄려면 채팅 종료 후 `stop-database.cmd`를 실행합니다.

기본 PostgreSQL 접속은 `127.0.0.1:5432`, DB/앱 사용자 이름은 `investlab_research`입니다.
비밀번호는 `.env`의 `RESEARCH_DB_PASSWORD`이고 생성된 관리자 비밀번호는 앱 접속에 사용하지 않습니다.
현재 Compose는 로컬 개발용으로 loopback에만 바인딩하고 재부팅 시 자동 시작하지 않습니다.
원격 팀 서버는 그 서버의 인증·네트워크·백업 정책에 맞춰 별도로 구성하세요.

`backup-postgres.cmd`는 실행 중인 DB를 `.local/backups/*.dump`로 백업합니다. 컨테이너 종료·일반적인
`docker compose down`은 볼륨을 보존하지만 **`docker compose down -v`는 저장 데이터를 삭제**하므로
사용하지 마세요. 볼륨도 호스트 디스크에 있으므로 중요한 백업은 다른 저장장치에도 보관하세요.
백업 복원 절차와 팀 DB 연결은 [migrations/README.md](migrations/README.md)를 참고하세요.

## 데이터를 포함한 Docker 이미지 배포

팀원이 소스 코드나 Python 없이 기존 기본 분석을 쓸 수 있는 이미지 배포를 지원합니다.
프로젝트에서 아래 명령을 실행하면 앱 이미지와 데이터 포함 PostgreSQL 이미지를 만들고,
프로젝트 밖 `../investlab-docker-bundle/`에 전달용 파일을 생성합니다.

```powershell
.\.venv\Scripts\python.exe scripts/build_distribution.py --tag 20261115
```

배포 폴더에는 두 이미지를 묶은 `images.tar.gz`, SHA-256 검증 파일, Compose 설정, 이미지/데이터 기준
메타데이터와 `start.cmd`가 있습니다. 이 폴더 전체를 팀원에게 전달하면 됩니다.
받는 사람은 Docker Desktop 실행 → `start.cmd` 더블클릭으로 이미지 로드·빈 볼륨에 DB 복원·채팅 시작을
진행합니다. 최초 복원만 시간이 걸리고, 다음 실행에서는 본인의 기존 볼륨을 재사용합니다.
종료는 `stop.cmd`입니다. macOS/Linux에서는 `sh start.sh`를 사용할 수 있으며 배포 플랫폼은 linux/amd64입니다.

API 키가 없어도 이미지에 저장된 기업의 기본 분석을 열람할 수 있습니다. 이때 ‘최신’은 저장된 스냅샷 중
최신입니다. 새로운 공시 수집에는 DART 키, 추가 GPT 질문에는 OpenAI 키가 필요하며 생성된 `.env`에 본인의
키를 입력합니다. 기본 분석 캐시는 모델·토큰 예산을 포함하므로 배포본의 모델 ID/예산 설정을 유지하세요.

시드는 PostgreSQL exported snapshot으로 일관된 데이터를 내보내고, 최초 빈 볼륨 초기화 시 해시를 검증한 뒤
단일 트랜잭션으로 복원합니다. API 키/비밀번호, 개인 대화(`rag_chat_tasks`), 임대 잠금, 수집 작업 이력 데이터는
이미지에 포함하지 않습니다. 기존 볼륨은 자동으로 덮어쓰지 않습니다. 각 팀원의 새 분석은 각자의 DB에만 저장됩니다.

이미지 아카이브/DB 백업/.env는 Git에 넣지 않습니다. 빌드 스크립트는 외부 레지스트리에 업로드하지 않습니다.
Docker Hub/GHCR로 배포하려면 생성된 두 이미지를 본인의 저장소 이름으로 태그·게시하고, 수신자 설정의 이미지
이름을 해당 주소로 바꿉니다. 이미지가 게시된 경우 아카이브 없이 `start.cmd`가 이미지를 pull할 수 있습니다.

## VS Code에서 시작하기 (Windows)

이 폴더 전체를 VS Code에서 열고 터미널에서 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Python 3.12가 필요합니다. 의존성은 `.venv`에, uv가 없으면 도구는 `.local/tooling`에 설치합니다.
기존 `.env`와 개인 VS Code 설정은 덮어쓰지 않습니다. 생성된 `.env`에서 아래 세 값을 입력하세요.
키는 채팅, 스크린샷, Git에 공유하지 않습니다.

```dotenv
INVESTLAB_AI_DART_API_KEY=본인_DART_API_키
INVESTLAB_AI_OPENAI_API_KEY=본인_OpenAI_API_키
INVESTLAB_AI_OPENAI_MODEL=본인_API_프로젝트에서_사용할_모델_ID
```

모델은 Responses API와 Structured Outputs를 지원해야 합니다. 특정 유료 모델을 기본값으로 강제하지
않습니다. 임베딩은 `text-embedding-3-small`로 시작하며 OpenAI 사용량에 포함됩니다.

이 PC에서는 기존 서버를 종료한 뒤 **F5 → InvestLab AI: Neon shared server**를 선택하면 공용 DB로 실행합니다(8002).
기존 `InvestLab AI: local server` 구성은 로컬 DB 개발용으로 유지했습니다. Python/Python Debugger 확장이 필요합니다.
디버거 없이 공용 DB를 사용하려면 `start-chat.cmd`를 실행합니다.
아래 명령과 `Tasks: Run Task → InvestLab: start server`는 기존 로컬 DB 개발용입니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1
```

서버: http://127.0.0.1:8000, 로컬 API 문서: http://127.0.0.1:8000/docs.
문서의 Authorize에는 `.env`에 생성된 서비스 토큰만 입력합니다. OpenAI 키를 넣는 칸이 아닙니다.
서비스 토큰을 포함한 내부 API는 프론트에 직접 공개하지 않고 백엔드에서 호출합니다.
`Settings`는 `.env`를 자동으로 읽지 않습니다. 개발 실행 스크립트/디버거의 명시적 `--env-file`로 읽습니다.

## 첫 공시 분석 순서

서버를 실행한 상태에서 새 터미널을 열고 실행하세요. 아래 회사 코드는 삼성전자 예시입니다.
접수번호는 목록 응답의 실제 `rceptNo`로 바꿉니다. 날짜는 **회계연도가 아닌 공시 접수일 범위**입니다.

```powershell
# 비밀값 없이 설정 여부 확인
.\.venv\Scripts\python.exe -m investlab_ai.cli doctor
# 기업 목록은 최초 한 번 내려받아 DB에 저장
.\.venv\Scripts\python.exe -m investlab_ai.cli sync-companies
.\.venv\Scripts\python.exe -m investlab_ai.cli companies "삼성전자"
.\.venv\Scripts\python.exe -m investlab_ai.cli disclosures 00126380 2026-01-01 2026-11-14

# 아래의 '접수번호'를 위 응답의 14자리 번호로 교체
.\.venv\Scripts\python.exe -m investlab_ai.cli prepare 접수번호
.\.venv\Scripts\python.exe -m investlab_ai.cli ask 접수번호 "매출 변화와 공시에 적힌 원인을 설명해줘"
.\.venv\Scripts\python.exe -m investlab_ai.cli plan 접수번호
.\.venv\Scripts\python.exe -m investlab_ai.cli analyze 접수번호 --max-calls 10
```

`prepare`는 DART 접수번호로 메타데이터를 검증하고 ZIP 원문을 받습니다. 목차·본문·표를 분리하고
임베딩을 만든 뒤 원본·SHA-256·수집시각·문서 조각·벡터를 하나의 트랜잭션으로 저장합니다.
같은 접수번호를 다시 준비하면 기존 임베딩을 재사용합니다. 별도 첨부 PDF/이미지는 처리하지 않습니다.
토크나이저 최초 사용 시 공식 인코딩 파일을 다운로드하고 `.local/tiktoken`에 캐시합니다.

`ask`는 선택한 접수번호의 문서 조각만 대상으로 코사인 유사도+키워드 순위를 결합해 검색합니다.
찾은 원문과 질문을 GPT에 보내고 Structured Outputs로 주장을 받습니다. 서버는 각 주장의 인용 ID가
실제로 전달한 원문에 속하는지 검사합니다. 응답의 `sources`에는 원문 텍스트, 목차, DART URL이 있습니다.
인용 ID 검증은 의미/수치의 진실성을 증명하지 않으므로 실제 원문과 평가 질문으로 검증해야 합니다.

`analyze`는 검색 상위 몇 개만 읽지 않고 **저장된 모든 문서 조각을 순서대로** 나눠 읽습니다.
부분별 핵심·사업·실적·위험·확인사항을 합쳐 반환합니다. 문서 전체에 대한 단일 종합 요약/순위는 아직
생성하지 않습니다. `plan`은 예상 GPT 호출 수와 원문 토큰 수를 외부 API 호출 없이 보여줍니다.
요청 `maxModelCalls`(기본 10)와 서버 `INVESTLAB_AI_MAX_ANALYSIS_CALLS`(기본 40) 중 작은 값을 넘으면
유료 호출 전에 `ANALYSIS_CALL_BUDGET_EXCEEDED`로 거부하며 문서를 조용히 잘라내지 않습니다.
필요한 호출 수·비용을 확인한 뒤 한도를 조정하세요. plan은 출력/지침 토큰 및 정확한 가격 견적은 아닙니다.

## 내부 API 계약

SEC 경로도 기존과 같은 서비스 Bearer 인증을 사용하며 [contracts/internal-ai-api.yaml](contracts/internal-ai-api.yaml)에 포함합니다.
백엔드는 기업 검색 → 보고서 선택 → 원문 준비 → 기본 분석 또는 추가 질문 순서로 호출할 수 있습니다.

| 메서드 | 경로 | 용도 |
| --- | --- | --- |
| GET | `/internal/v1/overseas-companies?query=AAPL` | 공개 SEC 기업 ID·CIK·티커 검색 |
| GET | `/internal/v1/overseas-companies/{cik}/disclosures` | 최근 3년 정기보고서·정정본 목록 |
| POST | `/internal/v1/sec-disclosures/{accession}/prepare?cik={cik}` | SEC 확인 메타데이터로 원문·검색 청크 보관, GPT 호출 없음 |
| POST | `/internal/v1/sec-disclosures/{accession}/basic-analysis` | 저장된 기본 분석 재사용 또는 최초 1회 생성 |
| POST | `/internal/v1/sec-disclosures/{accession}/questions` | `{"question":"매출 증가 원인은?"}` 원문 검색 답변 |

분석·질문 API는 준비되지 않은 원문을 자동 다운로드하거나 선행 유료 분석하지 않고 409를 반환합니다.
SEC 식별자는 CIK 10자리와 accession `0000320193-25-000079` 형식이며 기존 DART 경로와 구분합니다.
원문 URL은 클라이언트 입력을 받지 않고 SEC 목록에서 검증합니다. 목록의 날짜 범위는 최근 3년 이내만 허용합니다.

계약 원본: [contracts/internal-ai-api.yaml](contracts/internal-ai-api.yaml).
JSON 필드는 camelCase이고 날짜는 YYYY-MM-DD입니다. `/internal/v1/*` 경로는 서비스 Bearer 인증이 필요합니다.
로컬 채팅은 별도의 쿠키 인증을 사용합니다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/health` | 프로세스 liveness만 확인 |
| POST | `/internal/v1/companies/sync` | DART 회사 목록 동기화 |
| GET | `/internal/v1/companies?query=삼성전자` | 회사 검색 |
| GET | `/internal/v1/disclosures?corpCode=00126380&startDate=2026-01-01&endDate=2026-11-14&page=1` | 정기 공시 목록 |
| POST | `/internal/v1/disclosures/{rcept_no}/prepare` | 원문·임베딩 준비 (동기 처리) |
| GET | `/internal/v1/disclosures/{rcept_no}` | 준비된 문서 조회 |
| GET | `/internal/v1/disclosures/{rcept_no}/analysis-plan` | 전체 분석 호출 수 조회 |
| POST | `/internal/v1/disclosures/{rcept_no}/analyze` | `{"maxModelCalls":10}`로 전체 부분별 분석 |
| POST | `/internal/v1/questions` | `{"rceptNo":"14자리번호","question":"질문"}` |

기존 `question` 필드는 유지했습니다. `rceptNo`는 스키마에서 선택이며, AI 설정 후 공시 선택이 없으면
422 `DISCLOSURE_SELECTION_REQUIRED`로 반환합니다. AI 미설정 상태의 기존 503 `AI_NOT_CONFIGURED`는 유지합니다.

- 근거 부족: HTTP 200, `status=insufficient_evidence`, 빈 `claims`/`sources`.
- 준비 안 됨: 409 `DOCUMENT_NOT_PREPARED`; 키/모델 미설정: 503 `AI_NOT_CONFIGURED` 또는 `DART_NOT_CONFIGURED`.
- 호출 제한: 429 `DART_RATE_LIMITED` / `OPENAI_RATE_LIMITED`.
- 외부 장애/잘못된 인용: 502, 제한시간 초과: 504. 공급자의 원문 오류·키를 응답에 노출하지 않습니다.
- 정정본은 별도 접수번호로 보관합니다. 목록은 기본적으로 정정본도 포함하며 `finalOnly=true`를 명시할 수 있습니다.

전체 설계·공개 API 기준은 [backend 협업 규약](https://github.com/investlab-team/investlab-backend/blob/dev/docs/architecture/repository-contracts.md)입니다.
백엔드 담당자와 계약·타임아웃·마이그레이션을 검토한 뒤 공개 백엔드 API와 연동하세요.
공용 DB의 거래 스키마는 별도 backend 저장소의 Flyway가 관리합니다.

## DB와 현재 범위

### 주가·전망 질문 (토스 직접 연결)

백엔드 시세 API가 완성되기 전에도 AI 서버가 토스 공식 Open API를 직접 조회합니다.
`삼성전자 주가 오를까?`, `SK하이닉스 현재 주가 얼마야?`, `삼성전자 차트 분석해줘`처럼 질문하세요.
공시 요약·공시 후속 질문은 기존 경로를 유지하며 주가 질문을 기본 공시 요약으로 바꾸지 않습니다.

개인 설정은 `.env.toss.example`을 `.env.toss`로 복사해 발급받은 Client ID/Secret 전체 값을 입력합니다.
별표로 가려진 값은 사용할 수 없습니다. `.env.toss`는 Python 실행 시 자동으로 읽으며 Git/이미지에서 제외합니다.
`check-toss.cmd`는 키·시세 원문을 출력하지 않고 인증·현재가·완료 일봉·계산 지표를 확인합니다.
주문·계좌 조회를 호출하지 않습니다. IP 거부(403)는 토스 Open API의 허용 IP 설정을 확인하세요.

처리 순서는 질문 의도 구분 → 현재가/수정주가 일봉 → Python 지표 계산 → 저장된 공시 근거 → GPT 조건부 전망입니다.
현재가는 GPT 없이 표시합니다. 전망은 시세 시각·기간·완료 거래일 기준을 보여주고,
20/60일 이동평균·20/60거래일 수익률·Wilder RSI(14)·연환산 변동성·이전 20일 대비 거래량을 계산합니다.
진행 중인 당일 일봉은 지표에서 제외합니다. 최근 약 1년(최대 260 완료 거래일)을 대상으로 하며 데이터가 부족하면 안내합니다.
뉴스·기관 수급·시장/업종 지수는 연결하지 않았고 공시는 저장된 최근 기본 분석의 원문 발췌를 재사용합니다.
기술적 흐름과 공시를 근거로 상승/하락 조건을 설명하며, 학습·검증된 확률 예측 모델이나 목표가 산출기는 아닙니다.

현재 공식 문서의 한도는 현재가 그룹 15회/초, 캔들 그룹 20회/초, 인증 5회/초이며 운영 중 변경될 수 있습니다.
클라이언트·API 그룹별 한도와 응답의 `X-RateLimit-*`, `Retry-After`가 기준입니다.
이 앱은 PC에서 같은 키의 프로세스들이 파일 잠금을 공유하고 기본 5회/초로 보수적으로 제한합니다.
429는 제한된 횟수만 재시도하고 긴 대기 요청은 안내하며, 실패 시 오래된 시세를 최신 시세로 반환하지 않습니다.
현재가는 15초, 일봉은 5분 재사용하고, 같은 질문·소유자·모델·자료의 전망도 재사용합니다. 시세/자료가 바뀌면 캐시 키가 바뀝니다.

토스는 client당 유효 토큰이 하나라 새 토큰 발급 시 이전 토큰이 무효화됩니다. 이 PC의 프로세스들은 토큰을 공유하며,
다른 PC/향후 백엔드와 같은 키로 각자 토큰을 발급하면 충돌할 수 있습니다. 백엔드 시세 연결 후 토큰 발급을 한곳으로 통합하세요.
토스 공식 이용 안내의 제3자 배포 제한 때문에 시세·캔들·전망 결과·토큰은 `.local/private` 개인 저장소에 보관합니다.
공용 Neon에는 시장 시세/전망 본문을 넣지 않으며 공시 원문·기본 분석은 기존 공용 저장소를 계속 사용합니다.
시장 질문 작업은 공용 DB에 상태 안내만 남기고 본문은 생성한 PC에서 7일간 보관합니다.
팀 공개 서비스에 토스 데이터를 제공하기 전에는 이용 허용 범위를 별도로 확인해야 합니다.

Docker는 개인 캐시용 `ai-private-cache` 볼륨을 사용합니다. `down`은 캐시를 유지하고 `down -v`는 이를 삭제합니다.
Python/Docker를 같은 키로 동시에 운영하기보다는 한 실행 방식을 사용하세요. 각각 다른 저장소에서 토큰을 재발급할 수 있습니다.

기준: [토스 공식 API 개요](https://openapi.tossinvest.com/openapi-docs/overview.md),
[시세 명세](https://developers.tossinvest.com/docs/market-data),
[토큰·데이터 이용 FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md).

### 공용 Neon 실행

`start-chat.cmd`를 실행하면 `.env.neon`이 있는 경우 공용 Neon을 사용하는 채팅 화면이 열립니다(로컬 8002).
기존 Docker PostgreSQL을 사용하는 실행은 `start-local-chat.cmd`로 남겨 두었습니다.
Docker 방식은 `start-neon-docker.cmd`이며 8000 포트를 사용합니다.
앱을 켜야 채팅 화면을 사용할 수 있고, PC를 꺼도 Neon에 저장된 자료는 남아 있습니다.

- `investlab_core`: 팀 백엔드 소유. 30개 회원·거래·AI 답변 테이블, Flyway V1.
- `investlab_research`: AI 소유. ERD의 8개 공시·기업·재무·RAG 테이블과 현재 AI 구현의 `rag_*` 테이블, Alembic.
- 비공개 `disclosures` Object Storage: 공시 ZIP과 보고서별 압축 검색 청크·벡터. DB에는 파일 키·해시·분석 결과를 저장합니다.
- 회사·공시 메타데이터는 같은 트랜잭션의 트리거로 ERD `companies`/`disclosure_documents`에도 반영합니다.
- 원문·청크는 SHA-256으로 검증합니다. 새 공시 저장·질문용 재색인도 이 공용 파일 저장소를 사용합니다.
- `financial_*`, `ingestion_jobs`, 관계형 `document_chunks`/`embedding_records`는 ERD 스키마를 준비한 상태입니다.
  현재 검색은 `rag_evidence_manifest`의 파일 키를 따라 압축 자료를 읽습니다. 별도의 재무 정규화 수집 기능은 아직 없습니다.

Neon CLI가 관리하는 `.env.local`에는 관리자 연결과 S3 설정이 있고,
앱용 `.env.neon`에는 AI 전용 pooled/direct URL만 있습니다. Python 실행은 관리자 DB 연결을 앱에 주입하지 않습니다.
기존 `.env`의 DART/OpenAI 설정은 유지하며 이 파일들은 모두 Git에서 제외합니다.
앱은 pooled URL, Alembic은 direct URL을 사용합니다. 팀원 설정은 [docker/shared/README.md](docker/shared/README.md)를 따르세요.

초기 DB 준비 도구는 `scripts/setup_neon_databases.py`, 읽기 전용 원본 이전 도구는 `scripts/migrate_to_neon.py`입니다.
이전 도구는 GPT를 호출하지 않고 업로드 파일을 다시 받아 검증하며 동일한 대상만 재사용합니다.
원본 로컬 DB와 백업은 남겨 둡니다. 스키마 적용·기존 데이터 이전은 DB 담당자가 한 번 수행하며 팀원은 매번 복원하지 않습니다.
회사 목록을 통째로 가져오는 검색과 유휴 상태 조회를 줄여 무료 데이터 전송·절전 한도를 아낍니다.

Neon Free는 무제한이 아닙니다. DB/파일/전송/컴퓨트 사용량을 Neon Console에서 확인하세요.
유료 플랜 전환은 수행하지 않았습니다. 공시·벡터·분석 결과·환경 파일은 Git 또는 앱 이미지에 포함하지 않습니다.

팀 연동 DB는 PostgreSQL 16입니다. `.env.example`의 명시적인 `INVESTLAB_AI_LOCAL_SQLITE_PATH`는
DB 설치 없이 개발하는 용도이며 PostgreSQL로 전환할 때 **환경변수 자체를 제거**해야 합니다.
PostgreSQL 마이그레이션은 [migrations/README.md](migrations/README.md)를 따릅니다.
로컬 DB는 원문·벡터를 `rag_*`에 보관하며 Neon 프로필은 대용량 자료를 비공개 파일로 분리합니다.
해당 공시 하나의 청크·벡터만 메모리에 읽습니다. pgvector/Redis 선택은 아직 강제하지 않습니다.

현재 로컬 개발용입니다. 내부 prepare/analyze는 동기 요청이고 채팅은 작업 ID와 진행 상태를 반환합니다.
기본 분석에는 DB 임대 잠금을 적용하고 분석 결과를 캐시합니다. 이전 대화는 브라우저에 질문·작업 ID를
보관하며 작업 결과는 브라우저 소유자별로 DB에서 조회합니다. 서버 재시작으로 중단된 질문은 재전송을 안내합니다.
운영용 분산 작업 큐, 사용자별 호출 한도, 답변 토큰 스트리밍, 공개 백엔드 인증 연동은 후속 범위입니다.
유료 POST와 OpenAI SDK의 자동 재시도를 껐으며 부분 분석 중 실패한 호출 비용은 되돌릴 수 없습니다.
서버 제한시간과 백엔드 제한시간은 실제 문서 측정 후 맞춰야 합니다.

재무 수치 계산 도구, 연결/별도 자동 정규화, OCR, 전체 시장 수집, 퀀트 점수/매매 신호는 구현하지 않았습니다.
표는 제목·주변 단위·헤더와 span 정보를 보존하지만 복잡한 다단 헤더를 회계 데이터로 정규화하지 않습니다.
과거 공개시각을 보장하지 않으므로 현재 DB를 그대로 point-in-time 백테스트 자료로 사용하면 안 됩니다.

## 검사와 GitHub 반영

```powershell
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest
# 계약 갱신: uv run --locked python scripts/export_contract.py
```

테스트는 합성 공시와 모의 DART/OpenAI 응답으로 파싱·검색 범위·인용·오류·계약·저장을 검증하며
실제 API 크레딧을 사용하지 않습니다. 별도로 실제 DART 원문 수집과 GPT 분석·저장·채팅 흐름을 검증했습니다.
로컬 Docker PostgreSQL 16.16에서 실제 데이터 이전·전체 캐시 재사용·기업 검색·채팅 응답·중복 생성 잠금과
마이그레이션 일치를 검증했습니다. 검색 경로는 실제 DB와 모의 AI 공급자로 확인했으며 이전 검증 중 GPT를 재호출하지 않았습니다.
`.env`, `.venv`, `.local`, 개인 `.vscode`는 Git에서 제외합니다.

기본 통합 브랜치는 `dev`입니다.
팀 규약에 따라 GitHub 이슈를 등록한 다음 실제 번호로 `feat/이슈번호-dart-rag`로 브랜치를 바꾸고
커밋 본문에 `Refs #번호`, dev 대상 PR에 해당 이슈를 연결하세요. dev/main 직접 push는 금지입니다.
규약과 템플릿은 [CONTRIBUTING.md](CONTRIBUTING.md)를 따릅니다.
