# 공용 DB에 연결하는 AI 클라이언트

AI 서버 컨테이너만 실행하며 DB 컨테이너·새 데이터 볼륨·복원을 만들지 않습니다.
공용 `investlab_research` DB의 공시·기본 분석과 같은 Neon 프로젝트의 비공개 `disclosures` 파일 저장소를 사용합니다.
새 공시·기본 분석도 이곳에 저장하므로 같은 모델·분석 버전의 팀원이 기존 결과를 재사용합니다.

이 PC에서는 루트의 `start-neon-docker.cmd`를 실행합니다. Python 방식은 `start-chat.cmd`입니다.
Docker는 로컬 8000, Python Neon은 8002 포트를 사용하며 둘 중 하나만 실행하면 됩니다.

## 팀원 빠른 실행

1. Docker Desktop을 켜고 이 PR의 브랜치 또는 병합 후 최신 `dev`를 내려받습니다.
2. Windows에서는 루트의 **`setup-shared-client.cmd`**를 실행합니다. `.env.team`을 만들고 개인 서비스 토큰·인스턴스 ID를 자동 생성합니다.
   기존 값은 보존하며 비밀값을 화면에 출력하지 않습니다. Python 설치는 필요하지 않습니다.
   macOS/Linux에서는 `cp docker/shared/.env.example .env.team` 후 개인별 32자 이상 난수 토큰과 고유 INSTANCE_ID를 직접 설정합니다.
3. 담당자에게 아래 공유 연결 설정을 **비공개로** 전달받아 자신의 `.env.team`에 입력합니다.
   GitHub 이슈·PR·스크린샷에는 값을 올리지 마세요. 관리자 `.env.local` 전체를 전달받지 않습니다.
4. 아래 명령으로 자신의 AI 서버를 실행한 뒤 `http://127.0.0.1:8000`을 엽니다.

| 설정 이름 | 용도 |
| --- | --- |
| `RESEARCH_DB_URL` | `investlab_research` 전용 앱 계정의 pooled 연결 URL(TLS 포함) |
| `INVESTLAB_AI_OBJECT_BUCKET` | 공시 원문·검색 자료 버킷, 기본값 `disclosures` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | 담당자가 팀 사용 범위를 확인한 스토리지 런타임 자격증명 |
| `AWS_ENDPOINT_URL_S3`, `AWS_REGION` | 같은 공유 파일 저장소의 접속 위치 |

팀원의 일반 실행에는 direct/관리자 DB URL이 필요하지 않습니다. 각자 Neon 프로젝트를 새로 만들거나 DB를 복원할 필요도 없습니다.
Neon 콘솔 멤버 초대·접근 권한과 런타임 설정 전달은 담당자가 별도로 처리합니다. 이 저장소는 접속 권한을 공개하지 않습니다.

저장된 기본 분석만 읽는 데는 DART/OpenAI 키가 필요하지 않습니다. 캐시 재사용을 위해 예시의 모델 ID·분석 토큰 예산은 유지하세요.
새 분석·추가 질문에는 본인의 OpenAI 키와 해당 원문 수집 설정이 필요합니다.

```powershell
docker compose --env-file .env.team -f docker/shared/compose.yaml up --build -d
# http://127.0.0.1:8000
# 실행 로그 확인(환경변수 전체를 출력하는 config 명령은 사용하지 마세요)
docker compose --env-file .env.team -f docker/shared/compose.yaml logs --tail=60 ai
docker compose --env-file .env.team -f docker/shared/compose.yaml down
```

이미지를 별도 배포하면 `INVESTLAB_APP_IMAGE`를 해당 이미지로 지정하고 `--build`를 생략할 수 있습니다.
현재 이미지는 이 PC에만 빌드했으며 레지스트리에 게시하지 않았습니다.
따라서 처음에는 위 명령의 `--build`를 유지합니다. 이미지에는 기존 공시 데이터나 접속 키를 넣지 않습니다.
스키마 변경은 DB 담당자가 direct URL로 Alembic을 실행합니다. 팀원 서버는 스키마를 자동 변경하지 않습니다.
`/health`는 DB를 조회하지 않고, 채팅 화면은 유휴 중 DB 상태를 주기적으로 조회하지 않아 무료 절전을 방해하지 않습니다.

