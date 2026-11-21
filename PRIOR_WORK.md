# Prior work disclosure

Pakkaa reuses parts of an earlier project by the same author. This file says exactly what came from it and
what was built for Pakkaa, so judges can tell the two apart. It is updated after every milestone.

## Source

| | |
|---|---|
| Project | **PaidSafe** (MIT licence, same author) |
| Repository | https://github.com/Chiru595/paidsafe |
| Pinned commit | `1ca1a53c4a57f5deb526daaf1fb3408348f656d6` (7 Nov 2026, 23:13 IST) |
| Built for | *Flip the Switch* hackathon, 7 Nov 2026 (Switch / Mattermost, not Nexconn) |
| Vendored copy | [`legacy/paidsafe/`](legacy/paidsafe/), unchanged except that its `.git` folder was removed. Its 59 tests pass. |

PaidSafe was a single-room team chat on Switch (Mattermost) where two LLM debaters argued "chase or wait" on a
late invoice, a code-only **Ledger** posted cited fact cards and fact-checked every post, and a code-only
**Warden** enforced freeze, a circuit breaker and two approvals bound to a verified sender id. It had no
payments, no web app, no database, no negotiation and no money-out flow.

## What is reused, and how

"Ported" means the logic is copied into `services/api/app/` and adapted (storage, identity and money types);
the original file is named so it can be diffed.

| PaidSafe module | What it does | Pakkaa destination | How it is reused |
|---|---|---|---|
| `rules/india_msmed.yaml`, `rules/uk_late_payment.yaml` | Rule packs with `source_url` and `as_of` on every value | `app/rules/packs/` | **Copied as is.** New packs (rate bands, risk thresholds) follow the same format |
| `tools/interest.py` | MSMED compound interest (monthly rests, 3x Bank Rate, 45-day cap), UK late-payment interest, stale-rate refusal | `app/rules/interest.py` | **Ported.** Paise integers at the boundary instead of floats; rule loader points at the new pack folder |
| `tools/factcheck.py` | Deterministic check that every number in a text cites a fact card and matches it (lakh/crore/k formats, ignores dates and ids) | `app/agents/ledger/factcheck.py` | **Ported almost verbatim.** Used on copilot replies, approver briefs and drafts |
| `tools/facts.py` (`Fact` dataclass, rendering) | Fact cards carrying the numbers they state | `app/agents/ledger/facts.py` | **`Fact` type reused.** The MegaMart invoice card builders are replaced by deal, rate-band and payee cards |
| `tools/evidence.py` | Every amount in an outgoing draft must trace to a fact card; facts hash | `app/agents/ledger/evidence.py` | **Ported.** Applied to Deal Room drafts and Pay Lane card amounts |
| `tools/common.py` (freeze, two-key resume, rejection counter) and `tools/warden_core.py` | Kill switch, two-key unfreeze, circuit breaker, approvals bound to verified identity, impostor rejection | `app/domain/approvals.py`, `app/security/freeze.py` | **Logic ported; mechanism changed.** Identity is an authenticated session user plus server-side membership role, not a Switch sender id. State is in Postgres, not SQLite. Chat-text commands ("approve B") are **not** ported: in Pakkaa approvals can only happen through authenticated API calls, never through chat text |
| `tools/common.py` `audit()` | Append-only JSON-lines audit log | `app/security/audit.py` | **Replaced.** Pakkaa's audit log is hash-chained (`prev_hash`) in Postgres with a verify and export endpoint |
| `tools/memory.py` | Per-client playbook: decision, then the real outcome, fed back as fact card F7 | `app/memory/` | **Concept reused.** Becomes `MemoryItem(kind=playbook/counterparty)` in Postgres with pgvector, scoped per org |
| `agents/instructions/guardrails.md`, `relationship.md`, `cash.md` | Scope, evidence, authority and honesty rules for LLM agents; debater personas | `app/agents/prompts/` | **Adapted** for the copilot, scribe and (Sunday) the chase-or-wait debaters |
| `tests/test_interest.py`, `test_factcheck.py`, `test_evidence.py`, parts of `test_warden.py` | Known-answer and safety tests | `services/api/tests/legacy/` | **Ported** with the modules above |

## Not reused

- `agents/switch_client.py`, `agents/run.py`, `scripts/*`, `switch/paidsafe.template.yaml`, `config/roles.json`:
  Switch and Mattermost transport. Pakkaa runs on Nexconn.
- `tools/send_client_message.py`: simulated send to a local folder. Pakkaa sends through Nexconn and pays
  through a `PaymentProvider`.
- `data/*`: MegaMart synthetic data. Pakkaa has its own seed script.
- `demo/video/*`: may be reused later for the Nexconn demo video; this table will say so if it is.

## Built new for Pakkaa (21 Nov 2026 onward)

Everything else, including:

- The product: Deal Room (money in) and Pay Lane (money out) on Nexconn.
- Web app (React, Vite, TypeScript, Tailwind) and API (FastAPI, SQLAlchemy, Alembic, Postgres with pgvector).
- OTP login, orgs, roles, guest join links, Nexconn token issuing, rooms and cards.
- Live copilot, Scribe term extraction, pre-send check, fair-price meter, clause explainer, lock review.
- Term-sheet lock with OTP and SHA-256, payment links, payouts with idempotency keys.
- Pay Lane card builder, money-talk detector, the risk engine's payment rules, amount-band approval policy,
  approver brief, verify call with challenge questions, Ask Pakkaa.
- Learning from signals, the hash-chained audit log, red-team tests, deployment.

The commit history from the first commit onward is the record of what was built and when.

## AI disclosure

Claude Code (Claude Opus 5.5) helps write the code, tests and docs. The author reviews all of it and can
explain all of it.
