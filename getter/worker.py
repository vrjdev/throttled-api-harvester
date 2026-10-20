import gzip
import json
import logging
import signal
import time
from datetime import datetime, timezone

import requests

from .config import Config
from .ratelimit import RateLimiter
from .storage import S3Store, Store

log = logging.getLogger("getter")


def fetch(cfg: Config, session, limiter: RateLimiter, offset: int) -> dict:
    """One request, one attempt. Any problem is logged and returned as a record; never raised or retried."""
    limiter.wait()
    try:
        resp = session.get(
            cfg.api_url,
            params={cfg.param_name: offset},
            headers={cfg.auth_header: cfg.api_key} if cfg.api_key else {},
            timeout=30,
        )
        if not resp.ok:
            log.warning("offset=%d failed: HTTP %d", offset, resp.status_code)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return {"status": resp.status_code, "body": body}
    except Exception as e:  # network error, timeout, anything unexpected
        log.warning("offset=%d failed: %s: %s", offset, type(e).__name__, e)
        return {"status": None, "error": f"{type(e).__name__}: {e}"}


def _checkpoint_key(cfg: Config) -> str:
    return f"{cfg.prefix}/w{cfg.worker_index}/checkpoint.json"


def load_checkpoint(cfg: Config, store: Store) -> int:
    raw = store.get(_checkpoint_key(cfg))
    return json.loads(raw)["next"] if raw else cfg.worker_index


def flush(cfg: Config, store: Store, records: list, next_i: int) -> None:
    # Data first, checkpoint second: a crash in between only replays the batch,
    # and the deterministic key makes the replay overwrite instead of duplicate.
    key = f"{cfg.prefix}/w{cfg.worker_index}/batch-{records[0]['i']:010d}.jsonl.gz"
    payload = "\n".join(json.dumps(r) for r in records).encode() + b"\n"
    store.put(key, gzip.compress(payload, mtime=0))
    store.put(_checkpoint_key(cfg), json.dumps({"next": next_i}).encode())
    log.info("flushed %d records -> %s (next=%d)", len(records), key, next_i)


def _try_flush(cfg: Config, store: Store, records: list, next_i: int) -> list:
    """Flush; on failure log and keep the records so the next flush retries them (no data dropped)."""
    try:
        flush(cfg, store, records, next_i)
        return []
    except Exception:
        log.exception("flush failed, keeping %d records and continuing", len(records))
        return records


def run(cfg: Config, store: Store, session=None, limiter=None, stop=lambda: False) -> None:
    session = session or requests.Session()
    limiter = limiter or RateLimiter(cfg.qps)
    i = load_checkpoint(cfg, store)
    log.info("worker %d starting at i=%d of %d", cfg.worker_index, i, cfg.total)
    records: list = []
    while i < cfg.total and not stop():
        counter = cfg.total - i  # counts down from TOTAL_REQUESTS to 1
        offset = counter * cfg.step
        result = fetch(cfg, session, limiter, offset)
        records.append(
            {"i": i, "counter": counter, "offset": offset, "fetched_at": datetime.now(timezone.utc).isoformat(), **result}
        )
        i += cfg.worker_count
        if len(records) >= cfg.batch_size:
            records = _try_flush(cfg, store, records, i)
    if records:
        _try_flush(cfg, store, records, i)
    log.info("worker %d done at i=%d", cfg.worker_index, i)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = Config.from_env()
    stopping = False

    def _stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run(cfg, S3Store(cfg.bucket), stop=lambda: stopping)


if __name__ == "__main__":
    main()