주가 질문은 개인별 토스 Client ID/Secret을 사용합니다. `.env.team`의 토스 설정을 입력하면 직접 시세를 조회할 수 있습니다.
공용 DB는 DART 공시 자료를 공유하며 토스 시세·전망·토큰은 각 PC의 `ai-private-cache` 볼륨에만 보관합니다.
미국 기업은 SEC 정기보고서와 저장된 기본 분석도 공용 DB·파일 저장소에서 읽습니다.
새 SEC 보고서 수집에는 `.env.team`의 `INVESTLAB_AI_SEC_USER_AGENT`에 실제 연락처를 설정해야 합니다.
저장된 공시 조회만 할 때는 SEC 설정이 없어도 됩니다. DB 담당자가 `0006_sec_research`까지 먼저 적용합니다.
토스는 키당 토큰 1개만 유효하므로 같은 키를 여러 PC에서 각각 발급해 사용하지 마세요.
동일 개인 PC에서도 Python/Docker를 동시에 사용하면 토큰 저장소가 달라질 수 있습니다.
공개 팀 서비스에서 토스 데이터를 재배포하는 것은 공식 이용 안내와 별도 허용 범위를 확인해야 합니다.

## 접속 후 확인할 것

- `분석 가능한 기업은 몇 개야?`: 국내/미국의 저장된 기업·공시 수 확인.
- `삼성전자 공시 설명해줘`, `엔비디아 공시 설명해줘`: **저장된 기본 분석을 불러왔어요** 배지 확인. GPT 추가 호출 없음.
- `그럼 주요 위험요인은?`: 원문 검색 후 본인 GPT API로 추가 답변(사용료 발생).
- `AAPL 현재 주가 얼마야?`: 개인 토스 설정이 있을 때 현재가 조회. GPT 호출 없음.
- `삼성전자 주가 한 달 동안 오를까?`: 개인 토스 + OpenAI 설정으로 조건부 전망 생성.

2026-11-16 확인 데이터는 국내 102개 기업·공시/기본 분석 1,477건, 미국 101개 기업·1,013건입니다.
미국은 시총 상위 100개 기업 1,012건과 요청 시 분석을 검증한 리비안 1건을 포함합니다. 이후 사용자 요청에 따라 증가할 수 있습니다.
`/health` 200만으로 DB 연결이 검증되는 것은 아닙니다. 실제 채팅의 저장 결과 조회까지 확인하세요.
테스트 결과를 이슈에 남길 때는 OS·화면 정상 여부·기업 수·오류 코드만 공유하고, 연결 문자열·키·원본 예외 로그는 제외하세요.

## DB 담당자와 백엔드 팀 참고

`investlab_research`는 AI 공시 자료용, `investlab_core`는 백엔드 회원·계좌·주문·시장·사용자 답변용입니다.
백엔드에는 core 전용 계정을 별도로 연결합니다. AI용 URL을 백엔드 거래 DB 설정에 재사용하지 않습니다.
core의 스키마가 준비되었다고 실제 회원·거래 데이터나 백엔드 연결까지 완료된 것은 아닙니다.
이번 AI PR은 다른 저장소의 백엔드 설정 파일을 배포하지 않습니다. 백엔드 쪽 연결·엔티티 검증은 담당 팀에서 별도로 확인합니다.

공유 연구 DB는 Alembic `0006_sec_research`까지 적용된 상태입니다. 팀원은 `setup-postgres.cmd`, 초기 DB 생성,
데이터 이전·전체 사전 분석을 반복 실행하지 않습니다. 새 스키마 변경은 담당자가 테스트 브랜치 검증 후 direct URL로 한 번 적용합니다.
공용 Neon 자료는 PC를 꺼도 보존되며, 각 팀원의 채팅 서버는 자신의 PC에서 실행해야 합니다.
