# AutoPay AI

AutoPay AI is a FastAPI backend that automates bill payments for Nigerian users. Send a bill to the Telegram bot or the REST API as text, a photo, or a PDF. The app extracts the vendor, amount, and due date, decides whether to **pay now**, **schedule**, or **hold**, and pays by Paystack bank transfer from a wallet the user funds through Paystack Checkout or a dedicated virtual account.

## Features

- Bill intake through the Telegram bot (text, photo, or PDF) or the REST API (JSON or file upload)
- Bill extraction with a Groq LLM when `GROQ_API_KEY` is set, a local RapidOCR fallback for images, and a regex fallback
- Pay now / schedule / hold decision in a LangGraph graph. The rules are deterministic and do not call an LLM: hold if the balance is below the amount plus fee, pay now if the bill is due within 3 days, otherwise schedule
- Paystack bank-transfer payouts. The recipient's account name is checked against the vendor name (fuzzy match), and the wallet is refunded when a payout fails
- Wallet top-ups through Paystack Checkout, credited by the `charge.success` webhook
- Dedicated virtual accounts (Paystack DVA). Bank transfers into them are credited to the right wallet
- JWT access tokens with rotating refresh tokens, and bcrypt password hashing
- BVN encrypted at rest with Fernet, plus an HMAC for lookups
- Paystack webhook signature verification (HMAC-SHA512) with replay protection
- Telegram webhook secret-token verification, so only Telegram can drive the bot
- Audit log, Prometheus metrics, and a small web dashboard at `/`
- Scheduled auto-pay: a background job pays bills that become due

## How it works

1. **Intake.** Send a bill to the bot, or call `POST /api/v1/bills/upload`. The bot shows the extracted details so you can confirm or edit them.
2. **Decision.** The decision node picks pay now, schedule, or hold from your balance, the payout fee, and the days until the due date. On the REST upload endpoint, pay-now bills are paid immediately. Scheduled bills are picked up later by the scheduler job, which runs every minute.
3. **Payout.** `execute_payout` locks the bill and wallet rows, debits the amount plus fee, resolves the account name with Paystack, checks it against the vendor name, creates a transfer recipient, and starts the transfer. Paystack's `transfer.success`, `transfer.failed`, and `transfer.reversed` webhooks set the final status.
4. **Funding.** `POST /api/v1/wallet/topup` returns a Paystack Checkout URL, and the wallet is credited when Paystack sends `charge.success`. A bank transfer into the user's dedicated virtual account is credited the same way, matched by the gateway's customer code.

## Architecture

- **`app/main.py`**: FastAPI app. Mounts the REST routers, the Paystack and Telegram webhook endpoints, the health and metrics endpoints, and the dashboard. Its lifespan starts the scheduler and the Telegram bot.
- **`app/services/`**: business logic for bills, payouts, wallet, auth, KYC, audit, bill extraction, and name matching.
- **`app/services/payments/`**: payment-provider interface and the Paystack implementation.
- **`app/agents/`**: LangGraph decision graph with one deterministic node.
- **`app/services/telegram.py`, `app/handlers/`**: python-telegram-bot conversations. The bot runs in the same process, in polling or webhook mode.
- **`app/core/scheduler.py`**: APScheduler jobs for scheduled and recurring bills.
- **PostgreSQL**: SQLModel tables, defined by `schema.sql` and versioned with Alembic.

Main libraries: Python 3.12, FastAPI, SQLModel/SQLAlchemy, PostgreSQL, Alembic, python-telegram-bot, APScheduler, LangGraph, Groq, RapidOCR (ONNX Runtime), PyMuPDF, and the Prometheus client.

## Getting started

### Prerequisites

