# atproto-feeds

*A source-first Bluesky signal desk — custom feed generator + public edition at [instantinternet.news](https://instantinternet.news).*

## Why this exists

Most Bluesky feeds optimize for engagement, topic keywords, or social
proximity. These degenerate into repost sludge, keyword cemeteries, or
clique amplifiers. This project takes a different approach: rank for
**how a post behaves**, not just what noun it contains.

**Receipts** is a structural feed. It rewards originality, primary sources,
and graph proximity. It penalizes reposts, floodposting, and screenshot
discourse. The ranking is inspectable, the editorial overrides are
documented, and the site admits it has a point of view instead of
pretending to be weather.

## What it does

**Ingests posts** via Jetstream WebSocket, filtered to a seed graph of
~3,800 authors (follows, mutuals, followers of the trust source account)
plus posts linking to high-value primary-source domains.

**Ranks candidates** every 2 minutes with a scoring model that combines
graph affinity, originality, evidence quality, substance, and freshness.
Volume dampening and per-author editorial weights handle prolific posters.
Composition rules prevent any single author or thread from dominating.

**Publishes a Bluesky feed** via `getFeedSkeleton` — live, post-level
ranking that anyone can pin in their Bluesky tabs.

**Publishes a site** at [instantinternet.news](https://instantinternet.news) —
a broadsheet-style front page frozen as an edition every 15 minutes. Three
columns: Sources, From the Graph, and a sidebar with edition stats and wire
briefs. Hero selection, headline cleaning, and domain normalization give the
page editorial structure.

**Respects visibility preferences.** The public site excludes authors who
have opted out of logged-out visibility (`!no-unauthenticated`). The feed
and site are intentionally not identical — one is a signed-in instrument,
the other is a public edition.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env with credentials and hostname

# Bootstrap seed graph
receipts-feed bootstrap

# Run everything (API + consumer + ranker + edition freeze)
receipts-feed serve

# One-shot commands
receipts-feed rank       # Single ranking pass
receipts-feed publish    # Publish feed record to Bluesky
receipts-feed top        # Debug: show top 20 ranked posts
```

## Architecture

Three clocks, one database, two output faces:

```
Jetstream (WebSocket)
    → consumer (continuous, filtered to seed graph)
        → posts table (SQLite, WAL mode)

Ranker (every 2 min)
    → score candidates
    → composition rules (author cap, thread cap, link dedup)
    → ranked_posts table

Edition freeze (every 15 min)
    → hydrate from public API
    → visibility gate (!no-unauthenticated)
    → hero selection
    → headline cleaning
    → editions table (frozen snapshots)

Output faces:
    → Bluesky feed: getFeedSkeleton (live ranked_posts)
    → Public site: broadsheet homepage (frozen editions)
```

### Site pages

| Page | Purpose |
|------|---------|
| `/` | Broadsheet homepage: hero + Sources / Graph / Wire columns |
| `/about` | What it rewards, penalizes, and doesn't claim |
| `/method` | Scoring components, composition rules, known biases |
| `/feed` | Bluesky feed landing with sample items |
| `/desk` | House posts from operator accounts (separate from ranking) |

### API endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /xrpc/app.bsky.feed.getFeedSkeleton` | Bluesky feed skeleton |
| `GET /.well-known/did.json` | DID document for feed service |
| `GET /health` | Health check |
| `GET /debug/top` | Top ranked posts with reasons |
| `GET /debug/stats` | Post/author/edition counts |

## Scoring model

Each candidate post is scored by summing:

| Component | Weight | Purpose |
|-----------|--------|---------|
| **Graph affinity** | mutual +4, followed +2, trusted_list +3, follower +1 | Anti-garbage prior |
| **Originality** | original +3, repost -6, quote -1 | Reward creation |
| **Evidence** | external link +2, primary-source domain +2 to +3.5 | Reward receipts |
| **Substance** | text length band, facets, links (capped) | Reward effort |
| **Freshness** | ~12h half-life exponential decay | Recency |

Penalties and dampening:

| Mechanism | Effect |
|-----------|--------|
| **Flood control** | Ramping penalty after 10 posts/day, capped at -5 |
| **Volume dampener** | Diminishing returns multiplier for prolific authors |
| **Author weights** | Manual editorial overrides for known edge cases |
| **Image stub** | -1.5 for image-only posts with minimal text |
| **Platform domains** | bsky.app, twitter.com, etc. don't count as sources |

## Domain bonuses

Transparent and editable. Primary sources (+2.0 to +3.5): courtlistener.com,
supremecourt.gov, congress.gov, sec.gov, arxiv.org, pubmed, github.com.
Reporting outlets (+1.0 to +2.0): reuters.com, apnews.com, propublica.org,
404media.co. Full list in `receipts_feed/domains.py`.

## Composition rules

After scoring, the ranked list is filtered:

- Maximum 2 posts per author per page
- Maximum 3 posts per root thread
- Duplicate links collapsed to the top 2

Without these, the feed becomes one person having a day.

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `FEED_PUBLISHER_HANDLE` | `instantinternet.news` | Bluesky account owning the feed |
| `TRUST_GRAPH_HANDLE` | `neutral.zone` | Account whose follows/mutuals seed the graph |
| `FEED_SERVICE_HOSTNAME` | — | Public hostname for `did:web` |
| `RANK_INTERVAL_SECONDS` | `120` | Ranking rebuild interval |
| `EDITION_INTERVAL_SECONDS` | `900` | Site edition freeze interval |
| `MAX_FEED_AGE_HOURS` | `24` | Post retention window |

## Deployment

Runs as a systemd service on the same Linode VM as driftwatch and labelwatch.

```bash
# Deploy
rsync -av --exclude='.venv' --exclude='data/' --exclude='__pycache__' \
  --exclude='.git' --exclude='.env' ./ root@instantinternet.news:/opt/receipts-feed/
ssh root@instantinternet.news 'systemctl restart receipts-feed'
```

- Service: `receipts-feed.service` (port 8100)
- Reverse proxy: Caddy (`instantinternet.news` → localhost:8100)
- Data: `/opt/receipts-feed/data/receipts.sqlite`

## Observatory family

**atproto-feeds** watches **discourse quality** — what's worth reading, who's
showing their work, what sources are being cited.

[Driftwatch](https://github.com/unpingable/atproto-driftwatch) watches
**information drift** — do claims persist, mutate, resist correction?

[Labelwatch](https://github.com/unpingable/labelwatch) watches **labeler
behavior** — are labelers consistent, accountable, governed?

Same family, different instruments.

## Design constraints

- Account-centric, not personalized: same ranking for everyone
- Structural feed: ranks by post behavior, not topic keywords
- Editorial overrides are documented, not hidden
- No ML classifiers, no LLM-in-the-loop, no engagement optimization
- Observation and curation only — does not moderate content or judge truth
- Official Bluesky embed visibility rules respected on public site

## Opt out

Want your posts excluded from this project? DM
[@instantinternet.news](https://bsky.app/profile/instantinternet.news)
with "opt out." Exclusion applies to your DID across all surfaces: feed,
site, and archives. DM "opt in" to reverse it.

## Project docs

- [PROVENANCE](PROVENANCE.md) — AI collaboration attribution
- [ROADMAP](ROADMAP.md) — Clustering, editions, story states, and what's next
- [CLAUDE.md](CLAUDE.md) — Development context for AI assistants

## License

Unless otherwise noted, this repository is licensed under MIT OR Apache-2.0,
at your option. Contributions are accepted under the same terms.
