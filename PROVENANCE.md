# Provenance

This project is human-directed and AI-assisted. Final design authority,
acceptance criteria, and editorial control rest with the human author.
AI contributions were material and are categorized below by function.

## Human authorship

The author defined the project direction, feed concept ("Receipts"),
editorial policy, scoring philosophy, domain bonus list, author weight
overrides, deployment targets, and the decision to build a publication-style
site rather than a generic feed viewer. The broadsheet newspaper layout,
edition-based publishing model, and DESK page separation were author-directed
design decisions.

## AI-assisted collaboration

### Architectural design

Lead collaboration: ChatGPT (OpenAI). Heavy involvement in the feed concept
development, scoring model design, candidate pool strategy, composition
rules, account-centric (not personalized) architecture, consent/visibility
policy, edition snapshot model, temporal clock hierarchy (fast/medium/slow),
and the "second face on the same engine" principle for the public site.

### Implementation, templates, and deployment

Lead collaboration: Claude (Anthropic) via Claude Code. Implementation of
the full stack: Jetstream consumer (borrowing patterns from driftwatch),
SQLite schema, ranking engine, FastAPI endpoints, Jinja2 templates, Bluesky
feed publishing, systemd service configuration, Caddy routing, and
deployment to production. Broadsheet homepage layout, hero eligibility
logic, headline cleaning, domain normalization, visibility gating, and
edition freeze system.

## Development context

This project borrows architectural patterns from its siblings
[driftwatch](https://github.com/unpingable/atproto-driftwatch) and
[labelwatch](https://github.com/unpingable/labelwatch), particularly
the Jetstream consumer, SQLite/WAL setup, and time utility modules.

The scoring model, domain bonus tables, and author weight overrides are
editorial decisions documented in the public Method page. They represent
taste, not claims of objectivity.

## Provenance basis and limits

This document is a functional attribution record. It is not a complete
forensic account of all contributions.

AI contributions to design critique, rejected alternatives, and policy
decisions may not appear in repository artifacts. Model names are recorded
at the platform level; exact versions may vary across sessions.

No exact proportional attribution is claimed. "Footguns avoided" and
"ideas that didn't ship" are real contributions that leave no artifact.

---

This document reflects the project state as of 2026-03-16 and may be revised.
