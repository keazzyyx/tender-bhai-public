"""Morning digest: unsent tenders + 3-day closing reminders -> WhatsApp/Telegram."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tender_bhai import obs
from tender_bhai.agent.tools import LAST_DIGEST_KEY
from tender_bhai.config import load_settings
from tender_bhai.dates import now_ist, to_iso
from tender_bhai.db import Database
from tender_bhai.delivery import deliver_digest
from tender_bhai.digest import build_digest, split_message
from tender_bhai.messaging.factory import get_messenger, recipient_for

MAX_TENDERS_PER_DIGEST = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the Tender Bhai morning digest")
    parser.add_argument("--dry-run", action="store_true", help="print instead of sending; marks nothing as sent")
    parser.add_argument("--force-empty", action="store_true", help="send even when there are no new tenders")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cfg = load_settings()
    obs.init(workflow_name="tender-bhai", tags=[*cfg.env_tags, "digest"])
    if not cfg.supabase_configured:
        logging.error("SUPABASE_URL / SUPABASE_SERVICE_KEY are required to send the digest")
        return 2
    db = Database(cfg.supabase_url, cfg.supabase_service_key)
    try:
        tenders = db.unsent_tenders(limit=MAX_TENDERS_PER_DIGEST)
        reminders = db.closing_soon(days=3, sent_only=True)
        if not tenders and not reminders and not args.force_empty:
            logging.info("nothing new and nothing closing soon - no digest today")
            return 0
        digest = build_digest(tenders, reminders)
        parts = split_message(digest)
        if args.dry_run:
            print(digest)
            logging.info("dry run: %d part(s), %d tenders, %d reminders", len(parts), len(tenders), len(reminders))
            return 0
        send = obs.span(kind="TOOL", name="send_digest", tool_name=cfg.messaging_provider)(deliver_digest)
        outcome = send(get_messenger(cfg), db, recipient_for(cfg), parts, len(tenders))
        logging.info("digest outcome: %s (%d parts)", outcome, len(parts))
        if outcome == "failed":
            return 1
        ids = [t.id for t in tenders if t.id]
        db.mark_sent(ids)
        db.set_preference(LAST_DIGEST_KEY, {"sent_at": to_iso(now_ist()), "ids": ids})
        return 0
    except Exception as exc:
        logging.exception("send_digest crashed: %s", exc)
        return 1
    finally:
        db.close()
        obs.finish()


if __name__ == "__main__":
    raise SystemExit(main())
