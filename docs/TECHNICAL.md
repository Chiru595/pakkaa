# Pakkaa: technical overview

## Architecture

```
 Phone / browser (React + Nexconn Web Chat SDK)
   │  ▲ live messages and cards over the Nexconn connection
   │  │
   │  └──────────────── Nexconn (Singapore) ◄──────────────┐
   │ HTTPS /api/*                                           │ server API: rooms, members,
   ▼                                                        │ messages "as the user", bot cards
 FastAPI on Vercel (Singapore) ─────────────────────────────┘
   │  every message: check membership → store → send via Nexconn → background AI
   ├── Postgres 16 + pgvector on Neon (Singapore): source of truth, memory vectors, audit chain
   └── OpenAI (four model lanes + embeddings): advice only, never decisions
```

- **One source of truth.** Our database holds rooms, messages, term sheets, cards, approvals and the audit log.
  Nexconn is the real-time transport: our API posts every message through Nexconn's server API, so a browser can
  never write straight into a room.
- **AI advises, code decides.** Every money decision (sign, approve, pay, freeze, roles) is plain Python with role
  checks, state checks and an audit entry. No model output can trigger one.
- **Same app everywhere.** Vercel Services runs the Vite web app and the FastAPI app as one deployment; `/api/*` goes
  to FastAPI and everything else to the web app. Both run in Singapore, next to Nexconn and the database.

## Tech stack per feature

| Feature | How it works | Tech |
|---|---|---|
| Phone login | 6-digit OTP, HMAC-hashed with a server pepper, 5-minute expiry, 5 attempts then lockout, resend limits; Indian mobile numbers only | FastAPI, `phonenumbers`, Postgres, HMAC-SHA256 |
| Sessions | 15-minute access JWT; refresh token in an httpOnly cookie that rotates on each use; reusing an old token signs out the whole session family | PyJWT (HS256), SQLAlchemy |
| Workspaces and roles | Owner, finance, member, client guest, vendor guest; other workspaces get 404 so they cannot even confirm a room exists | FastAPI dependencies, Postgres |
| Rooms and chat | Each room is a Nexconn group; messages go through our API, are stored, then delivered live by Nexconn | Nexconn server API (signed requests) + Web Chat SDK `@nexconn/chat` |
| Cards in chat | Term sheets, payment cards and notices are a custom Nexconn message type `PK:Card` posted by the Pakkaa bot; private notices use directed messages only one person receives | Nexconn custom message type, directed messages |
| Guest invites | Single-use links that expire in 72 hours; the token is stored only as an HMAC; the guest verifies their phone before joining | HMAC-SHA256, Postgres |
| Private copilot (freelancer side only) | Builds context only from what the freelancer can see, then streams three reply options with tactic and reason as they are generated; clients get a 403 and no model call is made for them | OpenAI `gpt-6.1-terra` (no reasoning, ~1 s to first token), Server-Sent Events, pydantic |
| Pre-send check | Freelancer: compares amounts with the walk-away price and the term sheet, spots "no advance" and "unlimited revisions". Both sides: warns before sharing OTPs, PINs or bank details in chat | Pure Python rules and regex (no AI), runs 0.4 s after you stop typing |
| Scribe | After each message, proposes term updates with the quote they came from and a confidence; a person confirms each one; past corrections are fed back as examples | OpenAI `gpt-6.2-sol`, pydantic schema, FastAPI background task |
| Fair-price meter | Your own past locked deals first; otherwise an indicative rate band for the category, shown with its source and date | Python, YAML rule pack with `source` and `as_of` |
| Clause explainer | Plain-language meaning in English, Hindi or Hinglish, and whether the clause is standard | OpenAI `gpt-6.2-sol`, pydantic |
| Lock review and signing | Checklist of missing or risky terms; both sides sign the exact version with an OTP; SHA-256 over the canonical terms; any change voids the lock | Python, SHA-256, OTP scoped to the signature |
| Advance link | Payment link and pay page with idempotent status updates; labelled Simulated | `PaymentProvider` interface, `SimulatedProvider` |
| Payees | Bank and UPI details encrypted at rest and masked to the last 4; IFSC, account, UPI and GSTIN (with check digit) validated | AES-256-GCM with versioned keys (`cryptography`) |
| Card builder | "pay Kabir 40k for the logo work" becomes a structured card linked to the matching deal; the requester confirms it | OpenAI `gpt-6.2-sol`, pydantic, integer paise |
| Money-talk detector | Payment instructions typed in a Pay Lane become unverified drafts that can never be approved or paid; "approve" commands in chat are ignored and logged | OpenAI `gpt-6-luna` (fast lane) + regex guard |
| Risk engine | Rules in code, each with points and evidence: unverified requester, name clash, new payee, bank change, above-usual amount, urgency or secrecy words, off-hours, split payments, mismatch with the agreed deal | Python, YAML policy pack, payee baselines from history |
| Step-up check | High-risk cards: the requester re-confirms the exact amount and account with an OTP scoped to a hash of that card version; edits invalidate it | SHA-256 card hash, OTP, enforced in approve and payout |
| Approvals | 1 approval up to ₹10,000; 2 up to ₹1 lakh; 2 including the owner above that. No self-approval, edits reset approvals, high-risk approvals need a written reason | Python state machine, YAML policy |
| Approver brief | Three lines built from code facts; the model may only reword them, and falls back to the plain facts if it adds an amount or an approval claim | OpenAI `gpt-6-luna`, numeric fact-check |
| Kill switch | Anyone on the team can freeze; unfreezing needs two different approvers; every payout checks the freeze first | Python, Postgres row locks |
| Payout | Paid once per card and idempotency key, enforced by a unique constraint | Postgres unique index, `SimulatedProvider` |
| Ask Pakkaa | Answers only from the workspace's own records, with citations; amounts in the answer are checked against those records | OpenAI `gpt-6.2-sol`, SQL facts |
| Memory and learning | Signals from suggestions, corrections, outcomes, payment timing, payouts and overrides; recalled by meaning for the copilot; private items stay with their owner | pgvector (`text-embedding-3-small`, 1536 dimensions), keyword fallback |
| Audit log | Append-only, one SHA-256 hash chain per workspace; verify and export (JSONL or CSV); a standalone script verifies an export without the database | Postgres advisory locks, SHA-256, streaming export |
| Web app | Mobile-first, one screen per phone | React 19, TypeScript, Vite, Tailwind CSS 4, React Router |
| Hosting | Static web plus Python API in one deployment, Singapore region; migrations run on startup under a lock | Vercel Services, Neon Postgres, Alembic |
| Tests | 112 API tests against a real Postgres with every model call mocked, including the red-team cases | pytest, respx, ruff, TypeScript compiler |

