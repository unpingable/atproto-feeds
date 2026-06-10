"""receipts_feed.health.v0 — semantic feed health.

Doctrine (chatty 2026-06-10, after the May 30 cursor-freeze archaeology):

    service_liveness != feed_usefulness
    200 OK + active unit + cursor != admissible feed

The holy trinity this module asserts:

    alive       — drain task exists and is not crashed
    advancing   — cursor advancing + drain loop iterating recently
    consumer-useful — newest skeleton item fresh, URIs renderable, AppView
                      can actually resolve them

Each check carries its own verdict (pass / warn / fail); an overall
verdict folds them. The CLI command exits nonzero on warn/fail so cron
can gate on it; the HTTP endpoint always returns 200 with the JSON
verdict (clients see-and-decide).

NOT a replacement for the dumb-liveness /health endpoint — that one is
for load-balancers and supervisors that just want a 200 to keep
forwarding traffic. /health/semantic and `receipts-feed health` are for
operators who want to know whether the feed is doing its job.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import db, timeutil

LOG = logging.getLogger("receipts.health")


RECEIPT_KIND = "receipts_feed.health.v0"
RECEIPT_SCHEMA_VERSION = 0


# Thresholds. Chosen for steady-state live ingest; tune via env if needed.
# All env vars accept seconds (or ratios as floats).
THRESHOLDS = {
    # Cursor age: in steady state, the cursor sits within ~minutes of "now".
    # 5 minutes is the warn boundary; 30 minutes is fail.
    "cursor_age_warn_seconds": float(os.getenv("HEALTH_CURSOR_AGE_WARN", "300")),
    "cursor_age_fail_seconds": float(os.getenv("HEALTH_CURSOR_AGE_FAIL", "1800")),

    # Drain progress age: the drain loop iterates every ~10s (timeout) at
    # minimum. >90s without progress means drain is stuck.
    "drain_progress_warn_seconds": float(os.getenv("HEALTH_DRAIN_PROGRESS_WARN", "90")),
    "drain_progress_fail_seconds": float(os.getenv("HEALTH_DRAIN_PROGRESS_FAIL", "300")),

    # Queue backlog: max=5000, warn at 4000, fail at 4900 (about to saturate
    # → drop window opening).
    "queue_backlog_warn": int(os.getenv("HEALTH_QUEUE_BACKLOG_WARN", "4000")),
    "queue_backlog_fail": int(os.getenv("HEALTH_QUEUE_BACKLOG_FAIL", "4900")),

    # Drop rate: catch-up bursts spike this; in steady state it should be
    # near zero. >1000/min warn, >10000/min fail.
    "drop_rate_warn_per_min": float(os.getenv("HEALTH_DROP_RATE_WARN", "1000")),
    "drop_rate_fail_per_min": float(os.getenv("HEALTH_DROP_RATE_FAIL", "10000")),

    # Newest skeleton item age: if the freshest URI in the served skeleton
    # is older than this, the feed is fossilizing.
    "newest_item_age_warn_hours": float(os.getenv("HEALTH_NEWEST_ITEM_WARN_H", "6")),
    "newest_item_age_fail_hours": float(os.getenv("HEALTH_NEWEST_ITEM_FAIL_H", "24")),

    # Skeleton renderable ratio: fraction of skeleton URIs that are
    # app.bsky.feed.post (the only kind Bluesky AppView can render via the
    # post path). After the 2026-06-10 fix, must be 1.0.
    "renderable_ratio_warn": float(os.getenv("HEALTH_RENDERABLE_RATIO_WARN", "1.0")),
    "renderable_ratio_fail": float(os.getenv("HEALTH_RENDERABLE_RATIO_FAIL", "0.9")),

    # AppView resolution: fraction of probed skeleton URIs the public
    # AppView returns posts for. Some posts may be deleted/private —
    # allow some failures.
    "appview_resolution_warn": float(os.getenv("HEALTH_APPVIEW_WARN", "0.8")),
    "appview_resolution_fail": float(os.getenv("HEALTH_APPVIEW_FAIL", "0.5")),
}

APPVIEW_BASE = os.getenv(
    "APPVIEW_BASE_URL", "https://public.api.bsky.app"
)


# ---------------------------------------------------------------------------
# Check primitives
# ---------------------------------------------------------------------------

def _verdict_for(value: float, warn: float, fail: float, *, higher_is_worse: bool = True) -> str:
    """Map a numeric value to pass/warn/fail given threshold ordering.

    For higher_is_worse: value < warn → pass; warn <= value < fail → warn;
    value >= fail → fail.

    For higher_is_better (higher_is_worse=False): value >= warn → pass;
    fail < value < warn → warn; value <= fail → fail.
    """
    if higher_is_worse:
        if value >= fail:
            return "fail"
        if value >= warn:
            return "warn"
        return "pass"
    else:
        # higher is better (e.g. renderable ratio)
        if value <= fail:
            return "fail"
        if value < warn:
            return "warn"
        return "pass"


def _check(name: str, value: Any, threshold: Any, verdict: str, note: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": name,
        "value": value,
        "threshold": threshold,
        "verdict": verdict,
    }
    if note:
        out["note"] = note
    return out


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _cursor_age_seconds(conn: sqlite3.Connection, *, consumer_name: str = "receipts_consumer") -> Optional[float]:
    """Decode the stored Jetstream cursor (microseconds since epoch) into a
    wall-clock age in seconds. Returns None if no cursor saved yet.
    """
    row = conn.execute(
        "SELECT cursor FROM cursors WHERE consumer = ?",
        (consumer_name,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        cursor_us = int(row[0])
    except (TypeError, ValueError):
        return None
    cursor_seconds = cursor_us / 1_000_000
    return max(0.0, time.time() - cursor_seconds)


def _check_cursor_age(conn: sqlite3.Connection) -> Dict[str, Any]:
    age = _cursor_age_seconds(conn)
    if age is None:
        return _check("cursor_age_seconds", None, None, "warn",
                      note="no cursor saved yet (fresh start?)")
    warn = THRESHOLDS["cursor_age_warn_seconds"]
    fail = THRESHOLDS["cursor_age_fail_seconds"]
    verdict = _verdict_for(age, warn, fail)
    return _check(
        "cursor_age_seconds", round(age, 1),
        {"warn": warn, "fail": fail}, verdict,
        note=("stale cursor — ingest falling behind" if verdict != "pass" else ""),
    )


def _check_drain_progress(consumer) -> Dict[str, Any]:
    """Consumer is None when this health check runs out-of-process (CLI).
    In that case we can't directly inspect the drain task; return verdict
    skipped so the receipt is honest about what it knows."""
    if consumer is None:
        return _check("drain_progress_age_seconds", None, None, "skipped",
                      note="consumer not in-process (CLI invocation)")
    last = getattr(consumer, "_last_progress_at", 0.0)
    if not last:
        return _check("drain_progress_age_seconds", None, None, "warn",
                      note="drain has never recorded progress (just started?)")
    age = time.time() - last
    warn = THRESHOLDS["drain_progress_warn_seconds"]
    fail = THRESHOLDS["drain_progress_fail_seconds"]
    verdict = _verdict_for(age, warn, fail)
    return _check(
        "drain_progress_age_seconds", round(age, 1),
        {"warn": warn, "fail": fail}, verdict,
        note=("drain alive but not advancing" if verdict != "pass" else ""),
    )


def _check_drain_alive(consumer) -> Dict[str, Any]:
    if consumer is None:
        return _check("drain_alive", None, True, "skipped",
                      note="consumer not in-process")
    # The drain task is held on consumer.run()'s local scope, not as an
    # attribute. We infer aliveness from _last_progress_at being recent
    # AND consumer._stop being false. If we ever surface the task object
    # we can check more directly.
    if consumer._stop:
        return _check("drain_alive", False, True, "fail", note="consumer stopped")
    return _check("drain_alive", True, True, "pass")


def _check_queue_backlog(consumer) -> Dict[str, Any]:
    if consumer is None:
        return _check("queue_backlog", None, None, "skipped",
                      note="consumer not in-process")
    backlog = consumer._event_queue.qsize() if consumer._event_queue else 0
    warn = THRESHOLDS["queue_backlog_warn"]
    fail = THRESHOLDS["queue_backlog_fail"]
    verdict = _verdict_for(backlog, warn, fail)
    return _check(
        "queue_backlog", backlog,
        {"warn": warn, "fail": fail}, verdict,
        note=("queue saturating — drain can't keep up" if verdict != "pass" else ""),
    )


def _check_drop_rate(consumer) -> Dict[str, Any]:
    if consumer is None:
        return _check("drop_rate_per_min", None, None, "skipped",
                      note="consumer not in-process")
    rate = getattr(consumer, "_drop_rate_per_min", 0.0)
    warn = THRESHOLDS["drop_rate_warn_per_min"]
    fail = THRESHOLDS["drop_rate_fail_per_min"]
    verdict = _verdict_for(rate, warn, fail)
    return _check(
        "drop_rate_per_min", round(rate, 1),
        {"warn": warn, "fail": fail}, verdict,
        note=("queue full — events dropping" if verdict != "pass" else ""),
    )


def _sample_skeleton(feed_name: str = "receipts", limit: int = 15) -> List[Dict[str, Any]]:
    """Read the same `ranked_posts` table the live skeleton endpoint reads,
    so this check sees exactly what Bluesky AppView would see.
    """
    ranked = db.get_ranked_posts(feed_name, limit=limit)
    return [{"post": r["uri"]} for r in ranked if r.get("uri")]


def _check_renderable_ratio(skeleton: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not skeleton:
        return _check("skeleton_renderable_ratio", None, None, "fail",
                      note="skeleton is empty")
    total = len(skeleton)
    renderable = sum(
        1 for item in skeleton
        if "/app.bsky.feed.post/" in (item.get("post") or "")
    )
    ratio = renderable / total if total else 0.0
    warn = THRESHOLDS["renderable_ratio_warn"]
    fail = THRESHOLDS["renderable_ratio_fail"]
    verdict = _verdict_for(ratio, warn, fail, higher_is_worse=False)
    return _check(
        "skeleton_renderable_ratio", round(ratio, 3),
        {"warn": warn, "fail": fail}, verdict,
        note=(f"{total - renderable}/{total} URIs are not feed.post records"
              if ratio < 1.0 else ""),
    )


def _check_newest_skeleton_item_age(
    conn: sqlite3.Connection, skeleton: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Look up each skeleton URI in the local posts table, find the newest
    created_at. Posts not in the local DB are skipped.
    """
    if not skeleton:
        return _check("newest_skeleton_item_age_hours", None, None, "fail",
                      note="skeleton is empty")
    uris = [item["post"] for item in skeleton]
    placeholders = ",".join("?" for _ in uris)
    rows = conn.execute(
        f"SELECT MAX(created_at) FROM posts WHERE uri IN ({placeholders})",
        uris,
    ).fetchone()
    max_created = rows[0] if rows else None
    if not max_created:
        return _check("newest_skeleton_item_age_hours", None, None, "fail",
                      note="no skeleton URIs found in posts table")
    try:
        dt = timeutil.to_utc_datetime(max_created)
    except Exception:
        return _check("newest_skeleton_item_age_hours", None, None, "fail",
                      note=f"failed to parse created_at: {max_created!r}")
    age_hours = max(0.0, (timeutil.now_utc() - dt).total_seconds() / 3600.0)
    warn = THRESHOLDS["newest_item_age_warn_hours"]
    fail = THRESHOLDS["newest_item_age_fail_hours"]
    verdict = _verdict_for(age_hours, warn, fail)
    return _check(
        "newest_skeleton_item_age_hours", round(age_hours, 2),
        {"warn": warn, "fail": fail}, verdict,
        note=("feed is fossilizing — newest skeleton item too old"
              if verdict != "pass" else ""),
    )


