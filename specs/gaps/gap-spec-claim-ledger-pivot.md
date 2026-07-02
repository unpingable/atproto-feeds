# Gap spec — Instant Internet News → public claim ledger (claimdoc/claimc)

**Status:** CANDIDATE — captured 2026-07-01. **Not authorization to build.** A handle
for review. Ratification gated on the open tensions below (esp. #1 and #2).

**Provenance:** user "Ideas" dump, 2026-07-01. This is pivot #2 — it sits on top of the
2026-06-10 source-first pivot (`TODO-source-first.md`), which already turned "ranked links"
into "one source object per canonical URL."

---

## The pivot in one line

Instant Internet News stops being **"what links are interesting?"** (curation, ranking)
and becomes **"what claims entered the room, with what custody?"** (a public claim ledger).

The current site already says the quiet part on `/about`: *"It does not verify factual
truth"* — a ranking instrument, not an oracle. Honest, but it caps the project as curation.
The pivot turns the existing **Receipts** rhetoric into an actual custody machine.

**Current shape:**

> post → URL → score → edition

**Claim-ledger shape:**

> post → extracted claim(s) → source/citation custody → compile result → settlement status → edition

The front page stops being story cards and becomes **claim cards**.

## Claim card anatomy

```
Claim:    "SEC may allow companies to end quarterly reporting."
Source:   SEC comment docket
Carrier:  Bluesky post by X
Status:   compiled · source-linked · not-settled · not-truth
Receipts: canonical URL, retrieved metadata, post URI, timestamp, digest
Score:    admissibility weight (NOT popularity, NOT truth)
```

## The claimc pipeline (per candidate item)

- **compile** — is there a coherent claim?
- **seal** — content-addressed claim + source refs.
- **settle** — source reachable / citation valid / carrier preserved.
- **score** — admissibility, not truth.

Editions render as four buckets:

- **Claims with receipts** (compiled + sealed + settled)
- **Uncompiled items** (no stable claim yet)
- **Relay / noise rejected**
- **Primary-source docket**

Rename "stories" → **claims** only when they compile. Everything else stays **items**.

## Admissibility, not truth — and negative results as a feature

The system is explicitly **not** a fact-checker. The honest public output is:

```
compile ✓   seal ✓   settle ✓   score 0.82
Truth: unknown.
```

That's a cleaner public story than "our AI thinks this is probably true." It also gives IIN
something most AI/news products lack: **negative results**. A trending item can honestly say:

```
Compilation failed
 · No stable claim extracted
 · Primary source missing
 · References circular
 · Carrier only
```

"There isn't a coherent claim here yet" is sometimes the most honest thing a system can say
about a viral story.

## Relationship to claimc / claimdocs — rhyme, don't depend

Per operator (2026-07-01): IIN should **borrow from** claimc and claimdocs, **not** call them as
a runtime backend — the way **labelwatch rhymes with agent_gov**: port the doctrine and the
object shapes, reimplement in IIN's own idiom against the live corpus, no shared process, no
release-state coupling.

- **`~/git/claimc`** (Rust) — a **claim compiler** / "loser detector." Does NOT read English,
  does NOT judge truth. Checks whether a claim has **declared its settlement apparatus** —
  custody, an external source, an executable score. Its load-bearing rule is the *same instinct*
  as IIN's no-NLP constraint: *"the compiler must be stupid… the second it reads English and
  decides, you've built a priest with a parser."* Borrowables: the `compile / seal / score`
  verbs and their **walls** (`compile pass ≠ true`, `seal ≠ settled`, `score ≠ authorized`);
  admissibility-not-truth; *"most high-status claims are not false — they're uncompiled."*
- **`~/git/claimdocs`** (Python) — "claims as data, docs that fail closed." Every edge carries
  one `claim_mode` and cites a `basis`; the renderer **refuses to render a strong claim whose
  basis it can't resolve**; **the vocabulary is yours** (BYO claim modes / basis kinds).
  Borrowables: `claim_mode / basis / adequacy / freshness`, fail-closed rendering, *"the model
  may propose the skeleton; it may not certify its own proposals."*

The corpus value is still bidirectional in spirit — social media is a near-perfect adversarial
benchmark (claims with no sources, sources with no claims, contradictions, corrections,
quote-post distortions, primary docs nobody read) — but as **doctrine cross-pollination**, not a
live coupling that makes IIN's front page hostage to claimc's build state.

## Slogans (load-bearing identity, keep verbatim)

- **"Instant Internet News: not what happened — what can be cited."**
- Bad version: "AI news summary from Bluesky." Death by sludge.
- Good version: "A public claim ledger for the internet's source-bearing discourse."

## Minimal viable pivot (additive, not rip-and-replace)

1. Keep aggregation as **ingestion only**.
2. Add `claimdoc` objects per edition (alongside existing editions — do not delete the wire desk).
3. Run `claimc` over each candidate (compile / seal / settle / score).
4. Render editions as the four buckets above.
5. Rename "stories" → "claims" only on compile.

---

## Open questions before ratifying (candidate — mostly not blockers)

1. **Register: stated vs receipted (NOT a collision).** The no-NLP line in `ROADMAP.md` is
   *stated* doctrine — operator-owned and amendable at will; being written in a repo makes it a
   note, not a receipt or a governance object. And it doesn't need breaking: it **agrees** with
   claimc's stupid-compiler rule. The pivot's job is to *receipt* the structural-only stance
   (enforce it in code + tests), not to quietly overwrite a stale ROADMAP line. Operating rule
   the operator set on 2026-07-01: **keep clear disposition on what's stated (doctrine,
   changeable) vs. what's receipted (enforced / evidenced).** When a stated constraint changes,
   amend it with a dated note; never let the old line masquerade as a receipt.

2. **The real crux: who authors `post → claimdoc`?** Both claimc AND the IIN constraint refuse
   to let a machine read English and decide. claimc compiles a claim doc that *already declares*
   its settlement apparatus; a Bluesky post doesn't arrive that way. So the load-bearing design
   choice is the **authoring** step, and the honest answer is **structural-only**: a post
   "declares a claim" when it *structurally carries the apparatus* — an external source link
   (docket / filing / paper / CourtListener), the assertion in-post, custody via the URL /
   quote / citation graph IIN already builds. No semantics. Consequence, and it's a feature:
   **most viral posts won't compile** (carrier only, no external source) — exactly the
   "negative results" surface. Uncompiled ≠ false; it's *unsettleable as posted*. This is the
   one genuine design decision to nail before a slice.

3. **Observatory-lineage guardrails.** "Admissibility not truth," receipts, custody, negative
   results — same doctrine family as driftwatch/labelwatch (detect-only, weather-never-verdict).
   But IIN **publishes to the public**, so the guardrails apply harder: the `score` must never
   read as truth-scoring, and a claim card must never become a verdict about the *subject* of
   the claim. The "Truth: unknown" framing is the defamation shield — keep it structural, not
   decorative.

4. **Scope: re-founding, not a feature.** It reframes the product identity in `ROADMAP.md` and
   `TODO-source-first.md`. Ratify the direction and pick one additive slice before touching
   code. Do not let it become a feature carnival.

## Ratification gate

Explicit operator go **plus** a chosen structural authoring rule for #2. First slice is additive
(`claimdoc` objects beside the existing wire desk), reversible, and does not remove the
source-first edition that already ships. **No runtime dependency on claimc/claimdocs — port
shapes only.**
