"""Background worker: picks up queued documents and processes them.

    python -m app.worker --once                       # process everything queued, then exit
    python -m app.worker --loop --interval 10         # keep running (production)
    python -m app.worker --loop --watch-dir inbox/    # also ingest PDFs dropped into a folder
"""
from __future__ import annotations

import argparse
import logging
import shutil
import signal
import time
from pathlib import Path

from app.config import Settings
from app.db import Session, create_tables, init_engine
from app.extraction import build_extractor
from app.processor import NotAPdfError, ingest_bytes, process_pending, requeue_stuck
from app.storage import build_storage

log = logging.getLogger("invoiceflow.worker")
_running = True


def _stop(signum, frame):  # noqa: ARG001
    global _running
    log.info("Shutdown requested; finishing the current document")
    _running = False


def ingest_folder(session, storage, folder: Path) -> int:
    """Ingest every PDF in a folder, then move it to processed/ (or rejected/)."""
    count = 0
    for path in sorted(folder.glob("*.pdf")):
        target = folder / "processed"
        try:
            _, created = ingest_bytes(session, storage, path.read_bytes(), path.name, source="folder")
            count += int(created)
        except NotAPdfError:
            target = folder / "rejected"
        target.mkdir(exist_ok=True)
        shutil.move(str(path), target / path.name)
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process the queue once and exit")
    parser.add_argument("--loop", action="store_true", help="Keep polling for new work")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between polls")
    parser.add_argument("--watch-dir", type=Path, help="Folder to ingest PDFs from")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    settings = Settings()
    init_engine(settings.database_url)
    create_tables()
    storage = build_storage(settings)
    extractor = build_extractor(settings)
    log.info("Worker started (extractor=%s)", settings.extractor)

    while _running:
        session = Session()
        try:
            if args.watch_dir:
                args.watch_dir.mkdir(parents=True, exist_ok=True)
                ingest_folder(session, storage, args.watch_dir)
            requeue_stuck(session)
            n = process_pending(session, storage, extractor, settings)
            if n:
                log.info("Processed %d document(s)", n)
        finally:
            Session.remove()
        if not args.loop:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
