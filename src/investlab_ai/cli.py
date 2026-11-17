"""Local developer client. Explicit --env-file; never send keys from a browser."""
import argparse
import json
import sys

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from investlab_ai.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="DART 공시 분석 개발 클라이언트")
    parser.add_argument("--env-file", default=".env", help="명시적으로 읽을 개발 설정 파일")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="비밀값 없이 설정 여부만 확인")
    sub.add_parser("sync-companies", help="DART 기업 목록 저장")
    companies = sub.add_parser("companies", help="저장된 회사 검색")
    companies.add_argument("query")
    disclosures = sub.add_parser("disclosures", help="정기 공시 목록")
    disclosures.add_argument("corp_code")
    disclosures.add_argument("start_date")
    disclosures.add_argument("end_date")
    disclosures.add_argument("--page", type=int, default=1)
    for name in ("prepare", "status", "plan", "analyze", "ask"):
        command = sub.add_parser(name)
        command.add_argument("rcept_no")
        if name == "ask":
            command.add_argument("question")
        if name == "analyze":
            command.add_argument("--max-calls", type=int, default=10)
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        settings = Settings()
    except ValidationError:
        print("설정 오류: .env.example을 복사하고 서비스 토큰과 설정을 확인하세요.", file=sys.stderr)
        raise SystemExit(1) from None
    if args.command == "doctor":
        print(json.dumps({
            "serviceTokenConfigured": bool(settings.service_token),
            "dartKeyConfigured": bool(settings.dart_api_key),
            "openaiKeyConfigured": bool(settings.openai_api_key),
            "openaiModel": settings.openai_model,
            "storage": "local-sqlite" if settings.local_sqlite_path else "postgresql",
        }, ensure_ascii=False, indent=2))
        return
    method, body, params = "GET", None, None
    if args.command == "sync-companies":
        method, path = "POST", "/companies/sync"
    elif args.command == "companies":
        path, params = "/companies", {"query": args.query}
    elif args.command == "disclosures":
        path, params = "/disclosures", {
            "corpCode": args.corp_code, "startDate": args.start_date,
            "endDate": args.end_date, "page": args.page,
        }
    elif args.command == "ask":
        method, path, body = "POST", "/questions", {
            "rceptNo": args.rcept_no, "question": args.question,
        }
    else:
        suffix = {"status": "", "plan": "/analysis-plan"}.get(
            args.command, f"/{args.command}",
        )
        path = f"/disclosures/{args.rcept_no}{suffix}"
        if args.command in {"prepare", "analyze"}:
            method = "POST"
        if args.command == "analyze":
            body = {"maxModelCalls": args.max_calls}
    try:
        with httpx.Client(timeout=1800) as client:
            response = client.request(
                method, f"{args.url.rstrip('/')}/internal/v1{path}",
                params=params, json=body,
                headers={"Authorization": f"Bearer {settings.service_token}"},
            )
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        if response.is_error:
            raise SystemExit(1)
    except (httpx.HTTPError, ValueError):
        print("서버 연결/응답 오류: VS Code에서 서버를 실행했는지 확인하세요.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