- Python 3.12 (`pyproject.toml` allows 3.12 to 3.14; CI and Docker use 3.12)
- PostgreSQL 16 (what CI and Docker use). The schema creates the `pgcrypto` extension, so the role needs permission to do that (a superuser is fine for local development)
- A Paystack account with test API keys
- Optional: a Telegram bot token from @BotFather, a Groq API key, Node.js (for the SPA smoke test), Docker, and GNU make

### 1. Install

Run these from the repository root. The first install is large because of the OCR and ONNX dependencies.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env                 # Windows: copy .env.example .env
```

Generate the secrets and paste them into `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"                              # JWT_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # BVN_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_hex(16))"                                   # TELEGRAM_SECRET_TOKEN
```

At a minimum, set `DATABASE_URL`, `JWT_SECRET_KEY`, `BVN_ENCRYPTION_KEY`, and `PAYSTACK_SECRET_KEY` (a test key). `.env` is gitignored; never commit it.

Leave `WEBHOOK_URL` empty for local development so the Telegram bot uses polling. A non-empty value switches the bot to webhook mode. Leave `TELEGRAM_BOT_TOKEN` empty to run without the bot. See [Configuration](#configuration) for every variable.

`make keygen` prints the three secrets above if you have GNU make installed.

### 3. Create the database

```bash
psql -U postgres -h localhost -c "CREATE DATABASE autopay;"
alembic upgrade head
```

`alembic upgrade head` applies `schema.sql` (the baseline migration) and then the later migrations. It is safe to run on an empty database and on one that was created by loading `schema.sql` by hand.

The Docker Postgres container loads `schema.sql` on first boot; the app container then runs `alembic upgrade head`, which is a no-op for the objects that already exist.

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

- Dashboard: http://localhost:8000/
- API docs (Swagger UI): http://localhost:8000/docs
- Liveness: http://localhost:8000/healthz. Readiness (checks the database): http://localhost:8000/readyz

Settings are read from `.env` in the current directory, so start the app from the repository root.

Optional demo data:

```bash
python -m scripts.seed
```

This creates two demo users, `ada@example.com` and `tunde@example.com` (passwords are in `scripts/seed.py`), plus a demo bill and a demo top-up. It does nothing if any user already exists. Do not run it against a production database.

### Local webhooks (optional)

Paystack webhooks, and Telegram in webhook mode, need a public HTTPS URL to reach your machine. With [ngrok](https://ngrok.com/):

```bash
ngrok http 8000
```

Point Paystack's webhook URL at `https://<ngrok-host>/webhooks/paystack`. For Telegram webhook mode, set `WEBHOOK_URL=https://<ngrok-host>/telegram/webhook` and `TELEGRAM_SECRET_TOKEN` to any random string. Free ngrok URLs change on restart.

## Configuration