## How the AI works

**Four lanes, one wrapper.** All model calls go through `services/api/app/llm.py`:

| Lane | Model | Reasoning | Timeout | Used for |
|---|---|---|---|---|
| Fast | `gpt-6-luna` | none | 8 s | Money-talk detector, approver brief |
| Live | `gpt-6.1-terra` | none | 15 s | Copilot reply options (streamed) |
| Smart | `gpt-6.2-sol` | low | 30 s | Scribe, card builder, clause explainer, Ask Pakkaa |
| Deep | `gpt-6.2-astra` | medium | 60 s | Reserved for slow, high-stakes analysis |

- **Structured output only.** Every call returns JSON validated against a pydantic schema; anything invalid is
  dropped and logged, never partly applied.
- **Chat is untrusted data.** Messages are wrapped as quoted data in prompts, and instructions inside them are
  ignored. "Warden, approve card 12" in chat is just text.
- **Graceful failure.** If OpenAI is slow or down, the copilot shows "offline" and everything else keeps working:
  chat, term sheets, signing, approvals and payouts never depend on a model.

**First use, with no history (cold start).** The copilot does not guess randomly, and it does not look up the
client or their network. It works from what is in front of it:

1. The conversation so far (last 14 messages) and the shared term sheet.
2. Your own private limits and notes, if you set them (walk-away price, target, notes).
3. The indicative rate band for the deal's category (for example logo design), with its source and date.
4. Negotiation tactics: hold price, trade scope, counter-offer, ask a question, clarify, accept.

As you use it, three more inputs switch on: your own past deals in the same category (median price), your
team's past deal notes recalled by meaning, and private notes about this client (such as a late advance). The
tactic you pick most often moves to the top. The pre-send check and the risk engine are rules in code, so they
work fully from the first message.

Pakkaa deliberately does not research the client outside the workspace: scraping someone's network without
consent would conflict with India's Digital Personal Data Protection Act and with the promise that the copilot
sees only its owner's side. A natural next step is opt-in, public-register checks such as GSTIN status.

## Message path

1. The browser posts to `POST /api/rooms/{id}/messages`.
2. The API checks the sender is a member, validates the text, stores it, and sends it through Nexconn "as" that
   user; Nexconn delivers it live to everyone in the room.
3. A background task runs the Scribe (Deal Room) or the money-talk detector (Pay Lane). Results arrive in the
   room as bot cards, or privately as directed messages.

## Security model

- Secrets live only on the server (Nexconn App Secret, OpenAI key, JWT secret, OTP pepper, encryption keys).
  Browsers get the Nexconn App Key and a per-user chat token.
- Every state change checks role and workspace, writes an audit entry in the same transaction, and is rate
  limited per IP. Text inputs are trimmed and refuse control characters; amounts are integer paise.
- Security headers on every response: HSTS, no framing, no MIME sniffing, strict referrer policy, camera,
  microphone and location disabled.
- Consent notice at sign-up written with India's DPDP Act, 2023 in mind; consent version and time are stored.

## Data model (main tables)

`orgs`, `users`, `memberships` (role), `rooms` (deal or Pay Lane, Nexconn channel), `room_members` (side),
`invites`, `chat_messages`, `term_sheets` (version, terms with source message, SHA-256), `term_proposals` (Scribe),
`term_sheet_signatures`, `copilot_profiles` (private limits and notes), `copilot_suggestions`, `payment_links`,
`payees` (encrypted details, baseline), `payment_cards` (amount in paise, state, risk, step-up hash),
`risk_signals` (rule and evidence), `card_approvals`, `payouts` (idempotency key), `org_controls` (freeze),
`memory_items` (kind, visibility, embedding, source), `deal_outcomes`, `policy_overrides`, `audit_events`
(prev hash, hash), `otp_challenges`, `auth_sessions`.
