# 기업분석 DB 마이그레이션

PostgreSQL 16의 investlab_research database는 AI 전용 역할로 접근합니다. SQLAlchemy 드라이버는 `postgresql+psycopg`(Psycopg 3)이며 URL.create로 환경변수를 조합해 비밀번호를 안전하게 인코딩합니다. Alembic 마이그레이션은 아래와 같이 준비되어 있습니다. 현재 거래 DB 접근·변경은 없습니다.

`0001_dart_rag`는 공시 RAG MVP 전용 `rag_companies`, `rag_documents`, `rag_chunks`를 추가합니다.
기존 기업분석/거래 테이블을 변경하지 않습니다. 팀의 전체 물리 ERD와 통합할 때 검토할 분리된
구현이며, 이 테이블을 전체 ERD v1.1 구현으로 간주하지 않습니다.

PostgreSQL 16에서 환경변수를 주입한 뒤 `uv run --locked alembic upgrade head`를 실행합니다.
운영 마이그레이션에 `INVESTLAB_AI_LOCAL_SQLITE_PATH`를 지정하지 마세요. `.env`를 직접 읽지 않으므로
프로세스 환경이나 명시적 dotenv 실행으로 주입합니다. 자동 downgrade는 실행하지 않습니다.

원본 ZIP, SHA-256, DART 접수번호, 수집시각(TIMESTAMPTZ), 공시별 벡터 모델·차원을 보존합니다.
문서·문서 조각·벡터는 한 트랜잭션으로 저장합니다. 정정본은 별도 접수번호이며 과거 출처를 덮어쓰지 않습니다.
JSON 필드는 PostgreSQL에서 JSONB, 명시적인 로컬 개발 SQLite에서는 JSON입니다. MVP는 공시 하나의
벡터만 읽어 NumPy로 정확한 코사인 검색을 수행합니다. pgvector·별도 벡터 DB는 도입하지 않았습니다.

로컬 SQLite는 개발/테스트 전용이고 서버 시작 시 테이블을 생성합니다. PostgreSQL 검증을 대체하지 않습니다.
UUID는 계약 ID에 문자열로 저장하는 MVP이므로 팀 ERD의 native UUID 제약과 후속 통합 검토가 필요합니다.

`0002_chat_and_cache`는 공시 기본 분석/전체 분석 캐시, 브라우저별 질문 작업과 시총 100개 기업의
사전 수집·분석 진행 상태를 추가합니다. 기본 분석을 위해 원문을 저장할 때는 문서 조각/임베딩이
비어 있을 수 있고, 본문 처리와 추가 질문 시 순차적으로 준비합니다.
`0003_analysis_leases`는 같은 캐시의 중복 유료 생성을 막는 10분 임대 잠금입니다.
캐시에는 답변·원문 발췌·생성시각·모델을 보존하며 기존 원문을 수정하지 않습니다.

## 로컬 Docker 이전

Docker Desktop 실행 후 프로젝트 루트의 `setup-postgres.cmd`를 실행합니다. 이 스크립트는 SQLite 원본을
읽기 전용으로 열고 기존 `rag_*` 테이블을 PostgreSQL로 복사합니다. `rag_analysis_leases`는 이전하지 않습니다.
테이블마다 PK 순서로 전체 행을 정규화해 SHA-256을 계산하고 대상의 행 수·해시와 비교합니다.
원문 ZIP은 내용 해시로, 날짜는 UTC로, JSON은 키 순서와 무관하게 비교합니다. 메모리에는 일부 행만 유지합니다.
이미 같은 내용이 있는 테이블은 확인 후 건너뛰고, 서로 다른 기존 데이터가 있으면 덮어쓰지 않고 중단합니다.
실패한 테이블은 롤백하고 성공한 테이블은 다음 실행에서 검증 후 재사용합니다.