Settings come from environment variables or `.env`. Environment variables take precedence over `.env`.

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `staging`, `production`, or `test`. In `production` the app refuses to start when a required secret is missing. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `DATABASE_URL` | local `autopay` database | **Set this explicitly.** Format: `postgresql://user:password@host:5432/dbname` |
| `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_ECHO` | `10`, `20`, `false` | Connection pool size, overflow, and SQL logging |
| `JWT_SECRET_KEY` | empty | **Required.** Signs access and refresh tokens. Changing it signs out all users. Use a long random string. Outside production a missing value produces an ephemeral key that changes on restart. |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_ACCESS_TTL_MIN` | `15` | Access-token lifetime in minutes |
| `JWT_REFRESH_TTL_DAYS` | `7` | Refresh-token lifetime in days |
| `BCRYPT_ROUNDS` | `12` | Password-hashing work factor. Values below 12 are rejected outside the `test` environment. |
| `BVN_ENCRYPTION_KEY` | empty | **Required for KYC.** A Fernet key. It also derives the key for BVN hashes, so changing it breaks lookups for stored BVNs. |
| `PAYSTACK_SECRET_KEY` | empty | **Required.** `sk_test_...` or `sk_live_...`. Used for API calls and webhook signature checks. |
| `PAYSTACK_BASE_URL` | `https://api.paystack.co` | Paystack API root. |
| `AUTO_PROVISION_DVA_ON_SIGNUP` | `false` | If `true`, signup creates a dedicated virtual account. Enable it only after Paystack approves Dedicated NUBAN for your business. |
| `PAYOUT_FEE_NGN` | `50.00` | Flat fee per payout, in NGN |
| `TELEGRAM_BOT_TOKEN` | empty | Empty disables the bot. Use your own token from @BotFather. |
| `TELEGRAM_BOT_USERNAME` | empty | Bot username without `@`. Used for `https://t.me/<username>?start=...` links. |
| `TELEGRAM_SECRET_TOKEN` | empty | Secret Telegram echoes on every webhook request. **Required in production when webhook mode is on.** Requests without it get a 403. |
| `WEBHOOK_URL` | empty | Empty means long polling (local development). Set `https://<host>/telegram/webhook` for webhook mode. |
| `ALLOWED_ORIGINS` | localhost on ports 3000, 5173, and 8000 | Comma-separated CORS origins |
| `GROQ_API_KEY` | empty | Enables LLM extraction. Empty means OCR and regex only. |
| `SCHEDULER_ENABLED` | `true` | Set to `false` to keep the auto-pay jobs from running. The test suite sets it to `false`. |
| `APP_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT` | | Used by `docker-compose.yml`. `POSTGRES_PASSWORD` has no default: Compose refuses to start without it. |
| `TEST_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/autopay_test` | The throwaway database the test suite uses. Its name must contain "test". |
| `PAYSTACK_PUBLIC_KEY`, `LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2`, `GOOGLE_API_KEY` | | Present in `.env.example` but not used by the application. |

## API

Every endpoint except signup, login, refresh, the dashboard and API docs, the health and metrics endpoints, and the webhooks requires `Authorization: Bearer <access_token>`.

```bash
# Sign up. The response contains access_token and refresh_token.
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"first_name":"Ada","last_name":"Lovelace","email":"you@example.com","phone_number":"+2348012345678","password":"secret123"}'

# Create a bill from JSON
curl -X POST http://localhost:8000/api/v1/bills \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"vendor_name":"Lekki Phase 1 Estate","amount":25000,"due_date":"2026-11-30T00:00:00","account_number":"0123456789","bank_code":"058","bank_name":"GTBank"}'
```

Interactive documentation with request and response schemas is at `/docs`.

### Health and operations

| Method | Path | Description |
|---|---|---|
| GET | `/` | Web dashboard |
| GET | `/docs` | Interactive API docs (Swagger UI) |
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe (checks the database) |
| GET | `/metrics` | Prometheus metrics. Not authenticated, so restrict access at the network level |

### Auth (`/api/v1/auth`)

| Method | Path | Description |
|---|---|---|
| POST | `/signup` | Create an account and return tokens (201) |
| POST | `/login` | Log in and return tokens |
| POST | `/refresh` | Exchange a refresh token for new tokens. The old refresh token is rotated |
| POST | `/logout` | Revoke a refresh token, or all of your refresh tokens if none is given |
| GET | `/me` | Current user |
| GET | `/wallet` | Wallet balance and currency |
| POST | `/telegram/link-code` | Create a 15-minute code for `/link` in the bot |
| DELETE | `/telegram/link-code` | Invalidate unused link codes |
| DELETE | `/telegram/link` | Unlink Telegram from your account |

### Bills (`/api/v1/bills`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/bills` | Create a bill from JSON (201) |
| POST | `/api/v1/bills/upload` | Create a bill from a `request_bill` text field or a `file` (PDF or image). The decision runs, and pay-now bills are paid immediately (201) |
| GET | `/api/v1/bills` | List your bills |
| GET | `/api/v1/bills/{bill_id}` | Get one bill |
| POST | `/api/v1/bills/{bill_id}/pay` | Pay a bill now (runs the payout) |
| POST | `/api/v1/bills/{bill_id}/cancel` | Cancel a bill |

