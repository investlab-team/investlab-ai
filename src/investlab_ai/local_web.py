import hashlib
import hmac
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from investlab_ai.agent.chat import ChatService
from investlab_ai.schemas import ChatRequest

COOKIE = "investlab_local_chat"


def local_routes(chat: ChatService, token: str) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    assets = Path(__file__).parent / "web"

    def signature(value: str) -> str:
        return hmac.new(token.encode(), value.encode(), hashlib.sha256).hexdigest()

    def valid_cookie(value: str) -> bool:
        identity, _, digest = value.partition(".")
        return bool(identity and digest and hmac.compare_digest(signature(identity), digest))

    def local_host(request: Request):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise HTTPException(403, detail="LOCAL_CHAT_ONLY")

    def owner(request: Request, writing: bool = False) -> str:
        local_host(request)
        cookie = request.cookies.get(COOKIE, "")
        if not valid_cookie(cookie):
            raise HTTPException(401, detail="OPEN_LOCAL_CHAT_FIRST")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, detail="INVALID_LOCAL_ORIGIN")
        if writing and request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, detail="INVALID_LOCAL_ORIGIN")
        return hashlib.sha256(cookie.encode()).hexdigest()

    @router.get("/")
    def home(request: Request):
        local_host(request)
        response = FileResponse(assets / "index.html")
        cookie = request.cookies.get(COOKIE, "")
        if not valid_cookie(cookie):
            identity = secrets.token_urlsafe(32)
            cookie = identity + "." + signature(identity)
            response.set_cookie(COOKIE, cookie, httponly=True, samesite="strict", max_age=2592000)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/chat-assets/{filename}")
    def asset(filename: str):
        if filename not in {"chat.css", "chat.js"}:
            raise HTTPException(404)
        return FileResponse(assets / filename)

    @router.post("/chat/api/messages", status_code=202)
    def send(request: Request, body: ChatRequest):
        task_id = chat.submit(owner(request, writing=True), body)
        return {"taskId": task_id}

    @router.get("/chat/api/tasks/{task_id}")
    def task(request: Request, task_id: str):
        result = chat.research.repo.task(task_id, owner(request))
        if result is None:
            raise HTTPException(404, detail="CHAT_TASK_NOT_FOUND")
        return JSONResponse(result)

    @router.get("/chat/api/status")
    def status(request: Request):
        owner(request)
        run = chat.research.repo.latest_bulk()
        basic = chat.research.repo.basic_analysis_counts()
        overseas = chat.overseas.sec.repo.counts()
        sec_run = chat.overseas.sec.repo.run()
        return {
            **chat.research.repo.counts(),
            **basic,
            "overseas": {**overseas, "run": {k: v for k, v in sec_run.items() if k != "universe"} if sec_run else None},
            "savedAnalysisAvailable": basic["basicReports"] > 0 or overseas["analyses"] > 0,
            "ready": bool(chat.research.provider and (
                chat.research.dart or chat.research.config.sec_user_agent)),
            "bulk": {"status": run["status"],
                     "companies": len(run.get("inventoryCompleted", [])),
                     **chat.research.repo.bulk_counts(run["runId"])} if run else None,
        }

    return router
