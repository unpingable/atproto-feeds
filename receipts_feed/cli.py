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

    # debug
    sub.add_parser("top", help="Show top ranked posts (debug)")

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
