# Master plan — Instant Internet News → claim ledger (public surface: "Receipts")

Build document for pivot #2. Candidate origin: `specs/gaps/gap-spec-claim-ledger-pivot.md`.
This file supersedes that gap-spec as the executable plan.

## Context

IIN is a Bluesky signal desk (live at instantinternet.news). Pivot #1 (2026-06-10) turned "ranked
links" into "one source object per canonical URL." This is pivot #2: re-found the front page as a
**public claim ledger** — *"not what happened, what can be cited."* Direction is accepted
(operator, 2026-07-01/02); the gate is engineering, not ratification.

**Key finding:** IIN is *already* a structural machine. At edition-composition time
(`site.py::build_and_freeze_edition()`, right after `compact_dockets(items)`), every item already
carries the fields a no-NLP compile predicate needs — including `headline_basis`
("og_title" / "post_fallback" / "graph_note"), the exact seam to formalize. This pivot **hardens
what `headline_basis` already gestures at** into a typed ledger. No new ingestion, no NLP, no new
network fetches.

**Rhyme, don't depend** (like labelwatch↔agent_gov): borrow doctrine + object shapes from
`claimc` (Rust `compile/seal/score`, "stupid compiler," `E004_MISSING_SETTLEMENT`) and `claimdocs`
(`claim_mode/basis/adequacy/freshness`, fail-closed render). **No runtime dependency** — IIN gets
its own pure `claimdoc.py` + module-level vocabulary. See `vocabulary.md`.

## North star & promotion gate (governance)

Ship **dark/additive**. Default operating posture = **claims as a co-equal section** beside the
existing source-first wire desk. **Do NOT aim directly at homepage replacement.**

Promotion to homepage spine is *earned*, not scheduled — allowed only when live receipts justify
it: **meaningful compile rate**, a **useful negative-results surface**, **low defamation /
authority-confusion risk**, and clear evidence claim cards are more informative than the wire desk
as the primary entry point. Until then the wire desk stays the primary face.

## Naming (locked)

- **Public / nav surface:** `Receipts` (reuse the existing site identity — don't mint a new noun).
- **Internal concept / module:** `claim_ledger` / `claimdoc`.
- **Card object:** claim card (aka receipt card).
- **Explainer subtitle:** *"Claims with receipts, not verdicts."*
- Not user-facing: "The Ledger" (too institutional — implies authority the system disclaims).

## The compile predicate (v0)

An edition item **compiles** into a claimdoc iff ALL hold (structural only, never parses `text`):
1. **assertion present** — `text` non-empty (carried verbatim),
2. **external source present** — `canonical_url`/`external_uri` with `source_domain` NOT a platform domain (`domains.is_platform_domain`),
3. **custody resolved** — `canonical_url` + post `uri` + url_meta `fetch_status == 200` with `fetched_at`,
4. **settleable source class** — `source_class` ∈ {filing, regulation, paper, code, reporting, wire}.

`is_docket` bundles (empty text) → **skipped** (`compile_claim` returns `None`), not surfaced as failures.

**Claim modes:** `sourced` (primary source + basis resolved) and `reported` (reporting/wire +
basis resolved) are **strong/witnessing**; `carrier` (external link, basis unresolved / platform-
only → "title unverified") and `uncompiled` (no external source) are **non-witnessing** and land on
the negative surface. **Most viral posts won't compile — expected feature.**

## Phases (each independently commit-able and reversible)

- **Phase 0** — this doc + `vocabulary.md`. Commit. No code.
- **Phase 1** — pure `receipts_feed/claimdoc.py` (`compile_claim`, `seal_digest`, `_admissibility`) mirroring the `source_class.py` module idiom (constants + frozensets + pure funcs; no YAML — repo has no config loader). `tests/test_claimdoc.py` (pure, inline-dict fixtures per `tests/test_source_class.py`), incl. negatives (carrier/uncompiled/rejections/docket→None/seal-stability/fail-closed). Commit.
- **Phase 2** — additive `claimdocs` + `claim_rejections` tables (PK `(edition_id, post_uri)`; `claim_id` indexed) + prune parallel to `save_edition`'s 100-edition cap; `save_claimdocs`/`get_claimdocs_for_edition`. New `receipts_feed/claim_ledger.py::resolve_claim_basis(items, url_meta_by_url)` (stamp-only, no reorder/drop). Wire into `site.py` after `compact_dockets` under `config.CLAIM_LEDGER_ENABLED` (default off, try/except non-fatal). Commit.
- **Phase 3** — `_collect_claims` (strong modes) + `templates/_claim_card.html` fail-closed partial; co-equal "Receipts" section, existing sections/`_MAIN_SECTION_ORDER` untouched. Commit.
- **Phase 4** — `_collect_uncompiled` block (honest reason strings, structural copy — defamation shield) + `GET /debug/claims` audit endpoint mirroring `api.py::debug_top`. Commit.
- **Phase 5** — dated stated→receipted note on the `ROADMAP.md` no-NLP line; `/method` + `/about` copy ("admissibility not truth," "Truth: unknown," negative results). Commit.

### Later (named, gated — not now)
- Adequacy / human admission (claimdocs' layer) — editorial, YAGNI until wanted.
- `settle` step + richer admissibility; whether admissibility feeds ranking (kept separate in v0).
- Corroboration / contradiction across claims.
- Homepage-spine promotion — only via the promotion gate above.

## Cross-cutting guardrails
- No NLP, no truth judgment, no model certification — compile is stupid/structural.
- No runtime dependency on claimc/claimdocs — rhyme only.
- Additive & reversible — new module, new tables, new template, flag default off; unset env = full revert (tables left as harmless empty additive state).
- Publish guardrails: admissibility ≠ truth; card never a verdict about the subject; "Truth: unknown" structural, not decorative.
- Commit-only, no push. No deploy as part of this plan.

## Risks (mitigations baked into the phases)
1. `claimdocs` PK must be `(edition_id, post_uri)`, not `claim_id` (recurring seals collide otherwise).
2. Orphan pruning — mirror `save_edition`'s 100-edition prune for claimdocs/rejections.
3. Edition JSON back-compat — `item["claim"]` additive; all reads via `.get`; historical editions → empty Claims bucket; `edition_detail.html`/`archive.html` untouched.
4. Hero selection unaffected iff `resolve_claim_basis` is stamp-only. A story may be both hero and claim card in v0 — left independent, noted.
5. Docket cards — empty-text bundles skipped (`None`), not surfaced as failures.
6. Defamation shield — `admissibility` never labeled `score`/truth; "Truth: unknown." structural in the template.

## Verification
- Phase 1: `pytest tests/test_claimdoc.py` run bare (exit code is the verdict). Full `pytest tests/` stays green.
- Phase 2: locally `CLAIM_LEDGER_ENABLED=1`, run a rank + edition build, inspect the `claimdocs` table + `GET /debug/claims`; confirm flag off = zero writes, no `item["claim"]` key.
- Phase 3/4: `receipts-feed serve` locally; homepage with flag on/off — existing sections/wire/hero identical when off; with on, confirm fail-closed (`fetch_status != 200` → carrier "title unverified," never the strong claim); negative surface shows honest reasons.
- Regression: full `pytest tests/` green after each phase.
