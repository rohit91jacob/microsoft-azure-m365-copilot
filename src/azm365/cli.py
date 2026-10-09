"""``azm365`` command line. Exit codes: 0 ok, 2 usage, 3 data-quality failure, 4 dbt failure."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

from azm365 import __version__, bronze, pipeline
from azm365.extract import SOURCES
from azm365.http import FetchError
from azm365.lake import open_lake
from azm365.settings import Settings


def _setup_logging(verbose: bool) -> None:
    fmt = os.environ.get("AZM365_LOG_FORMAT", "text")
    level = logging.DEBUG if verbose else logging.INFO
    if fmt == "json":
        logging.basicConfig(
            level=level,
            format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
        )
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("azure", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _common_extract_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", action="append", choices=SOURCES, help="repeatable; default all")
    p.add_argument("--as-of", help="UTC date/time the snapshot is taken for (default now)")
    p.add_argument(
        "--fixtures",
        type=Path,
        help="serve requests from a fixture directory instead of the network (synthetic data)",
    )
    p.add_argument("--force", action="store_true", help="re-fetch and overwrite existing snapshots")
    p.add_argument(
        "--max-pages",
        type=int,
        help="sample: cap Retail Prices pages per filter (the snapshot is marked incomplete)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="azm365", description=__doc__)
    p.add_argument("--version", action="version", version=f"azm365 {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="land raw snapshots in bronze")
    _common_extract_args(e)

    s = sub.add_parser("silver", help="bronze -> silver Parquet (incremental)")
    s.add_argument("--full-refresh", action="store_true")

    sub.add_parser("dq", help="run the silver data-quality gate")

    d = sub.add_parser("dbt", help="run dbt against the silver tables (default: build)")
    d.add_argument("dbt_args", nargs=argparse.REMAINDER)

    sub.add_parser("publish", help="copy the dbt marts to the gold container as Parquet")

    r = sub.add_parser("run", help="extract -> silver -> DQ -> dbt build -> publish")
    _common_extract_args(r)
    r.add_argument("--skip-extract", action="store_true")

    rep = sub.add_parser("report", help="write the static results site")
    rep.add_argument("--out", type=Path, default=Path("site"))

    sub.add_parser("status", help="summarise what is in bronze")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    settings = Settings.from_env()
    try:
        if args.cmd == "extract":
            landed = pipeline.extract(
                settings,
                args.source,
                as_of=args.as_of,
                fixtures=args.fixtures,
                force=args.force,
                max_pages=args.max_pages,
            )
            print(
                json.dumps({"landed": len(landed), "items": sum(r["item_count"] for r in landed)})
            )
        elif args.cmd == "silver":
            print(json.dumps(pipeline.build_silver(settings, full_refresh=args.full_refresh)))
        elif args.cmd == "dq":
            results = pipeline.run_dq(settings)
            print(
                json.dumps({"checks": len(results), "warnings": sum(not r.passed for r in results)})
            )
        elif args.cmd == "dbt":
            dbt_args = [a for a in args.dbt_args if a != "--"] or ["build"]
            pipeline.run_dbt(settings, dbt_args)
        elif args.cmd == "publish":
            print(json.dumps(pipeline.publish_gold(settings)))
        elif args.cmd == "run":
            summary = pipeline.run_all(
                settings,
                sources=args.source,
                as_of=args.as_of,
                fixtures=args.fixtures,
                force=args.force,
                max_pages=args.max_pages,
                skip_extract=args.skip_extract,
            )
            print(json.dumps(summary, default=str))
        elif args.cmd == "report":
            from azm365 import report

            summary = report.build_site(settings, args.out)
            print(json.dumps(summary, default=str)[:2000])
        elif args.cmd == "status":
            manifests = bronze.list_manifests(open_lake(settings))
            counts = Counter((m["source"], m["scope"], m["origin"]) for m in manifests)
            for (source, scope, origin), n in sorted(counts.items()):
                print(f"{source:14} {scope:28} {origin:8} {n:4} snapshot(s)")
    except pipeline.DataQualityError as exc:
        logging.getLogger("azm365").error("%s", exc)
        return pipeline.EXIT_DQ
    except pipeline.TransformError as exc:
        logging.getLogger("azm365").error("%s", exc)
        return pipeline.EXIT_DBT
    except FetchError as exc:
        logging.getLogger("azm365").error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
