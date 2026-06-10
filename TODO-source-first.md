# TODO — source-first follow-ups

Captured from chatty + fable's review of the live wire-desk edition,
2026-06-10. Pivot landed; remaining work is extraction hygiene, bot
containment, taxonomy seam, and the product framing the wire desk now
makes possible.

Status legend: `[ ]` open · `[~]` partial · `[x]` done

---

## A. Extraction hygiene (one-line patch list)

The data layer works. These are the cards where it doesn't yet.

- [ ] **OG fetch failure ≠ permission to promote skeet to headline.**
      Atlantic / Axios / DocumentCloud cards still skeet-headlined when
      the publisher blocks our UA. The fallback path currently uses
      `display_headline` (post text) when og_title is missing. Better:
      try Bluesky's stored link-card title before falling through to
      post text, and visibly mark the card as "title unverified" when
      we lack a publisher-confirmed headline. Don't let the skeet wear
      the headline.

- [ ] **Federal Register / SEC / CourtListener domain-specific handlers.**
      Live edition shows `Federal Register :: Request Access` /
      `unblock.federalregister.gov` because our fetcher hit the access
      interstitial. For known publishers with anti-bot middleware,
      prefer:
      1. The submitted URL (pre-redirect)
      2. A domain-specific title extractor (FR doc slug → human title,
         SEC comment doc number, CL docket #)
      3. Hard-block the `unblock.*` interstitial domain class from
         landing in url_metadata.final_url
      Implementation: a small `handlers/` dict keyed by domain.

- [ ] **Counter: rename "url clusters" or fix the metric.**
      Homepage says 27 stories / 1 URL cluster. Reads broken. Either
      rename to "multi-post URL clusters" (current semantics — only
      counts 2+ post groups) or recompute as "stories with a canonical
      URL" (which is the more useful number now).

- [ ] **Demote scanner-like authors harder.**
      `@vnclotto`'s ASN dumps still occupy two consecutive From the
      Graph slots. Add a `scanner_like_author` detector:
      ```
      high_link_or_IP_density
      + repeated ASN/port/protocol vocabulary
      + low conversational variance
      + high daily volume
      + narrow topic entropy
      ```
      → author_weight *= 0.2, suppress from This Edition / Fading /
      Wire, allow on story page. Quick win: add `@vnclotto` to
      `author_weights.py` with weight 0.2 as a stop-gap.

- [ ] **Method page still describes pre-pivot doctrine.**
      Says "one lead post per cluster." Update to: homepage shows one
      *source object* per canonical URL; representative post becomes
      quote/dek; story page collects the full commentary thread.

- [x] **About: URL metadata carveout doctrine line.**
      Shipped in `b24930e`. Verified on the live site.

---

## B. Taxonomy seam — sections vs desks vs operator

> Sections describe **what the object is**.
> Desks describe **why you care**.
> Tags describe **where it came from**.

The current nav mixes all three axes ("Home / About / Method / Feed /
Desk / Watch / Business / Sports / Weather"). Split them.

### Source sections (homepage spine, keep)

- [x] Filings & Regulation
- [x] Papers
- [x] Reporting
- [x] From the Graph
- [x] Below the Fold
- [ ] **Code & Repos** — promote when volume justifies
- [ ] **Data & Dockets** — separate from Filings if FR/SEC overload it
- [ ] **Media / Video** — YouTube + video-essay content

### Desks (editorial lenses, restructure)

- [ ] **Business Desk** — *tighten*. Currently leaks (Boston.com reunion
      trolling, Maricopa Recorder DocumentCloud). Re-scope to:
      markets, firms, labor, regulation, platforms, procurement,
      pricing power, AI/search manipulation, fraud, institutional
      incentives.
- [ ] **Material** (rename from Weather) — climate/energy/grid/insurance/
      food/disaster/infrastructure. Keep the tagline "Where abstraction
      stops working" — best line on the site. AP solar/coal lives here
      properly.
- [ ] **Sports & Wagers** — gambling, prediction markets, stadium deals,
      athlete labor, media rights, ticketing. Hide from nav when empty,
      or render "Dormant this edition" compactly.
- [ ] **State & Courts** — maybe. Filings already serve this; defer.

### Operator surface (utility, rename)

- [ ] **Watchlist** (rename from Watch) — "operator lens, not a news
      category".
- [x] Feed (unchanged)
- [x] Method (needs pivot update per A above)
- [x] About (URL-metadata doctrine line shipped)

### Nav rule

- [ ] Hide empty desks from primary nav unless they're identity-bearing.
- [ ] Visually distinguish source sections / desks / operator (three
      groups, not one flat row).

---

## C. Product framing — Bloomberg terminal for cursed substrate

The win condition the pivot makes possible:

> **I should not have to enter the feed to know what the feed discovered.**

The cursed place does three things — discovery (good), context (chaotic),
consumption (spiritually radioactive). InstantInternet steals discovery
+ context and leaves consumption in the dumpster.

The page should feel like: *"Here is what the cursed place found, stripped
of its behavioral infection layer."* Social-media exhaust capture, not
a quirky news site.

### Six questions a Bloomberg-shaped reader has

1. What did the graph surface?
2. What are the actual source objects?
3. Who is reacting, and are they worth weighting?
4. What changed since the last edition?
5. What is fading?
6. What is machine/bot sludge?
7. Where is the primary source?

### High-utility feature list

- [x] Since-last-edition diff (basic version shipping; tighten copy)
- [~] Primary-source-first cards (in flight via A above)
- [x] "Why this surfaced" receipts (existing tags layer)
- [ ] **Watchlist lens** — operator-curated, doesn't boost main ranking
- [x] Fading / cooled-off stories (shipping)
- [ ] **Bot/slop dampening** (see scanner-author item in A)
- [ ] **Desk filters** — work the desks as filtered workspaces, not
      blog categories. Treat each as a saved query against the same
      ranking engine.
- [~] Story pages as discussion/commentary archives (basic page lives at
      `/story/<cluster_id>`; needs the same source-first render polish)
- [x] No infinite scroll
- [x] No engagement affordances

### Non-negotiables

The absence of dopamine machinery is the product, not incidental. The
cursed place says *"stay here and keep reacting."* This should say
*"here's the signal — go make coffee or commit crimes against CSS."*

---

## D. Visual judgment forks (need user eye in browser)

- [ ] **Display face**: current `Iowan Old Style` serif for headlines.
      Push harder toward wire-desk thesis by going **monospace for
      headlines** too? Trade-off: more terminal, less newspaper warmth.
      Decide in browser.
- [ ] **Light-paper variant**: dark-on-near-black is "desk", but a
      light paper variant might better suit Reporting-heavy editions.
      Punt unless requested.

---

## Pointer back to the rest of the work

Other open thread the user wants to return to: **driftwatch DuckDB
Phase 3** — the snapshot writer per `gap-spec-facts-export-duckdb-snapshot-001.md`
in the driftwatch repo. That spec was ratified `7faeee3` and is ready
to implement; this TODO is the receipts-feed branch of attention.
