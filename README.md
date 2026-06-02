# AutoPay AI

An AI-powered bill payment automation system for Nigeria. Users send bills (PDF, image, or text) to a Telegram bot, and an AI agent extracts the details, verifies the recipient account, and automatically pays or schedules the payment based on available balance and due date.

## How It Works

1. **Link account** — user registers on the web dashboard, generates a link code, and sends `/link CODE` to the Telegram bot.
2. **Send a bill** — user forwards a bill as a photo, PDF, or plain text.
3. **AI extraction** — the bot extracts vendor name, amount, due date, account number, and bank from the bill.
4. **Account verification** — the extracted account is verified against the bank via Payaza's name enquiry API. Payments are blocked if the name does not match the bill.
5. **Agent decision** — a LangGraph agent (powered by Groq) decides:
   - `pay_now` — balance sufficient and due in ≤ 3 days → prompts user for final confirmation
   - `schedule` — balance sufficient but due in > 3 days → saved and processed automatically when due
   - `hold` — insufficient balance → user is notified to top up
6. **Payout** — confirmed payments are sent via Payaza's payout API. Scheduled payments are processed by a background job that runs every 24 hours.

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Bot | python-telegram-bot v20 |
| AI Agent | LangGraph + Groq |
| Bill parsing | LangChain + Unstructured (PDF/image OCR) |
| Payments | Payaza API |
| Database | PostgreSQL via SQLModel |
| Scheduling | APScheduler |
| Deployment | Railway (nixpacks) |

## Project Structure

```
├── main.py                  # FastAPI app + Telegram webhook setup
├── agents/
│   ├── graphs.py            # LangGraph workflow definition
│   ├── nodes.py             # Decision node (pay_now / schedule / hold)
│   └── state.py             # AgentState TypedDict
├── api/
│   ├── authRoute.py         # User registration and Telegram linking endpoints
│   ├── billRoute.py         # Bill management REST endpoints
│   └── webHookRoute.py      # Payaza webhook handler (credits/settlements)
├── handlers/
│   ├── auth.py              # /start, /link, /wallet Telegram commands
│   ├── bill_conversation.py # Multi-step bill ConversationHandler
│   └── helpers.py           # Shared utilities and keyboard builders
├── models/
│   ├── user.py              # User + TelegramLinkCode SQLModel tables
│   ├── bill.py              # Bill table
│   └── transaction.py       # VirtualAccount table
├── services/
│   ├── payaza.py            # Payaza API client (virtual accounts, payouts, webhooks)
│   ├── payout.py            # High-level payout orchestration
│   ├── loader.py            # PDF bill extractor
│   ├── imageloader.py       # Image bill extractor
│   ├── textLoader.py        # Plain-text bill extractor
│   └── banks.py             # Bank name → bank code lookup
└── core/
    ├── database.py          # SQLModel engine + init_db
    └── scheduler.py         # APScheduler job for due bill processing
```

## Environment Variables

Create a `.env` file in the project root:

```env
TELEGRAM_BOT_TOKEN=
WEBHOOK_URL=https://your-domain.com/webhook

DATABASE_URL=postgresql://user:password@host:port/dbname

PAYAZA_PUBLIC_KEY=
PAYAZA_WEBHOOK_SECRET=

GROQ_API_KEY=
```

## Running Locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

For the Telegram webhook to work locally, expose the server with a tool like [ngrok](https://ngrok.com) and set `WEBHOOK_URL` to the public URL.

## Deployment

The project is configured for Railway via `nixpacks.toml`, which installs the system dependencies required for OCR (`tesseract`, `poppler_utils`, `libGL`).

```bash
railway up
```

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Introduction and onboarding instructions |
| `/link CODE` | Links the Telegram account to a web dashboard account |
| `/wallet` | Shows current balance and virtual account details for top-ups |
| `/cancel` | Cancels an in-progress bill conversation |

Send any bill (PDF, photo, or text) to start the payment flow.
