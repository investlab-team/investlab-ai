"""Resumable top-100 / three-year periodic-report collection and basic analysis."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, local
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.catalog import regular_reports, years_ago
from investlab_ai.ingestion.dart import DartClient
from investlab_ai.ingestion.market import top_companies
from investlab_ai.providers.openai_provider import OpenAIProvider
from investlab_ai.storage import Repository


def make_service(config: Settings) -> ResearchService:
    repo = Repository(config)
    if config.local_sqlite_path is not None:
        repo.initialize_local()
    return ResearchService(
        config, repo,
        DartClient(config.dart_api_key.get_secret_value()) if config.dart_api_key else None,
        OpenAIProvider(config) if config.openai_api_key and config.openai_model else None,
    )


def close_service(service: ResearchService):
    if service.dart:
        service.dart.close()
    if service.provider:
        service.provider.close()
    service.repo.close()


def main():
    parser = argparse.ArgumentParser(description="시총 상위 100개 기업의 최근 3년 정기보고서")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--run-id")
    parser.add_argument("--phase", choices=["inventory", "collect", "analyze", "all"], default="all")
    parser.add_argument("--workers", type=int, choices=range(1, 33), default=3)
    parser.add_argument("--reverse-order", action="store_true", help="최근 공시부터 처리")
    parser.add_argument("--recover-running", action="store_true", help="기존 실행을 종료한 뒤 중단 상태 복구")
    parser.add_argument("--limit", type=int, help="점검용 최대 처리 건수; 기본은 미완료 전체")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    config = Settings()
    service = make_service(config)
    repo = service.repo
    output = Path(".local/bulk-progress.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_lock = Lock()
    stop_paid = False
    thread_state = local()
    workers = []

    def emit(data):
        with write_lock:
            output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(data, ensure_ascii=False), flush=True)

    try:
        if args.run_id:
            run = repo.bulk(args.run_id)
            if run is None:
                raise ServiceError("BULK_RUN_NOT_FOUND", 404)
        else:
            today = datetime.now(ZoneInfo("Asia/Seoul")).date()
            if not repo.has_companies():
                emit({"stage": "company_catalog"})
                repo.save_companies(service.require_dart().companies())
            ranking = top_companies(repo.all_companies())
            run = {
                "runId": str(uuid4()), "status": "inventory", "ranking": ranking,
                "startDate": years_ago(today).isoformat(), "endDate": today.isoformat(),
                "inventoryCompleted": [], "inventoryErrors": {}, "createdAt": datetime.now(UTC).isoformat(),
            }
            repo.save_bulk(run["runId"], run)
        from datetime import date
        if args.recover_running:
            repo.recover_bulk(run["runId"])
            for item in repo.bulk_items(run["runId"]):
                try:
                    if service.cached_analysis(item.rcept_no):
                        repo.bulk_item_status(run["runId"], item.rcept_no, "completed")
                except ServiceError:
                    pass
        if args.phase in {"inventory", "all"}:
            for target in run["ranking"]["companies"]:
                company = target["company"]
                code = company["corpCode"]
                if code in run["inventoryCompleted"]:
                    continue
                try:
                    reports = regular_reports(service.require_dart(), code,
                                              date.fromisoformat(run["startDate"]),
                                              date.fromisoformat(run["endDate"]))
                    repo.add_bulk_items(run["runId"], reports)
                    run["inventoryCompleted"].append(code)
                    run["inventoryErrors"].pop(code, None)
                except ServiceError as exc:
                    run["inventoryErrors"][code] = exc.code
                    if exc.code in {"DART_AUTH_FAILED", "DART_RATE_LIMITED"}:
                        run["status"] = "needs_attention"
                        repo.save_bulk(run["runId"], run)
                        emit({"runId": run["runId"], "stage": "inventory_stopped", "code": exc.code})
                        return
                repo.save_bulk(run["runId"], run)
                emit({"runId": run["runId"], "stage": "inventory",
                      "companies": len(run["inventoryCompleted"]), "company": company["corpName"],
                      **repo.bulk_counts(run["runId"])})
        if args.phase == "inventory":
            run["status"] = "inventory_ready" if not run["inventoryErrors"] else "inventory_partial"
            repo.save_bulk(run["runId"], run)
            emit({"runId": run["runId"], "stage": run["status"], **repo.bulk_counts(run["runId"])})
            return
        items = repo.bulk_items(run["runId"])
        if args.reverse_order:
            items.reverse()
        if args.limit is not None:
            items = items[:args.limit]
        run["status"] = "collecting" if args.phase == "collect" else "analyzing"
        repo.save_bulk(run["runId"], run)

        def process(disclosure):
            nonlocal stop_paid
            stop_file = Path(".local") / ("stop-" + run["runId"])
            if stop_paid or stop_file.exists():
                return
            if not hasattr(thread_state, "service"):
                thread_state.service = make_service(config)
                with write_lock:
                    workers.append(thread_state.service)
            worker = thread_state.service
            try:
                if not repo.claim_bulk_item(run["runId"], disclosure.rcept_no):
                    return
                if args.phase == "collect":
                    worker.collect_original(disclosure)
                    repo.bulk_item_status(run["runId"], disclosure.rcept_no, "collected")
                    emit({"runId": run["runId"], "stage": "collection",
                          "company": disclosure.corp_name, **repo.bulk_counts(run["runId"])})
                    return
                worker.collect_original(disclosure)
                if stop_paid or stop_file.exists():
                    repo.bulk_item_status(run["runId"], disclosure.rcept_no, "pending")
                    return
                result = worker.basic_analyze(disclosure.rcept_no)
                repo.bulk_item_status(run["runId"], disclosure.rcept_no, "completed")
                emit({"runId": run["runId"], "stage": "analysis", "company": disclosure.corp_name,
                      "rceptNo": disclosure.rcept_no, "cached": result.cached,
                      **repo.bulk_counts(run["runId"])})
            except ServiceError as exc:
                repo.bulk_item_status(run["runId"], disclosure.rcept_no, "failed", exc.code)
                if exc.code in {"OPENAI_RATE_LIMITED", "OPENAI_QUOTA_EXHAUSTED", "OPENAI_REQUEST_FAILED", "DART_AUTH_FAILED",
                                "DART_RATE_LIMITED", "OPENAI_SPEND_LIMIT_REACHED", "OPENAI_USAGE_LIMIT_REACHED"}:
                    stop_paid = True
                emit({"runId": run["runId"], "stage": "error", "code": exc.code,
                      "company": disclosure.corp_name, **repo.bulk_counts(run["runId"])})
            except Exception as exc:
                repo.bulk_item_status(run["runId"], disclosure.rcept_no, "failed", "PROCESSING_FAILED")
                emit({"runId": run["runId"], "stage": "error", "code": "PROCESSING_FAILED",
                      "exceptionType": type(exc).__name__,
                      "company": disclosure.corp_name, **repo.bulk_counts(run["runId"])})

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(process, item) for item in items]
            for future in as_completed(futures):
                future.result()
        counts = repo.bulk_counts(run["runId"])
        if args.phase == "collect":
            run["status"] = "collected" if counts["collected"] == counts["total"] else "collection_partial"
        else:
            run["status"] = "completed" if (counts["total"] == counts["completed"]
                                            and not run["inventoryErrors"]) else "partial"
        repo.save_bulk(run["runId"], run)
        emit({"runId": run["runId"], "stage": run["status"], **counts})
    except ServiceError as exc:
        emit({"stage": "stopped", "code": exc.code})
        raise SystemExit(1) from None
    finally:
        for worker in workers:
            close_service(worker)
        close_service(service)


if __name__ == "__main__":
    main()
