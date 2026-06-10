"""CLI entry point for receipts feed operations."""

import argparse
import asyncio
import logging
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(prog="receipts-feed")
    sub = parser.add_subparsers(dest="cmd")

    # serve
    sub.add_parser("serve", help="Run the feed API server + consumer + ranker")

    # bootstrap
    sub.add_parser("bootstrap", help="Bootstrap seed graph from trust source")

    # rank (one-shot)
    sub.add_parser("rank", help="Run a single ranking pass")

    # publish
    pub = sub.add_parser("publish", help="Publish feed record to Bluesky")
    pub.add_argument("--name", default="receipts")
    pub.add_argument("--display-name", default="Receipts")
    pub.add_argument("--description", default="Original, source-bearing, graph-adjacent posts. Less repost sludge. More people showing their work.")

    # refresh
    sub.add_parser("refresh-graph", help="Refresh seed graph from trust source")

    # debug
    sub.add_parser("top", help="Show top ranked posts (debug)")

    # semantic feed health check (cron-friendly; nonzero exit on degraded/failed)
    health_p = sub.add_parser(
        "health",
        help="Semantic feed health (alive + advancing + consumer-useful)",
    )
    health_p.add_argument(
        "--no-appview-probe", action="store_true",
        help="Skip the external public.api.bsky.app AppView resolution probe",
    )
    health_p.add_argument(
        "--sample-size", type=int, default=5,
        help="Number of skeleton URIs to AppView-probe (default 5)",
    )
    health_p.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit the receipt as JSON to stdout",
    )

    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn
        from . import config
        uvicorn.run(
            "receipts_feed.api:app",
            host="0.0.0.0",
            port=config.FEED_SERVICE_PORT,
            log_level="info",
        )

    elif args.cmd == "bootstrap":
        from .graph import bootstrap_graph
        result = bootstrap_graph()
        print(f"Graph bootstrapped: {result}")

    elif args.cmd == "refresh-graph":
        from . import db
        from .graph import refresh_graph
        db.init_db()
        result = refresh_graph()
        print(f"Graph refreshed: {result}")

    elif args.cmd == "rank":
        from . import db
        from .rank import run_rank
        db.init_db()
        run_rank()

    elif args.cmd == "publish":
        from .publisher import publish_feed
        result = publish_feed(
            feed_name=args.name,
            display_name=args.display_name,
            description=args.description,
        )
        print(f"Published: {result}")

    elif args.cmd == "health":
        import json as _json
        from . import db, health
        db.init_db()
        # consumer=None: the CLI runs out-of-process from the live service,
        # so the in-process checks (queue backlog, drain progress, drop
        # rate) report verdict=skipped. The DB-driven checks (cursor age,
        # newest skeleton item, renderable ratio) and the AppView probe
        # cover what the CLI can see externally.
        receipt = health.compute_health(
            consumer=None,
            probe_appview=not args.no_appview_probe,
            skeleton_sample_size=args.sample_size,
        )
        if args.json_output:
            print(_json.dumps(receipt, indent=2))
        else:
            print(f"=== {receipt['receipt_kind']} ===")
            print(f"verdict          : {receipt['verdict']}")
            print(f"generated_at     : {receipt['generated_at']}")
            print(f"skeleton_size    : {receipt['skeleton_size']}")
            print(f"probe_appview    : {receipt['probe_appview']}")
            print()
            print(f"{'check':<36} {'verdict':<8} value")
            print(f"{'-'*36} {'-'*8} {'-'*20}")
            for c in receipt["checks"]:
                val = c.get("value")
                print(f"{c['name']:<36} {c['verdict']:<8} {val}")
                if c.get("note"):
                    print(f"  · {c['note']}")
            print()
            print("rationale:")
            for r in receipt["rationale"]:
                print(f"  - {r}")
        if receipt["verdict"] in ("degraded", "failed"):
            sys.exit(2)

    elif args.cmd == "top":
        from . import db
        db.init_db()
        ranked = db.get_ranked_posts("receipts", limit=20)
        if not ranked:
            print("No ranked posts yet.")
        else:
            for i, item in enumerate(ranked, 1):
                print(f"{i:3d}. score={item['score']:.2f} reasons={item['reasons']}")
                print(f"     {item['uri']}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