성공 전에는 SQLite 모드를 유지하며 성공 후에만 `.env`를 PostgreSQL로 전환합니다.
원본 `.local/research.db`는 삭제하지 않으며 이전 과정에서 GPT 비용은 발생하지 않습니다.
일반 앱 접속 역할은 SUPERUSER/CREATEDB/CREATEROLE 권한 없이 생성됩니다.

## 백업과 복원

실행 중인 로컬 컨테이너는 `backup-postgres.cmd`로 custom-format `pg_dump` 백업을 만듭니다.
새 팀 DB로 복원하려면 해당 DB 관리자가 빈 데이터베이스를 준비한 뒤 아래처럼 실행합니다.
비밀번호는 `pg_restore`가 묻는 입력창 또는 별도 비밀 관리로 제공하며 명령에 직접 적지 마세요.

```powershell
pg_restore --host 팀호스트 --port 5432 --username 팀사용자 --dbname 빈DB이름 --no-owner --no-acl --exit-on-error .local/backups/실제백업.dump
```

백업은 앱 테이블과 Alembic 버전을 포함하며 DB 사용자/비밀번호는 별도 관리합니다.
복원 후 `.env`의 `RESEARCH_DB_*`를 팀 DB 정보로 바꾸고 `INVESTLAB_AI_USE_DOCKER_DB=false`로 설정합니다.
`INVESTLAB_AI_LOCAL_SQLITE_PATH`는 제거하고, 로컬 채팅을 쓸 때 `INVESTLAB_AI_ENABLE_LOCAL_CHAT=true`를 둡니다.
## Neon 공용 연구 DB

`0004_evidence_objects`는 원문/청크 파일 키와 선택적 외부 원문 저장을 추가합니다.
`0005_research_contract`는 팀 ERD v1.1의 연구 테이블 8개와 기존 RAG 메타데이터 동기화 트리거를 추가합니다.
초기 회사·공시 레코드도 반영하며 재무·관계형 청크/임베딩 테이블은 후속 기능의 스키마입니다.
이미 있는 `rag_*`의 분석 캐시 키/모델/파서 버전은 변경하지 않습니다.
관리자는 `.env.neon`의 `RESEARCH_DB_DIRECT_URL`로 Alembic을 실행합니다. 앱 pooled URL로 마이그레이션하지 않습니다.
정식 DB 변경 전에 임시 Neon 브랜치에서 검증합니다. DB 간 FK는 만들지 않고 안정적인 외부 ID를 사용합니다.
`companies.id`는 `investlab_company_id(corp_code)`가 만드는 고정 UUID이며 `disclosure_documents.id`는 기존 문서 UUID입니다.
공시 공개시각은 DART 목록의 날짜만 있으므로 KST 00:00을 저장한 날짜 단위 값입니다. 실제 공개시각으로 백테스트하면 안 됩니다.
기존 정정 접수번호는 별도 문서로 유지하고 정정 전 문서를 확정할 수 없는 경우 `supersedes_id`는 NULL로 둡니다.
파일 저장 자료가 있는 동안 0004를 되돌리려 하면 거부합니다. 원문을 먼저 DB에 복원해야 합니다.
0005의 하향 변경은 데이터 보존 설계를 포함한 별도 마이그레이션을 작성해야 합니다.

`0006_sec_research`는 SEC용 `rag_sec_issuers`, `rag_sec_filings`, `rag_sec_analyses`,
`rag_sec_runs`, `rag_sec_items` 5개 테이블을 추가합니다. 접수번호(accession)·CIK·UTC 수집시각,
원문 ZIP/검색 청크 키와 해시·모델별 기본 분석·재개 가능한 작업 상태를 저장합니다.
기존 DART/ERD 데이터는 변경하지 않으며 SEC 기업은 DART 전용 ERD 트리거에 넣지 않습니다.
2026-11-16 임시 Neon 복제 브랜치에서 기존 DART 1,477건 보존과 새 테이블을 확인한 뒤 공용 연구 DB에 적용했습니다.
하향 변경은 보관 데이터 손실을 방지하기 위해 별도 데이터 보존 마이그레이션을 요구합니다.
