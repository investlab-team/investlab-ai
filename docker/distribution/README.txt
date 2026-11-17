InvestLab - 기존 공시·분석 데이터가 포함된 Docker 배포본

Windows에서 바로 사용하기
1. Docker Desktop을 설치하고 실행하세요.
2. 이 폴더 전체를 받으세요. images.tar.gz와 images.sha256을 함께 받아야 합니다.
3. start.cmd를 더블클릭하세요. Python 설치나 소스 코드가 없어도 됩니다.
4. 이미지 불러오기와 최초 데이터 복원이 끝나면 채팅 화면이 자동으로 열립니다.
5. '삼성전자 최신 공시 설명해줘', '브이원텍 공시 설명해줘'라고 질문하세요.
6. 종료는 stop.cmd입니다. 컨테이너를 꺼도 저장된 데이터는 유지됩니다.

기존 기본 분석 열람에는 API 키가 필요하지 않습니다. 데이터 기준일·포함 건수는 images.json에 있습니다.
새 공시 수집이나 GPT 추가 질문을 하려면 첫 실행으로 생성된 .env에 본인의 DART/OpenAI 키를 넣고
docker compose up -d --force-recreate ai 를 실행하세요. 저장된 분석을 재사용하려면 모델 ID는 유지하세요.
API 키가 없을 때 '최신'은 배포본에 저장된 공시 중 최신을 뜻합니다.

각 사람은 독립된 DB 복사본을 사용합니다. 이후 생성된 분석은 다른 사람에게 자동 공유되지 않습니다.
업데이트된 이미지를 받아도 기존 DB를 덮어쓰지 않습니다. docker compose down -v 는 데이터를 삭제합니다.
포트가 겹치면 .env의 INVESTLAB_CHAT_PORT 값을 변경하세요. API 키와 비밀번호가 있는 .env는 공유하지 마세요.
이 배포본은 Docker Hub/GHCR에 게시되지 않은 이미지 파일 전달 방식입니다.

--- English instructions / publisher notes ---

Windows quick start
1. Install and start Docker Desktop (Linux containers / WSL 2).
2. Receive this ENTIRE folder, including images.tar.gz and images.sha256.
3. Double-click start.cmd. Python, VS Code, API keys and the source checkout are not required
   to read the packaged basic analyses. Image loading and the first database restore take time.
4. The browser opens when the database restore and AI server are ready.
5. Ask: 삼성전자 최신 공시 설명해줘 / 브이원텍 공시 설명해줘
6. Double-click stop.cmd to stop. The database volume remains.

macOS / Linux: run sh start.sh. Images target linux/amd64; ARM hosts need emulation.
Manual image loading: docker load --input images.tar.gz
Manual startup after preparing .env: docker compose up -d --wait --wait-timeout 900

Optional new analysis / follow-up questions
start.cmd creates a private .env on the first run, with new database passwords and a service token.
Add your own INVESTLAB_AI_DART_API_KEY and INVESTLAB_AI_OPENAI_API_KEY there, then run:
  docker compose up -d --force-recreate ai
Keep the packaged model ID / basic-analysis token budget to reuse the existing cached analyses.
The packaged snapshot's date/count/model information is in images.json.
Without a DART key, 'latest' means the newest report in the packaged snapshot.
New public filings require a DART key. Additional GPT questions require an OpenAI API key.

Data persistence
Each recipient has an independent PostgreSQL database, restored into the named volume on FIRST run.
New analyses are not synchronized with other recipients. Image updates do not overwrite an existing
database volume. Do NOT use 'docker compose down -v' unless you intend to delete that local data.
If port 8000 is in use, edit INVESTLAB_CHAT_PORT in .env before starting.

Privacy / distribution
Images contain company filings, cached basic analyses, document chunks and company catalog only.
API keys, passwords, browser conversations and collection-task history are not packaged.
Keep .env private. Send this folder through your file-transfer service; do not commit the large
image archive or database dump to Git. No registry upload is performed by the build script.

Registry distribution (publisher only)
You may tag and push the two images listed in images.json to your Docker Hub/GHCR repository.
Change the image names in images.json and .env.example to those published names, then recipients
can run start.cmd without images.tar.gz. This release has not been pushed to any external registry.
