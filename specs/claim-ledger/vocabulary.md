# Claim-ledger vocabulary — shapes to rhyme with (not import)

IIN borrows doctrine and object *shapes* from two sibling projects. It does **not** call them at
runtime (no `claimc` binary, no `claimdocs` import). IIN reimplements the shapes in its own pure
`receipts_feed/claimdoc.py`, the way labelwatch rhymes with agent_gov. This file is the map.

## The two sources

- **claimc** (Rust "claim compiler" / "loser detector"): checks whether a claim has *declared a
  settlement apparatus* (custody + external source + score rule). The **stupid compiler** rule —
  *"the second it reads English and decides, you've built a priest with a parser"* — is the same
  instinct as IIN's no-NLP constraint. Verbs: `compile / seal / score`. Reject: `E004_MISSING_SETTLEMENT`.
- **claimdocs** (Python "claims as data, fail closed"): every edge carries one `claim_mode` and
  cites a `basis`; the renderer **refuses to render a strong claim whose basis it can't resolve**.
  Vocabulary: `claim_mode / basis / adequacy / freshness`. BYO vocabulary per project.

## Map: borrowed shape → IIN realization

| Borrowed shape | Source | IIN realization |
|---|---|---|
| `compile` (stupid, structural) | claimc | `compile_claim(item, url_meta)` — the 4-check predicate; never reads `text` meaning |
| `E004_MISSING_SETTLEMENT` | claimc | no external non-platform source = no "court" → `uncompiled` mode |
| `seal` (content-address) | claimc | `seal_digest = sha256[:16]` over (`text`, `canonical_url`, post `uri`, `fetch_status`, `fetched_at`, `source_domain`) |
| `score` ≠ truth / ≠ authorization | claimc | **admissibility** — a structural weight (source-class + basis + corroboration). Never named `score`. "Truth: unknown." |
| walls (`compile pass ≠ true`) | claimc | a claim card is never a verdict about the claim's *subject* |
| `claim_mode` (witnessing vs not) | claimdocs | `sourced` / `reported` (witnessing) · `carrier` / `uncompiled` (non-witnessing) |
| `basis` + `freshness` | claimdocs | url_metadata: `fetch_status==200` + `og_title` resolved = resolvable basis; stale/failed = not; freshness from `fetched_at` age |
| **fail-closed render** | claimdocs | strong mode renders ONLY if `basis_resolved`; else the carrier "title unverified" form |
| `adequacy` (human admission) | claimdocs | **deferred** to a later phase — editorial labor, YAGNI until wanted |

## The walls (must hold; encode as comments + tests)

```
compile pass   ≠  claim is true
compile pass   =  the post structurally carries a settleable apparatus (source + custody)
seal           ≠  claim settled  (only: this exact claim + custody snapshot, content-addressed)
admissibility  ≠  truth,  ≠ popularity,  ≠ authorization
```

The public output is honest by construction: **"Truth: unknown."** The `admissibility` field must
never be surfaced as `score` or truth; the "Truth: unknown" line is structural in the template, not
decorative. This is the defamation shield — the format is the protection.

## Vocabulary constants (IIN's own, declared in `claimdoc.py`)

```
CLAIM_MODE_SOURCED    = "sourced"      # primary source (filing/regulation/paper/code), basis resolved
CLAIM_MODE_REPORTED   = "reported"     # reporting/wire, basis resolved
CLAIM_MODE_CARRIER    = "carrier"      # external link but basis unresolved / platform-only
CLAIM_MODE_UNCOMPILED = "uncompiled"   # no external source (E004) → negative-results surface
STRONG_MODES = {sourced, reported}     # fail-closed render gate

BASIS_PRIMARY   = "primary_source"     # filing/regulation/paper/code
BASIS_REPORTING = "reporting"          # reporting/wire
BASIS_CARRIER   = "carrier_only"
BASIS_NONE      = "none"

# reject codes (claimc idiom) — recorded on the doc/rejection for audit
E001_NO_ASSERTION        # predicate #1 (empty text)  → Rejection
E004_MISSING_SETTLEMENT  # predicate #2 (no external source / platform-only) → uncompiled mode
E005_UNRESOLVED_BASIS    # predicate #3 (fetch != 200) → carrier mode
E006_UNSETTLEABLE_CLASS  # predicate #4 (unknown/graph_note) → Rejection

# settleable / primary source classes are IMPORTED from source_class.py, not re-declared
SETTLEABLE_CLASSES = {filing, regulation, paper, code, reporting, wire}
PRIMARY_CLASSES    = {filing, regulation, paper, code}
```

Note the mode/reject split: `carrier` and `uncompiled` are **valid modes** (they render on the
negative surface), not hard rejections. Only `E001` (no assertion) and `E006` (unsettleable class,
e.g. graph_note/unknown) produce a `Rejection`. `E004` is recorded as a code but the item is a
`uncompiled` ClaimDoc — *uncompiled ≠ false; it's unsettleable as posted.*
