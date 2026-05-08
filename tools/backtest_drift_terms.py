#!/usr/bin/env python3
"""Backtest drift-phase term emergence within Receipts story clusters.

Read-only analysis of existing posts and clusters. For each cluster, emit a
CSV row showing the timestamp at which a member post matching each phase
bucket first appeared.

Purpose: answer one question before any data-model commitment — do drift
"phases" actually appear in a stable order in the data we already have?

What it can show
- Whether phase terms appear in clusters at all
- Relative ordering of first-appearance per phase
- Which post carried the earliest match per phase
- Whether wire/primary-source domains lead or lag social commentary

What it cannot show
- Whether a matching post is actually about the phase (false positives are guaranteed)
- Whether absence of a term means the phase didn't happen
- Anything in images, videos, or quoted posts the regex can't see
- Causality: term ordering is not narrative causation

Usage
    python tools/backtest_drift_terms.py --out /tmp/drift.csv
    python tools/backtest_drift_terms.py --out - --limit 50 --min-members 3
    python tools/backtest_drift_terms.py --cluster-types url root --out drift.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys

from receipts_feed import db

LOG = logging.getLogger("receipts.backtest_drift")


# Deliberately small, dumb, and obvious term lists.
# This is a smoke test for ordering, not a classifier. Do not promote.

PHASE_TEXT_TERMS: dict[str, list[str]] = {
    "object_event": [
        "shooting", "shooter", "gunman", "evacuation", "evacuated",
        "arrest", "arrested", "suspect", "breach", "breached",
        "fatality", "fatalities", "wounded", "killed", "explosion",
        "fire", "crash", "collapse", "raid", "lockdown",
    ],
    "confirmation": [
        "AP", "Reuters", "AFP", "Associated Press", "police said",
        "officials said", "spokesperson", "press conference",
        "statement", "confirmed", "according to", "report says",
        "reports indicate",
    ],
    "political_use": [
        "ballroom", "border", "second amendment", "gun control",
        "campaign", "blame", "this is what happens",
        "thoughts and prayers", "the left", "the right",
        "wokeness", "DEI", "deep state", "elites", "weaponized",
    ],
    "conspiracy": [
        "psyop", "false flag", "staged", "crisis actor", "hoax",
        "fake", "convenient timing", "controlled",
        "they want you to", "they don't want you to",
    ],
    "meta_discourse": [
        "discourse", "people are saying", "why is everyone",
        "the takes", "everyone on here", "this app", "the timeline",
        "the algorithm", "i can't with",
    ],
}

WIRE_OR_PRIMARY_DOMAINS: frozenset[str] = frozenset({
    "apnews.com", "reuters.com", "afp.com",
    "bbc.com", "bbc.co.uk",
    "c-span.org", "cspan.org",
    "supremecourt.gov", "justice.gov", "sec.gov", "fbi.gov",
    "whitehouse.gov", "state.gov", "defense.gov", "treasury.gov",
    "congress.gov", "senate.gov", "house.gov",
    "courtlistener.com", "documentcloud.org",
})

OUTRAGE_PACKAGING_DOMAINS: frozenset[str] = frozenset({
    "dailybeast.com", "rawstory.com", "mediaite.com",
    "mediamatters.org", "huffpost.com", "salon.com",
    "twitchy.com", "breitbart.com", "thefederalist.com",
    "redstate.com", "occupydemocrats.com",
})


def _compile(terms: list[str]) -> re.Pattern[str]:
    parts = []
    for t in terms:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9\s\-']*[A-Za-z0-9]", t):
            parts.append(r"\b" + re.escape(t) + r"\b")
        else:
            parts.append(re.escape(t))
    return re.compile("|".join(parts), re.IGNORECASE)


PHASE_REGEX: dict[str, re.Pattern[str]] = {
    bucket: _compile(terms) for bucket, terms in PHASE_TEXT_TERMS.items()
}
ALL_PHASES: list[str] = list(PHASE_TEXT_TERMS.keys()) + [
    "wire_or_primary_source", "outrage_packaging",
]


def match_phases_for_post(
    text: str | None, external_domain: str | None
) -> dict[str, list[str]]:
    matched: dict[str, list[str]] = {}
    if text:
        for phase, regex in PHASE_REGEX.items():
            hits = regex.findall(text)
            if hits:
                matched[phase] = sorted({h.lower() for h in hits})
    if external_domain:
        d = external_domain.lower().lstrip(".")
        if d in WIRE_OR_PRIMARY_DOMAINS:
            matched["wire_or_primary_source"] = [d]
        if d in OUTRAGE_PACKAGING_DOMAINS:
            matched["outrage_packaging"] = [d]
    return matched


def iter_clusters(
    conn,
    min_members: int,
    cluster_types: list[str] | None,
    limit: int | None,
):
    where = ["post_count >= ?"]
    params: list = [min_members]
    if cluster_types:
        placeholders = ",".join("?" * len(cluster_types))
        where.append(f"cluster_type IN ({placeholders})")
        params.extend(cluster_types)
    sql = (
        "SELECT cluster_id, cluster_type, first_seen_at, post_count, state "
        "FROM story_clusters "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY first_seen_at"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    for crow in conn.execute(sql, params):
        cluster = dict(crow)
        members = conn.execute(
            "SELECT cm.post_uri, cm.author_did, cm.joined_at, "
            "       p.text, p.created_at, p.external_domain "
            "FROM cluster_members cm "
            "LEFT JOIN posts p ON p.uri = cm.post_uri "
            "WHERE cm.cluster_id = ? "
            "ORDER BY COALESCE(p.created_at, cm.joined_at)",
            (cluster["cluster_id"],),
        ).fetchall()
        cluster["members"] = [dict(m) for m in members]
        yield cluster


def analyze_cluster(
    cluster: dict,
) -> dict[str, tuple[str, str, list[str]] | None]:
    """For each phase, find the earliest matching member.

    Returns {phase: (timestamp, post_uri, [matched_terms])} or {phase: None}.
    """
    phase_first: dict[str, tuple[str, str, list[str]] | None] = {
        p: None for p in ALL_PHASES
    }
    for m in cluster["members"]:
        ts = m.get("created_at") or m.get("joined_at")
        if not ts:
            continue
        matched = match_phases_for_post(m.get("text"), m.get("external_domain"))
        for phase, terms in matched.items():
            cur = phase_first[phase]
            if cur is None or ts < cur[0]:
                phase_first[phase] = (ts, m.get("post_uri") or "", terms)
    return phase_first


def main() -> None:
    p = argparse.ArgumentParser(
        description="Backtest drift-phase term emergence in Receipts clusters."
    )
    p.add_argument("--out", default="-",
                   help="CSV output path (default: stdout)")
    p.add_argument("--min-members", type=int, default=2,
                   help="Skip clusters with fewer than this many members (default 2)")
    p.add_argument("--cluster-types", nargs="*", default=None,
                   help="Restrict to specific cluster_types (e.g. url root headline)")
    p.add_argument("--limit", type=int, default=None,
                   help="Process only the first N clusters")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    conn = db.get_conn()
    out_fh = sys.stdout if args.out == "-" else open(args.out, "w", newline="")
    try:
        writer = csv.writer(out_fh)
        writer.writerow([
            "cluster_id", "cluster_type", "first_seen_at", "member_count",
            "first_object_event_at", "first_confirmation_at",
            "first_political_use_at", "first_conspiracy_at",
            "first_meta_discourse_at", "first_outrage_packaging_at",
            "first_wire_or_primary_source_at",
            "earliest_matching_phase_terms",
            "earliest_matching_post_uris",
            "notes",
        ])

        n = 0
        for cluster in iter_clusters(
            conn, args.min_members, args.cluster_types, args.limit
        ):
            n += 1
            phase_first = analyze_cluster(cluster)

            non_null = [(ph, v) for ph, v in phase_first.items() if v]
            if non_null:
                earliest_ts = min(v[0] for _, v in non_null)
                earliest_phase_terms: list[str] = []
                earliest_uris: list[str] = []
                for ph, v in non_null:
                    if v[0] == earliest_ts:
                        earliest_phase_terms.append(f"{ph}:{','.join(v[2])}")
                        if v[1] and v[1] not in earliest_uris:
                            earliest_uris.append(v[1])
            else:
                earliest_phase_terms = []
                earliest_uris = []

            def at(ph: str) -> str:
                v = phase_first[ph]
                return v[0] if v else ""

            writer.writerow([
                cluster["cluster_id"],
                cluster["cluster_type"],
                cluster["first_seen_at"],
                len(cluster["members"]),
                at("object_event"),
                at("confirmation"),
                at("political_use"),
                at("conspiracy"),
                at("meta_discourse"),
                at("outrage_packaging"),
                at("wire_or_primary_source"),
                "; ".join(earliest_phase_terms),
                "; ".join(earliest_uris),
                "",
            ])

        LOG.info("Processed %d clusters", n)
    finally:
        if out_fh is not sys.stdout:
            out_fh.close()
        conn.close()


if __name__ == "__main__":
    main()
