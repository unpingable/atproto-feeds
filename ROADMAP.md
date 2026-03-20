# Roadmap

Current state: live feed + frozen-edition site with broadsheet layout.
What follows is the path from "ranked posts arranged nicely" to
"story clusters with temporal structure."

## Near-term

### Edition identity

Give editions numbers and timestamps so the archive path becomes natural.
Edition metadata on the homepage ("Edition 042 / Published 2:15 PM").
Log edition diffs: what entered, left, stayed, moved.

### DM opt-out

Project-level exclusion via DM to `@instantinternet.news`. Applies to
feed + site + archives. Stored by DID. Documented publicly.

### Cluster support (Phase 1: exact keys only)

Three cluster types, in precedence order:

1. **URL cluster** — canonical external URL (strip utm, fragments, normalize host)
2. **Root-thread cluster** — reply tree / quote chain grouped by root_uri
3. **Singleton fallback** — unclustered posts

Tables: `story_clusters`, `cluster_members`, `cluster_windows`, `edition_items`.

Feed stays post-level. Site homepage becomes cluster-level.

### Cluster scoring

```
cluster_score = (
    lead_post_score * 0.45
    + corroboration * 0.15
    + author_diversity * 0.10
    + source_quality * 0.10
    + persistence * 0.10
    + acceleration * 0.10
    - redundancy_penalty
    - single_author_penalty
)
```

### Story states

Tiny state machine: emerging → active → dominant → persistent → fading.
Transitions based on volume, corroboration, source quality, graph
diversity, persistence across editions.

### Homepage composition from clusters

- **Hero**: highest hero-eligible cluster (not just highest score)
- **Sources column**: URL clusters with strong source/reporting domains
- **From the Graph**: root/singleton discourse clusters, graph-heavy originals
- **Wire**: emerging clusters, fading clusters, briefs

## Medium-term

### Phase 2: conservative title/domain fallback clustering

Only after seeing where exact keys miss obvious duplicates. Same domain +
similar cleaned headline title, strict threshold.

### Story packages

Not just "related posts" but structured packages:
- Lead witness
- 2–3 corroborating signals
- One graph reaction
- Primary source / reporting link

### State labels on the site

Dry, functional: `emerging`, `active`, `persistent`, `fading`,
`document drop`, `outsider break-in`.

### "What changed since last edition"

Tiny box: new / rising / fading / gone. Makes the 15-minute cadence
legible.

### German reading page (DE / Lesen)

Filtered lens for German-language posts. Same ranking/anti-sludge rules,
filter to `langs` containing `de`. Possibly biased toward German reporting
domains (tagesschau.de, spiegel.de, zeit.de, etc). Could be a hidden
subpage for personal use or a public section. Useful for language practice
with curated signal instead of raw firehose chaos.

### BUSINESS page

Filtered lens over the same engine for institutional/business-system stories.
Not generic finance news — business as infrastructure and conflict:
software wars, workflow captivity, pricing power, labor fights, market
structure, AI replacement theater, vendor/compliance/data moats.

Bias toward: reporting/primary source links, enterprise/finance/market
domains, clusters involving firms/products/regulation/labor.

Possible subsections: Workflow Wars, Moats & Markets, Labor & Management,
AI Replacement Theater, Policy/Regulation.

Same architecture as DESK/WATCH — separate lens, same substrate.

### Cluster pages

Click a story → lead post, supporting posts, source links, timeline
across editions, domain mix, graph vs outsider split.

### Edition character

One-line summaries: "Document-heavy edition", "Burst edition",
"One story dominating the room."

### Second feed algorithm

- `receipts-live` — rolling, fresher, post-level
- `receipts-edition` — cluster-level, more composed

## Long-term

### Phase 3: cluster-state-aware composition

Hero from dominant clusters, wire from emerging/fading, source character
reporting (primary sources vs reporting vs platform chatter).

### Desk annotations

Occasional editor notes on clusters, used sparingly. "This cluster is
mostly one event refracted through several repost-heavy accounts; the
underlying source is thinner than the heat suggests."

### Archive

Daily snapshots, browsable by date. Edition history with diffs.

## Known issues

### Source-bot dominance (addressed)
Previously the main quality problem. Now handled by three layers:
- Post-level relay penalty for low-commentary link posts
- Account-level stink score (7-day rolling behavior analysis)
- Outsider relay penalty for prolific domain-specific bots
- Cluster representative selection preferring human curators over relays

Covered by 13 regression tests. In cooling-off period — only reopen if
new bot subtypes evade the current model or legitimate curators get
unfairly buried.

## Design constraints (persistent)

- Feed ranks posts. Homepage ranks clusters.
- No embeddings, no NLP entity extraction, no fuzzy semantic clustering
  beyond conservative exact/near-exact keys.
- `bsky.app` is not a source.
- One author is not a cluster.
- Many posts linking one URL are not many facts.
- Editorial overrides are documented, not hidden.
