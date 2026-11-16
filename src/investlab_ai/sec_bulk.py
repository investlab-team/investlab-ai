"""Resumable US top-100 / three-year SEC regular-report ingestion and basic analysis."""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, local
from uuid import uuid4

from filelock import FileLock

from investlab_ai.agent.sec_service import SecService
from investlab_ai.agent.service import ResearchService
from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.neon_environment import load_neon_environment
from investlab_ai.providers.openai_provider import OpenAIProvider
from investlab_ai.schemas import OverseasCompany, SecFiling
from investlab_ai.sec_storage import SecRepository
from investlab_ai.storage import Repository

ROOT = Path(__file__).resolve().parents[2]
QUOTA_CODES = {"OPENAI_QUOTA_EXHAUSTED", "OPENAI_SPEND_LIMIT_REACHED", "OPENAI_USAGE_LIMIT_REACHED"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("collect", "analyze", "all"), default="all")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--run-id")
    parser.add_argument("--new-run", action="store_true")
    args = parser.parse_args()
    load_neon_environment(ROOT)
    settings = Settings()
    if args.stage != "collect" and (not settings.openai_api_key or not settings.openai_model):
        raise ServiceError("AI_NOT_CONFIGURED", 503)
    repo = Repository(settings)
    sec = SecRepository(repo)
    run = None if args.new_run else sec.run(args.run_id)
    if args.run_id and run is None:
        raise ServiceError("SEC_RUN_NOT_FOUND", 404)
    if run is None:
        inventory = json.loads((ROOT / ".local/sec-inventory.json").read_text(encoding="utf-8"))
        if len(inventory["issuers"]) != 100:
            raise RuntimeError("Complete top-100 inventory is required")
        run = {"runId": str(uuid4()), "status": "inventoried", "startedAt": datetime.now(UTC).isoformat(),
               "startDate": inventory["startDate"], "endDate": inventory["endDate"],
               "universe": inventory["universe"], "companies": 100,
               "total": sum(len(i["filings"]) for i in inventory["issuers"]), "errors": {}}
        sec.save_run(run["runId"], run)
        for item in inventory["issuers"]:
            issuer = OverseasCompany.model_validate(item["issuer"])
            sec.save_issuer(issuer)
            sec.add_items(run["runId"], [SecFiling.model_validate(f) for f in item["filings"]])
    run_id = run["runId"]
    if args.stage != "collect":
        if run.get("analysisModel", settings.openai_model) != settings.openai_model:
            raise ServiceError("SEC_ANALYSIS_MODEL_MISMATCH", 409)
        if run.get("evidenceTokensPerReport", settings.sec_basic_analysis_tokens) != settings.sec_basic_analysis_tokens:
            raise ServiceError("SEC_ANALYSIS_BUDGET_MISMATCH", 409)
        run.setdefault("analysisModel", settings.openai_model)
        run.setdefault("evidenceTokensPerReport", settings.sec_basic_analysis_tokens)
        run.setdefault("analysisVersion", "sec-basic-v1")
    state, state_lock, stop = local(), Lock(), Event()
    services = []
    errors = {}

    def service():
        if not hasattr(state, "service"):
            worker_repo = Repository(settings)
            provider = OpenAIProvider(settings) if args.stage != "collect" else None
            research = ResearchService(settings, worker_repo, None, provider)
            state.service = SecService(research)
            with state_lock:
                services.append(state.service)
        return state.service

    def update():
        counts = sec.run_counts(run_id)
        run.update(collected=counts["collected"], analyzed=counts["analyzed"],
                   analyzedCompanies=counts["analyzedCompanies"], errors=dict(errors),
                   updatedAt=datetime.now(UTC).isoformat())
        sec.save_run(run_id, run)
        (ROOT / ".local/sec-progress.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        (ROOT.parent / "investlab-us-progress.json").write_text(json.dumps({k: v for k, v in run.items() if k != "universe"},
                                                                         ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"SEC {run['status']}: {run.get('collected', 0)}/{run['total']} collected; "
              f"{run.get('analyzed', 0)}/{run['total']} analyses; {run.get('analyzedCompanies', 0)}/100 companies", flush=True)

    def work(filing: SecFiling, phase: str):
        if stop.is_set():
            return
        worker = service()
        issuer = worker.repo.issuer(filing.cik)
        try:
            worker.collect(issuer, filing)
            if phase == "analyze":
                for attempt in range(4):
                    try:
                        worker.basic(filing)
                        break
                    except ServiceError as exc:
                        if exc.code != "OPENAI_RATE_LIMITED" or attempt == 3:
                            raise
                        time.sleep(15 * (attempt + 1))
            worker.repo.item_status(run_id, filing.accession, "analyzed" if phase == "analyze" else "collected")
            with state_lock:
                errors.pop(filing.accession, None)
        except ServiceError as exc:
            worker.repo.item_status(run_id, filing.accession, "error", exc.code)
            with state_lock:
                errors[filing.accession] = exc.code
            if exc.code in QUOTA_CODES or exc.code == "SEC_ACCESS_DENIED":
                run["blockedReason"] = exc.code
                stop.set()
        except Exception as exc:
            code = type(exc).__name__
            worker.repo.item_status(run_id, filing.accession, "error", code)
            with state_lock:
                errors[filing.accession] = code
        finally:
            with state_lock:
                run["processed"] = run.get("processed", 0) + 1
                if run["processed"] % 10 == 0:
                    update()

    lock = FileLock(str(ROOT / ".local/sec-bulk.lock"), timeout=1)
    try:
        with lock:
            for phase in (("collect", "analyze") if args.stage == "all" else (args.stage,)):
                run.update(status="collecting" if phase == "collect" else "analyzing", processed=0)
                update()
                items = [f for f, status in sec.items(run_id) if status != "analyzed"]
                if phase == "analyze":
                    items.sort(key=lambda f: (f.filed_on, f.accession), reverse=True)
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = [pool.submit(work, filing, phase) for filing in items]
                    for future in as_completed(futures):
                        future.result()
                if stop.is_set():
                    break
            counts = sec.run_counts(run_id)
            run["status"] = ("blocked" if stop.is_set() else "completed" if counts["analyzed"] == run["total"]
                             else "collected" if args.stage == "collect" and not errors else "partial")
            update()
    finally:
        for worker in services:
            worker.close()
            if worker.research.provider:
                worker.research.provider.close()
            worker.research.repo.close()
        repo.close()
    return 0 if run["status"] in {"completed", "collected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