### KYC (`/api/v1/kyc`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/kyc/bvn` | Submit an 11-digit BVN. It is stored encrypted |
| GET | `/api/v1/kyc/bvn` | KYC status: last four digits and validation flag |

### Wallet (`/api/v1/wallet`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/wallet/provision` | Create a Paystack dedicated virtual account for the user |
| POST | `/api/v1/wallet/topup` | Start a Paystack Checkout top-up and return the checkout URL (201) |
| POST | `/api/v1/wallet/topup/verify` | Check a pending top-up with Paystack |
| GET | `/api/v1/wallet/transactions` | Transaction history |

### Webhooks

| Method | Path | Description |
|---|---|---|
| POST | `/webhooks/paystack` | Paystack events: `charge.success`, `transfer.success`, `transfer.failed`, `transfer.reversed`, and `dedicatedaccount.assign.success`. HMAC-SHA512 signature-checked and deduplicated |
| POST | `/telegram/webhook` | Telegram updates. Requires the `X-Telegram-Bot-Api-Secret-Token` header to match `TELEGRAM_SECRET_TOKEN` |

## Telegram bot

Create a link code with `POST /api/v1/auth/telegram/link-code`, then send `/link <code>` to the bot.

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/link <code>` | Link your Telegram account |
| `/unlink` | Unlink your Telegram account |
| `/wallet` | Show your wallet balance |
| `/bills` | List your bills |
| `/transactions` | Show your transactions |
| `/topup` | Start a top-up through Paystack Checkout |
| `/schedule` | Schedule a bill payment step by step |
| `/help` | Show help |
| `/cancel` | Cancel the current flow |

Sending any text, photo, or document starts the bill flow.

## Security

- Passwords are hashed with bcrypt (cost 12).
- Access tokens are short-lived JWTs; refresh tokens are stored hashed in the database and rotated on every use.
- BVNs are encrypted with Fernet. Lookups use a separate HMAC, so the database never holds a searchable plaintext BVN.
- Paystack webhooks are verified with HMAC-SHA512 before the body is parsed, and deduplicated on `(provider, event_id)`.
- Telegram webhook requests are checked against `TELEGRAM_SECRET_TOKEN`.
- Every state change writes an audit row.
- The app ships no credentials. In `production` it refuses to start if `JWT_SECRET_KEY`, `BVN_ENCRYPTION_KEY`, or `PAYSTACK_SECRET_KEY` is missing.

> **Rotate anything that was previously committed.** Earlier versions of this repository carried real-looking values in `app/core/config.py` (Paystack secret key, Telegram bot token, Groq and LangChain keys, JWT signing key, BVN encryption key, and the database password) and the same database password in `.env.example`, `docker-compose.yml`, the `Makefile`, and the test fixtures. Those values were removed from the source, but they should be treated as exposed and rotated.

## Testing

```bash
pytest                                      # full suite
pytest tests/unit                           # unit tests only
pytest tests/integration                    # integration tests (needs PostgreSQL)
pytest -k wallet                            # filter by test name
pytest --cov=app --cov-report=term-missing  # coverage (gate: 70%)
ruff check .                                # lint
mypy app                                    # type check
```

330 tests, about 35 seconds. Coverage is 76%.

> **The suite never touches a non-test database.** The integration fixtures `TRUNCATE` every application table before and after each test, so the suite resolves its database from `TEST_DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/autopay_test`) and refuses to run if the name does not contain "test". The check runs against the connected database, not the URL.

Set up a test database once:

```bash
psql -U postgres -h localhost -c "CREATE DATABASE autopay_test;"
export DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/autopay_test
pytest
```

`TEST_DATABASE_URL` (read from the environment or `.env`) points the suite at the throwaway database. `DATABASE_URL` only matters for Alembic and the app itself.

`tests/manual/` holds interactive scripts that drive a browser. They are not part of the automated suite and need Node.js.

## Project structure

```
app/
  main.py              FastAPI app, routers, dashboard, lifespan (scheduler and bot)
  api/                 REST routers: auth, bills, kyc, wallet, webhooks, health
  agents/              LangGraph decision graph
  core/                settings, database engine, security, scheduler, logging, metrics
  handlers/            Telegram conversation handlers
  models/              SQLModel table definitions
  schemas/             Pydantic request and response models
  services/            business logic: bills, payouts, wallet, auth, KYC, audit, extraction, Telegram
  services/payments/   payment-provider interface and Paystack client
  static/, templates/  web dashboard
