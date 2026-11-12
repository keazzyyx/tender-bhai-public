"""Nightly scrape + extract. ``--dry-run`` prints the digest to stdout instead of storing/sending."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tender_bhai import obs
from tender_bhai.config import load_settings
from tender_bhai.db import Database
from tender_bhai.digest import build_digest
from tender_bhai.extract import Extractor
from tender_bhai.pipeline import nightly


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tender Bhai nightly run")
    parser.add_argument("--dry-run", action="store_true", help="scrape + extract but print instead of storing")
    parser.add_argument("--portals", default="", help="comma-separated scraper names to run (default: all)")
    parser.add_argument("--max-extract", type=int, default=80, help="cap on LLM extractions per run")
    parser.add_argument("--no-llm", action="store_true", help="stop after scraping + dedup (no Anthropic calls)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_settings()
    obs.init(workflow_name="tender-bhai", tags=[*cfg.env_tags, "nightly"])

    db: Database | None = None
    run_id: str | None = None
    if cfg.supabase_configured:
        db = Database(cfg.supabase_url, cfg.supabase_service_key)
        if not args.dry_run:
            run_id = db.start_run(dry_run=False)
    else:
        logging.warning("Supabase not configured - running without dedup/storage")

    extractor = None
    if not args.no_llm:
        if cfg.anthropic_configured:
            extractor = Extractor(cfg=cfg)
        else:
            logging.warning("ANTHROPIC_API_KEY not set - skipping extraction")

    only = {p.strip() for p in args.portals.split(",") if p.strip()} or None
    exit_code = 0
    try:
        report = asyncio.run(
            nightly(cfg, db, extractor, dry_run=args.dry_run, only_portals=only, max_extract=args.max_extract)
        )
        summary = report.as_row()
        logging.info(
            "run %s: %d candidates, %d after dedup, %d extracted, %d new, %d skipped (fit), %d skipped (value), %d errors",
            summary["status"],
            report.candidates,
            report.after_dedup,
            report.extracted,
            len(report.new_tenders),
            report.skipped_fit,
            report.skipped_value,
            len(report.errors),
        )
        for err in report.errors:
            logging.warning("  %s: %s", err["portal"], err["error"])
        if args.dry_run:
            print(build_digest(report.new_tenders))
        if db is not None and run_id:
            db.finish_run(run_id, summary)
        if summary["status"] == "failed":
            exit_code = 1
    except Exception as exc:
        logging.exception("nightly run crashed: %s", exc)
        if db is not None and run_id:
            db.finish_run(run_id, {"status": "failed", "errors": [{"portal": "run", "error": str(exc)[:300]}]})
        exit_code = 1
    finally:
        if db is not None:
            db.close()
        obs.finish()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
