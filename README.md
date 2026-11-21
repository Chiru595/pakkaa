# Pakkaa

**Deal pakkaa! Payment pakkaa!**

Pakkaa is the money layer for business conversations, for Indian freelancers, agencies and small suppliers.

- **Deal Room (money in):** freelancer and client negotiate in one room, each with a private AI copilot. Agreed
  terms fill a live term sheet, both sides lock it with OTP, and the advance is requested through a payment link.
- **Pay Lane (money out):** the team's only valid channel for outgoing payments. Payment cards come only from
  verified people, are risk-checked, verified by an in-app call when risky, approved by two people, then paid.

Everyday chat stays on WhatsApp or Slack. Pakkaa is where money is decided.

Built on [Nexconn](https://www.nexconn.ai) Chat and Call. Payments in this build are simulated. Nothing here is
legal or financial advice.

## Architecture

```
apps/web        React + Vite + TypeScript + Tailwind (mobile first), Nexconn Web Chat SDK
services/api    FastAPI, SQLAlchemy, Alembic, Postgres + pgvector, OpenAI
api/index.py    Vercel Python function entry for the API
```

- Our API is the source of truth. Every room message goes through `POST /api/rooms/{id}/messages`, is stored,
  and is delivered live by Nexconn's server API. Cards (term sheets, payment cards, notices) are a custom
  Nexconn message type `PK:Card` posted by the Pakkaa bot.
- The Nexconn App Secret stays on the server. Browsers get only the App Key and a per-user token.
- Every state change is authorised by role, checked for org isolation, and written to a hash-chained audit log.

See [NEXCONN_NOTES.md](NEXCONN_NOTES.md) for how Nexconn is used and [TODO.md](TODO.md) for progress.

## Run it locally (Windows, PowerShell)

Prerequisites: Git, Python 3.12, Node.js 20 or newer.

1. Clone and create your env file:
   ```powershell
   git clone https://github.com/Chiru595/pakkaa
   cd pakkaa
   py -3.12 scripts/gen_secrets.py
   ```
   Then open `.env` and fill `NEXCONN_APP_KEY`, `NEXCONN_APP_SECRET` and `OPENAI_API_KEY`.

2. API dependencies:
   ```powershell
   cd services/api
   py -3.12 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
   cd ../..
   ```

3. Database (Postgres 16 with pgvector). Either Docker:
   ```powershell
   docker compose up -d
   ```
   or, without Docker, an embedded Postgres that also updates `.env`:
   ```powershell
   services/api/.venv/Scripts/python scripts/dev_db.py
   ```

4. Migrations and API (keep this terminal open):
   ```powershell
   cd services/api
   .venv\Scripts\alembic upgrade head
   .venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload --reload-dir app
   ```

5. Web app (second terminal):
   ```powershell
   cd apps/web
   npm install
   npm run dev
   ```

6. Open http://localhost:5173 in a normal window and an incognito window. Sign in with two different mobile
   numbers; in development the one-time code is shown on screen (no SMS is sent).

After a restart, run `scripts/dev_db.py` again (if you use the embedded database), then steps 4 and 5.

## Tests

```powershell
cd services/api
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check app tests
cd ../../apps/web
npx tsc -b
```

API tests run against a real Postgres database (`pakkaa_test`, created by `scripts/dev_db.py`, or set
`TEST_DATABASE_URL`).

## Prior work

See [PRIOR_WORK.md](PRIOR_WORK.md).
