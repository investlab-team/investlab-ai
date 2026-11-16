"""Prepare a reproducible US-listed issuer universe, then resume shared SEC ingestion."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from investlab_ai.config import Settings
from investlab_ai.errors import ServiceError
from investlab_ai.ingestion.catalog import years_ago
from investlab_ai.ingestion.sec import SecClient
from investlab_ai.ingestion.us_ranking import us_top_companies
from investlab_ai.market import NEW_YORK
from investlab_ai.neon_environment import load_neon_environment
from investlab_ai.schemas import OverseasCompany

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("collect", "analyze", "all"), default="all")
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    try:
        load_neon_environment(ROOT)
        path = ROOT / ".local/sec-inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.refresh_universe or not path.exists():
            client = SecClient(Settings())
            try:
                universe = us_top_companies(client.companies())
                today = datetime.now(NEW_YORK).date()
                start = years_ago(today)
                inventory = {"universe": universe, "startDate": start.isoformat(),
                             "endDate": today.isoformat(), "issuers": []}
                for index, item in enumerate(universe["companies"]):
                    issuer = OverseasCompany.model_validate(item["issuer"])
                    filings = client.filings(issuer, start, today)
                    inventory["issuers"].append({"issuer": issuer.model_dump(mode="json", by_alias=True),
                                                "filings": [f.model_dump(mode="json", by_alias=True) for f in filings]})
                    print(f"보고서 목록 준비: {index + 1}/100 기업", flush=True)
                path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
            finally:
                client.close()
        from investlab_ai import sec_bulk
        sys.argv = ["sec_bulk", "--stage", args.stage, "--workers", str(args.workers)]
        if args.refresh_universe:
            sys.argv.append("--new-run")
        print("공시별 기본 분석은 기존 캐시를 재사용합니다. 새 분석에는 설정한 OpenAI API 사용료가 발생합니다.", flush=True)
        return sec_bulk.main()
    except ServiceError as exc:
        print("설정 또는 서비스 확인 필요:", exc.code)
        return 1
    except Exception as exc:
        # Provider/database exception strings can contain credentials.
        print("실행 실패:", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
