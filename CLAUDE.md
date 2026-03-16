# atproto-feeds

Bluesky custom feed generator + public signal desk at https://instantinternet.news

## Project structure

```
receipts_feed/
  config.py          — env-based configuration
  db.py              — SQLite: authors, posts, ranked_posts, cursors, feed_state
  timeutil.py        — UTC time utilities
  domains.py         — primary source + reporting domain bonus tables
  author_weights.py  — per-author editorial weight overrides
  graph.py           — seed graph bootstrap from trust source account
  ingest.py          — Jetstream websocket consumer (filters to seed graph)
  rank.py            — scoring function + composition rules + dampening
  hydrate.py         — post hydration via Bluesky public API + visibility gate
  api.py             — FastAPI: getFeedSkeleton, site routes, health, debug
  site.py            — public website routes (homepage, about, method, feed)
  publisher.py       — publish feed record to Bluesky
  cli.py             — CLI: serve, bootstrap, rank, publish, top
  templates/         — Jinja2 templates (broadsheet newspaper layout)
```

## Key accounts

- **Publisher account**: `instantinternet.news` (owns the feed record)
- **Trust graph source**: `neutral.zone` (follows/mutuals seed the candidate pool)
- App password stored at `~/git/claude/bsky/instantinternet.news`

## Deployment

- Runs on the same Linode VM as driftwatch/labelwatch
- Systemd service: `receipts-feed.service`
- Port: 8100
- Caddy reverse proxy: `instantinternet.news` -> localhost:8100
- Project dir on server: `/opt/receipts-feed`
- Service user: `receipts`
- Data: `/opt/receipts-feed/data/receipts.sqlite`

## Deploy workflow

```bash
rsync -av --exclude='.venv' --exclude='data/' --exclude='__pycache__' --exclude='.git' --exclude='.env' ./ root@instantinternet.news:/opt/receipts-feed/
ssh root@instantinternet.news 'systemctl restart receipts-feed'
```

## CLI commands

```bash
receipts-feed bootstrap   # seed graph from trust source
receipts-feed serve       # run API + consumer + ranker
receipts-feed rank        # one-shot ranking pass
receipts-feed publish     # publish feed record to Bluesky
receipts-feed top         # show top 20 ranked posts (debug)
```

## Architecture notes

- Account-centric feed: fixed graph from @neutral.zone, same ranking for everyone
- Selective ingest: only seed graph authors + high-value domain posts from firehose
- Two output faces from same engine: Bluesky feed (getFeedSkeleton) + public site
- Site uses official Bluesky embed visibility rules: !no-unauthenticated authors excluded
- Author weights are editorial overrides, documented in method page. Policy stack: volume dampener (automatic) + author weights (manual exceptions)
- Composition rules: max 2 posts/author, max 3/thread, dedupe links

## Don't trample

- Caddy config lives at `/home/jbeck/atproto/Caddyfile` on the server (mounted into Docker)
- Other services on same box: driftwatch (port 8422), labelwatch (port 8423), PDS (port 3000)
- The `.env` on the server contains the app password — don't overwrite it with rsync