def _check_appview_resolution(
    skeleton: List[Dict[str, Any]],
    *,
    sample_size: int = 5,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Probe public.api.bsky.app/xrpc/app.bsky.feed.getPosts with the top-N
    skeleton URIs and assert AppView returns posts. Some failures are
    expected (deleted/private/blocked); thresholds allow a fraction.
    """
    if not skeleton:
        return _check("appview_resolution_ratio", None, None, "fail",
                      note="skeleton is empty")
    sample = [item["post"] for item in skeleton[:sample_size]]
    qs = "&".join(f"uris={urllib.parse.quote(u, safe='')}" for u in sample)
    url = f"{APPVIEW_BASE}/xrpc/app.bsky.feed.getPosts?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — network call
        return _check("appview_resolution_ratio", None, None, "fail",
                      note=f"appview probe failed: {e!r}")
    returned = len(body.get("posts") or [])
    ratio = returned / len(sample) if sample else 0.0
    warn = THRESHOLDS["appview_resolution_warn"]
    fail = THRESHOLDS["appview_resolution_fail"]
    verdict = _verdict_for(ratio, warn, fail, higher_is_worse=False)
    return _check(
        "appview_resolution_ratio", round(ratio, 3),
        {"warn": warn, "fail": fail}, verdict,
        note=(f"{returned}/{len(sample)} sample URIs resolved through AppView"),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_health(
    *,
    consumer=None,
    probe_appview: bool = False,
    skeleton_sample_size: int = 5,
) -> Dict[str, Any]:
    """Build the receipts_feed.health.v0 receipt.

    Parameters:
      consumer            — JetstreamConsumer instance for in-process checks
                            (queue backlog, drain progress, drop rate). When
                            None, those checks return verdict=skipped.
      probe_appview       — if True, makes an external HTTP call to
                            public.api.bsky.app to verify URIs resolve.
                            Skip for the live /health/semantic endpoint
                            (avoid blocking-IO in async handler); enable for
                            the CLI command.
      skeleton_sample_size — number of skeleton URIs to AppView-probe.
    """
    conn = db.get_conn()
    try:
        skeleton = _sample_skeleton()
        checks = [
            _check_drain_alive(consumer),
            _check_drain_progress(consumer),
            _check_cursor_age(conn),
            _check_queue_backlog(consumer),
            _check_drop_rate(consumer),
            _check_renderable_ratio(skeleton),
            _check_newest_skeleton_item_age(conn, skeleton),
        ]
        if probe_appview:
            checks.append(_check_appview_resolution(
                skeleton, sample_size=skeleton_sample_size,
            ))
    finally:
        conn.close()

    verdicts = [c["verdict"] for c in checks]
    if "fail" in verdicts:
        overall = "failed"
    elif "warn" in verdicts:
        overall = "degraded"
    elif all(v in ("pass", "skipped") for v in verdicts):
        overall = "healthy"
    else:
        overall = "unknown"

    rationale = [
        f"{c['name']}: {c['verdict']}"
        + (f" ({c['note']})" if c.get("note") else "")
        for c in checks
        if c["verdict"] in ("warn", "fail")
    ]
    if not rationale:
        rationale = ["all checks pass within thresholds"]

    return {
        "receipt_kind": RECEIPT_KIND,
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "generated_at": timeutil.now_utc().isoformat(),
        "verdict": overall,
        "checks": checks,
        "skeleton_size": len(skeleton),
        "probe_appview": probe_appview,
        "rationale": rationale,
    }