migrations/            Alembic environment and versions (0001 to 0004)
scripts/               entrypoint.sh (Docker), seed.py (demo data)
tests/                 pytest suite: unit/, integration/, manual/
schema.sql             full PostgreSQL schema
DOCUMENTATION.md       long-form design notes
LICENSE                MIT
```

## Database migrations

- `0001_baseline` applies `schema.sql`. Every statement in it is idempotent, so it is safe on an empty database and on one that already has the schema.
- `0002` to `0004` add the webhook-event table, the provider order reference, and the virtual-account customer code. They skip themselves when the object already exists.
- For later changes, edit the models, then run `alembic revision --autogenerate -m "describe the change"` and `alembic upgrade head`.
- Revision ids must stay within 32 characters: Alembic's `alembic_version.version_num` column is `VARCHAR(32)`.

## Docker

`docker-compose.yml` defines a PostgreSQL 16 service and the app image.

```bash
docker compose up -d      # app on http://localhost:8000
docker compose logs -f app
```

The Postgres container loads `schema.sql` on first boot, and the app container waits for the database and applies the migrations before starting uvicorn. The image sets `ENVIRONMENT=production`, so it needs every secret in `.env`; set `ENVIRONMENT=development` in `.env` to relax that while developing.

The test image adds the dev dependencies:

```bash
docker compose -f docker-compose.test.yml run --rm app-test
```

GNU make is optional. `make help` lists the shortcuts (`up`, `test`, `migrate`, `seed`, `keygen`, `db-init`, and so on) if you have it installed; every target is a thin wrapper around the commands above.

## Deployment

Run a single app process: the scheduler and the Telegram bot live inside the app process, and a second worker would double-pay bills and fight over the Telegram webhook.

### Production checklist

- [ ] Rotate the credentials that were previously committed (see [Security](#security))
- [ ] Set `ENVIRONMENT=production`
- [ ] Set your own `DATABASE_URL`, `JWT_SECRET_KEY`, `BVN_ENCRYPTION_KEY`, `PAYSTACK_SECRET_KEY` (a live `sk_live_...` key), `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_SECRET_TOKEN`
- [ ] Set `WEBHOOK_URL=https://<host>/telegram/webhook`, and point Paystack's webhook URL at `https://<host>/webhooks/paystack`
- [ ] Set `ALLOWED_ORIGINS` to your frontend's origin(s)
- [ ] Keep `/metrics` on a private network
- [ ] Enable `AUTO_PROVISION_DVA_ON_SIGNUP` only after Paystack approves Dedicated NUBAN for your business
- [ ] Run one app process

## Known gaps

- **BVN is not validated against a registry.** BVNs are encrypted and stored, but no provider checks them, so `bvn_validated` stays `false`. Wiring it up means adding a BVN-verification provider.
- **A transfer into a virtual account is credited from the webhook only.** If a webhook is lost, the transfer is not picked up by a background poll. The manual top-up verify endpoint covers Checkout top-ups, not virtual-account deposits.
- **`/metrics` is unauthenticated.** It leaks request volume and business event rates; keep it on a private network.

## License

MIT. See [LICENSE](LICENSE).
