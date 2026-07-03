# Graph Report - .  (2026-06-12)

## Corpus Check
- 101 files · ~58,743 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1362 nodes · 3988 edges · 71 communities (63 shown, 8 thin omitted)
- Extraction: 78% EXTRACTED · 22% INFERRED · 0% AMBIGUOUS · INFERRED: 864 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Telegram Conversation Handlers|Telegram Conversation Handlers]]
- [[_COMMUNITY_JWT Security & Token Management|JWT Security & Token Management]]
- [[_COMMUNITY_Auth API Routes|Auth API Routes]]
- [[_COMMUNITY_KYC BVN Verification|KYC BVN Verification]]
- [[_COMMUNITY_SPA Frontend JavaScript|SPA Frontend JavaScript]]
- [[_COMMUNITY_Date Parser Service|Date Parser Service]]
- [[_COMMUNITY_Wallet API & Transactions|Wallet API & Transactions]]
- [[_COMMUNITY_Audit Log System|Audit Log System]]
- [[_COMMUNITY_Telegram Handler Unit Tests|Telegram Handler Unit Tests]]
- [[_COMMUNITY_Topup Conversation Flow|Topup Conversation Flow]]
- [[_COMMUNITY_Payment Exceptions & Errors|Payment Exceptions & Errors]]
- [[_COMMUNITY_SPA Wallet Render Test|SPA Wallet Render Test]]
- [[_COMMUNITY_Telegram Notification Tests|Telegram Notification Tests]]
- [[_COMMUNITY_LangGraph Decision Agent|LangGraph Decision Agent]]
- [[_COMMUNITY_Schedule Conversation Helpers|Schedule Conversation Helpers]]
- [[_COMMUNITY_SPA Bundle Smoke Tests|SPA Bundle Smoke Tests]]
- [[_COMMUNITY_Bill Conversation Flow|Bill Conversation Flow]]
- [[_COMMUNITY_Bills API Endpoints|Bills API Endpoints]]
- [[_COMMUNITY_Prometheus Metrics|Prometheus Metrics]]
- [[_COMMUNITY_Telegram Auth Handlers|Telegram Auth Handlers]]
- [[_COMMUNITY_Integration Test Fixtures|Integration Test Fixtures]]
- [[_COMMUNITY_Telegram Fake Message Tests|Telegram Fake Message Tests]]
- [[_COMMUNITY_SQLModel Model Tests|SQLModel Model Tests]]
- [[_COMMUNITY_Telegram Bot Builder|Telegram Bot Builder]]
- [[_COMMUNITY_Webhook Endpoint Tests|Webhook Endpoint Tests]]
- [[_COMMUNITY_SQLModel ORM Models|SQLModel ORM Models]]
- [[_COMMUNITY_Auto-Pay Scheduler Tests|Auto-Pay Scheduler Tests]]
- [[_COMMUNITY_Payout ACID Tests|Payout ACID Tests]]
- [[_COMMUNITY_Paystack Provider Tests|Paystack Provider Tests]]
- [[_COMMUNITY_Payout Name Validation|Payout Name Validation]]
- [[_COMMUNITY_APScheduler Integration|APScheduler Integration]]
- [[_COMMUNITY_Auth Endpoint Tests|Auth Endpoint Tests]]
- [[_COMMUNITY_Bill Loader Pipeline|Bill Loader Pipeline]]
- [[_COMMUNITY_Wallet Endpoint Tests|Wallet Endpoint Tests]]
- [[_COMMUNITY_Agent Decision Unit Tests|Agent Decision Unit Tests]]
- [[_COMMUNITY_Payment Provider Interface|Payment Provider Interface]]
- [[_COMMUNITY_Project Architecture Concepts|Project Architecture Concepts]]
- [[_COMMUNITY_Wallet Transaction Tests|Wallet Transaction Tests]]
- [[_COMMUNITY_Paystack Provider Implementation|Paystack Provider Implementation]]
- [[_COMMUNITY_FastAPI Application Entrypoint|FastAPI Application Entrypoint]]
- [[_COMMUNITY_Bill Endpoint Tests|Bill Endpoint Tests]]
- [[_COMMUNITY_Handler Ordering Tests|Handler Ordering Tests]]
- [[_COMMUNITY_Webhook Payload Processing|Webhook Payload Processing]]
- [[_COMMUNITY_Bill Extraction & LLM Loaders|Bill Extraction & LLM Loaders]]
- [[_COMMUNITY_Name Matching Service|Name Matching Service]]
- [[_COMMUNITY_Provider Stub & Webhook Events|Provider Stub & Webhook Events]]
- [[_COMMUNITY_Payment Math Utilities|Payment Math Utilities]]
- [[_COMMUNITY_Database Engine & Sessions|Database Engine & Sessions]]
- [[_COMMUNITY_KYC Endpoint Tests|KYC Endpoint Tests]]
- [[_COMMUNITY_Smoke Tests|Smoke Tests]]
- [[_COMMUNITY_Health Check Endpoints|Health Check Endpoints]]
- [[_COMMUNITY_Logging Configuration|Logging Configuration]]
- [[_COMMUNITY_Topup Initialization Stub|Topup Initialization Stub]]
- [[_COMMUNITY_Start Command Tests|Start Command Tests]]
- [[_COMMUNITY_Payment Provider Injection|Payment Provider Injection]]
- [[_COMMUNITY_Alembic Migration Config|Alembic Migration Config]]
- [[_COMMUNITY_HTTP Client IP Helper|HTTP Client IP Helper]]
- [[_COMMUNITY_Baseline Migration|Baseline Migration]]
- [[_COMMUNITY_Docker Compose Services|Docker Compose Services]]
- [[_COMMUNITY_Test Conftest Fixtures|Test Conftest Fixtures]]
- [[_COMMUNITY_CI & Code Quality|CI & Code Quality]]
- [[_COMMUNITY_Agent Package Init|Agent Package Init]]
- [[_COMMUNITY_App Package Init|App Package Init]]
- [[_COMMUNITY_Entrypoint Script|Entrypoint Script]]
- [[_COMMUNITY_Core Package Init|Core Package Init]]
- [[_COMMUNITY_Integration Tests Package|Integration Tests Package]]
- [[_COMMUNITY_Schemas Package Init|Schemas Package Init]]
- [[_COMMUNITY_Services Package Init|Services Package Init]]
- [[_COMMUNITY_Unit Tests Package|Unit Tests Package]]

## God Nodes (most connected - your core abstractions)
1. `User` - 152 edges
2. `Bill` - 90 edges
3. `Transaction` - 86 edges
4. `AuditActor` - 77 edges
5. `AuditEventType` - 64 edges
6. `session_scope()` - 60 edges
7. `BillStatus` - 59 edges
8. `TransactionType` - 55 edges
9. `TransactionStatus` - 55 edges
10. `AuditEntityType` - 54 edges

## Surprising Connections (you probably didn't know these)
- `Session` --uses--> `User`  [INFERRED]
  tests/integration/test_auth_endpoints.py → app/models/user.py
- `Session` --uses--> `User`  [INFERRED]
  tests/integration/test_kyc_endpoints.py → app/models/user.py
- `Production Database Service` --semantically_similar_to--> `Test Database Service`  [INFERRED] [semantically similar]
  docker-compose.yml → docker-compose.test.yml
- `test_scheduled_bill_auto_pays_when_due()` --calls--> `timedelta`  [INFERRED]
  tests/integration/test_auto_pay.py → app/core/security.py
- `test_scheduled_bill_skipped_when_already_paid()` --calls--> `timedelta`  [INFERRED]
  tests/integration/test_auto_pay.py → app/core/security.py

## Import Cycles
- 1-file cycle: `app/agents/graphs.py -> app/agents/graphs.py`
- 1-file cycle: `app/agents/nodes.py -> app/agents/nodes.py`
- 1-file cycle: `app/main.py -> app/main.py`
- 1-file cycle: `app/core/security.py -> app/core/security.py`
- 1-file cycle: `app/handlers/helpers.py -> app/handlers/helpers.py`
- 1-file cycle: `app/handlers/topup_conversation.py -> app/handlers/topup_conversation.py`
- 1-file cycle: `app/services/auth.py -> app/services/auth.py`
- 1-file cycle: `app/services/payout.py -> app/services/payout.py`
- 1-file cycle: `app/services/date_parser.py -> app/services/date_parser.py`
- 1-file cycle: `app/services/wallet.py -> app/services/wallet.py`

## Hyperedges (group relationships)
- **Core Backend Stack** — readme_fastapi_app, readme_postgres, readme_paystack, readme_langgraph_agent, readme_telegram_bot [EXTRACTED 1.00]
- **Full Test Infrastructure** — workflows_ci_workflow, dockercomposetest_app_test, dockercomposetest_db_test, workflows_ci_pytest, workflows_ci_ruff, workflows_ci_coverage [INFERRED 0.85]

## Communities (71 total, 8 thin omitted)

### Community 0 - "Telegram Conversation Handlers"
Cohesion: 0.05
Nodes (96): ConversationHandler, DEFAULT_TYPE, InlineKeyboardMarkup, Update, escape_md(), Escape Telegram Markdown V1 reserved chars. Used whenever we     interpolate use, bank_quick_pick_keyboard(), cancel_command() (+88 more)

### Community 1 - "JWT Security & Token Management"
Cohesion: 0.07
Nodes (58): Any, datetime, datetime, Session, User, create_access_token(), create_refresh_token(), decode_token() (+50 more)

### Community 2 - "Auth API Routes"
Cohesion: 0.13
Nodes (49): create_telegram_link_code(), invalidate_telegram_link_codes(), login(), logout(), me(), Auth API — signup, login, refresh, logout, me.  Mounted at /api/v1/auth in `app., Best-effort DVA provisioning. On any provider error, writes a     `va.created` a, Generate a short-lived (15 min) code the user pastes into the     Telegram bot w (+41 more)

### Community 3 - "KYC BVN Verification"
Cohesion: 0.08
Nodes (45): get_kyc(), KYC API — BVN submission.  Mounted at /api/v1/kyc in `app.main`., Encrypt the BVN with Fernet, store its hash + last4, write audit., submit_bvn(), Session, User, KycStatusResponse, KycSubmitRequest (+37 more)

### Community 4 - "SPA Frontend JavaScript"
Cohesion: 0.09
Nodes (41): API, billRow(), cancelBill(), doLogout(), esc(), filterBills(), fmtDate(), fmtMoney() (+33 more)

### Community 5 - "Date Parser Service"
Cohesion: 0.10
Nodes (37): datetime, _clamp_to_present(), _now(), parse_bill_due_date(), Robust date parsing for LLM-extracted bill due dates.  LLMs return dates in ever, ISO 8601 — full datetime, date-only, with/without TZ, with/without microseconds., Parse 'today', 'tomorrow', 'in 2 weeks', '5 days from today', etc., Best-effort parse. Never raises; falls back to `datetime.now()`. (+29 more)

### Community 6 - "Wallet API & Transactions"
Cohesion: 0.16
Nodes (37): list_transactions(), provision_virtual_account(), ProvisionResponse, Wallet API — balance, virtual account provisioning, top-up, transaction history., Idempotent. If the user already has a VA, returns it (200 OK     semantically, b, Body for `POST /wallet/topup`., Wire format for the top-up init response., Mint a unique `reference`, persist a pending `Transaction` row,     call `provid (+29 more)

### Community 7 - "Audit Log System"
Cohesion: 0.15
Nodes (31): Any, AuditActor, Session, AuditEntityType, AuditEventType, AuditLog, AuditLog, audit_bill_created() (+23 more)

### Community 8 - "Telegram Handler Unit Tests"
Cohesion: 0.12
Nodes (33): _FakeCallbackQuery, _FakeContext, _FakeUpdate, Unit tests for the Telegram bot handlers.  We use PTB's `Application` + `process, Minimal stand-in for a telegram.CallbackQuery.      The handlers call `query.ans, Put a parsed bill + staging snapshot into user_data, as if     receive_bill() ha, A non-numeric amount should bounce back to EDIT_VALUE with an     error, not sil, A non-parseable date should bounce back to EDIT_VALUE. (+25 more)

### Community 9 - "Topup Conversation Flow"
Cohesion: 0.12
Nodes (33): Decimal, DEFAULT_TYPE, InlineKeyboardMarkup, Update, InlineKeyboardMarkup, cancel_command(), done_keyboard(), handle_cancel() (+25 more)

### Community 10 - "Payment Exceptions & Errors"
Cohesion: 0.22
Nodes (28): AsyncClient, PaymentError, AccountNameMismatch, AuthenticationError, InsufficientFunds, InvalidAccount, KYCRequired, PaymentError (+20 more)

### Community 11 - "SPA Wallet Render Test"
Cohesion: 0.14
Nodes (27): AuditActor, Bill, Decimal, PaymentProvider, Session, Transaction, User, Enum (+19 more)

### Community 12 - "Telegram Notification Tests"
Cohesion: 0.10
Nodes (28): bot_with_send_recorder(), _link_user(), Tests for the credit/debit/refund Telegram notifications and the new multi-actio, A debit notification should mention amount, narration, and     remaining balance, A refund notification should include the refunded amount,     the failure reason, If the user has no linked Telegram, notification is a no-op., If Telegram raises (rate-limit, network), notify returns     False but doesn't p, Tapping 'Check balance' on the Done keyboard shows the     current wallet balanc (+20 more)

### Community 13 - "LangGraph Decision Agent"
Cohesion: 0.18
Nodes (24): build_graph(), LangGraph state graph build.  Currently a single-node graph (the rule is the onl, Build the LangGraph state graph., Invoke the graph. Equivalent to calling `decide()` directly —     exists so call, run_agent(), decide_for_bill(), make_decision_node(), Agent nodes — pure decision function + LangGraph node wrapper.  The decision rul (+16 more)

### Community 14 - "Schedule Conversation Helpers"
Cohesion: 0.10
Nodes (25): datetime, InlineKeyboardMarkup, handle_date_quickpick(), handle_edit(), handle_new_value(), Tap 'Edit' on the confirm screen → show the multi-field     editor (a list of al, User tapped a date quick-pick button. Apply the picked date     to the staging a, date_from_quickpick() (+17 more)

### Community 15 - "SPA Bundle Smoke Tests"
Cohesion: 0.08
Nodes (25): _get_spa_assets(), SPA smoke tests for the stripped-down bundle.  The frontend → backend wiring has, The HTML and JS are both served at the expected URLs., apiFetch was the entire backend wiring. Its presence means     someone re-introd, The bundle code should not reference any /api/v1/* path.     We strip comments s, Auth was via localStorage tokens. With auth removed, no     localStorage.getItem, The state-machine balance flow (setBalance / loadBalance /     reRenderBalanceCa, The new dataflow anchor: every page renderer reads from     the `data` namespace (+17 more)

### Community 16 - "Bill Conversation Flow"
Cohesion: 0.18
Nodes (24): DEFAULT_TYPE, Update, cancel_command(), _cancel_persisted_bill(), handle_cancel(), handle_confirm(), handle_edit_discard(), handle_edit_done() (+16 more)

### Community 17 - "Bills API Endpoints"
Cohesion: 0.20
Nodes (22): cancel_bill(), create_bill(), get_bill(), list_bills(), pay_bill(), Bills API — upload, list, get, pay, cancel.  Mounted at /api/v1/bills in `app.ma, Upload a bill (PDF / image) OR paste text, get back an     extracted + agent-dec, upload_bill() (+14 more)

### Community 18 - "Prometheus Metrics"
Cohesion: 0.12
Nodes (22): Prometheus metrics registry for the AutoPay AI app.  Exposes a small, focused se, `source` is "checkout" | "dva" | "manual"., `trigger` is "manual" | "upload" | "scheduled" | "recurring"., `trigger` is "manual" | "auto_pay"., `result` is "success" | "insufficient" | "provider_error" | "race"., `result` is "success" | "fail" | "skip"., record_bill_created(), record_bill_paid() (+14 more)

### Community 19 - "Telegram Auth Handlers"
Cohesion: 0.19
Nodes (22): DEFAULT_TYPE, Update, User, Context-manager variant for non-FastAPI callers (workers, scripts)., session_scope(), bills_command(), help_command(), link_command() (+14 more)

### Community 20 - "Integration Test Fixtures"
Cohesion: 0.12
Nodes (17): _clean_db(), client(), Integration test fixtures.  These tests hit a real database (`autopay_test`). We, A TestClient with the test DB wired in., Direct DB session for tests that need to set up data without HTTP., Default stub: create customer + DVA, everything else raises., A fresh stub for each test. Overrides `get_payment_provider`.      Patches TWO s, # IMPORTANT: also refresh the module-level `settings` shortcut. If (+9 more)

### Community 21 - "Telegram Fake Message Tests"
Cohesion: 0.11
Nodes (17): Any, _FakeMessage, _link_user_sync(), The /help output must include /transactions so users can     discover the new co, `/topup` from an unlinked chat should ask the user to link     first, not crash., Linked user should see the quick-pick amount keyboard with     the minimum/maxim, When the user picks 'Custom' and types 7500.50, the     top-up is initialized fo, A non-numeric custom amount should bounce back to the     custom-prompt state wi (+9 more)

### Community 22 - "SQLModel Model Tests"
Cohesion: 0.09
Nodes (12): Tests for the SQLModel model definitions.  These verify that each model:   - Has, The Python attribute is event_metadata; the SQL column is 'metadata'., Codes are 6 hex chars (12 chars when decoded from token_hex(3))., NUMERIC(14,2) preserves 0.01 precision (unlike float)., BVN was extracted to kyc_records — must not appear in users., The MVP used 'payaza_reference' — we now use generic 'provider' + 'provider_refe, test_audit_log_metadata_column_named_metadata(), test_decimal_precision_in_arithmetic() (+4 more)

### Community 23 - "Telegram Bot Builder"
Cohesion: 0.13
Nodes (21): ConversationHandler, Request, Application, build_bill_conversation(), build_schedule_conversation(), build_topup_conversation(), build_application(), _format_amount() (+13 more)

### Community 24 - "Webhook Endpoint Tests"
Cohesion: 0.24
Nodes (21): _make_transfer(), Integration tests for the Paystack webhook endpoint.  Coverage:   * charge.succe, Paystack retries the same event on a network blip. Second     delivery is a 200, Regression test for the "GET on the webhook URL returns the SPA     shell" bug., HEAD is still 405 — that's intentional.      Paystack only POSTs, so a HEAD requ, Create a debit transaction (status=processing) for testing the     transfer.* we, _sign(), test_bad_signature_returns_400() (+13 more)

### Community 25 - "SQLModel ORM Models"
Cohesion: 0.14
Nodes (13): Audit log — every state-changing event appends a row.  Rows are inserted in the, # NOTE: 'metadata' is reserved by SQLAlchemy Declarative, so we name the, Bill model — vendor invoice to be paid on the user's behalf., SQLModel ORM models.  Importing this package registers every model on `SQLModel., KycRecord, KYC (Know Your Customer) record — holds encrypted BVN.  The BVN (Bank Verificati, Telegram link codes — short-lived codes users send to the bot to link.  Replaces, Transaction model — every wallet credit/debit the app makes.  Provider-agnostic: (+5 more)

### Community 26 - "Auto-Pay Scheduler Tests"
Cohesion: 0.20
Nodes (20): _claim_due_scheduled_bill_ids(), _process_scheduled_bills(), Atomically claim all due scheduled bills for processing.      Uses `SELECT ... F, Auto-pay scheduled bills whose due date has arrived.      The flow per bill:, _make_user_with_balance(), Integration tests for the auto-pay scheduler path.  A `scheduled` bill whose `du, A `paid` bill should never be picked up by the scheduler., A scheduler run with no due bills is a fast no-op (no errors,     no audit rows, (+12 more)

### Community 27 - "Payout ACID Tests"
Cohesion: 0.14
Nodes (15): ACID tests for the payout service.  These tests exercise the database-level guar, # NOTE: A full concurrent-attempt test (two threads racing on the, Sequential simulation: first attempt succeeds, the second     sees bill.status=', Calling confirm_payout twice for the same reference must not     transition the, When the bill is unaffordable, the user gets a 402 and the     wallet must be un, test_confirm_payout_idempotent(), test_payout_failure_rolls_back_wallet_debit(), test_second_payout_attempt_gets_409() (+7 more)

### Community 28 - "Paystack Provider Tests"
Cohesion: 0.18
Nodes (20): PaystackProvider, _client(), Tests for the Paystack provider.  We use `respx` to mock httpx calls so no real, test_create_customer_auth_error(), test_create_customer_happy_path(), test_create_virtual_account_parses_response(), test_initiate_transfer(), test_kyc_required_error_mapping() (+12 more)

### Community 29 - "Payout Name Validation"
Cohesion: 0.19
Nodes (19): _async_autopay(), _autopay_one_bill(), Re-evaluate a single due bill and (if pay_now) execute the     payout. Called fr, Async helper: get a provider, open a session, call execute_payout.      Commits, _make_scheduled_bill(), _make_user_with_balance(), Integration tests for the account-name-mismatch guard.  These tests exercise the, A bill for 'DSTV Nigeria Ltd' resolving to 'DSTV NG LTD' is     a real-world ban (+11 more)

### Community 30 - "APScheduler Integration"
Cohesion: 0.19
Nodes (17): get_scheduler(), _process_recurring_bills(), APScheduler integration.  Runs in the same process as FastAPI (single worker by, Wrap an async function as a sync callable that runs it in its     own event loop, Spawn the next occurrence of every recurring bill whose     `next_recurrence_dat, Idempotent: a second call is a no-op.      Detects whether we're inside a runnin, _run_sync_in_private_loop(), start_scheduler() (+9 more)

### Community 31 - "Auth Endpoint Tests"
Cohesion: 0.22
Nodes (18): Integration tests for the auth endpoints.  Coverage:   * signup  → 201, tokens r, Swagger UI's 'Authorize' button + the curl 'Authorization' header     in 'Try it, RFC 6750 says a 401 on a Bearer-protected route must include     `WWW-Authentica, _signup_payload(), test_401_includes_www_authenticate_header(), test_login_401_on_bad_password(), test_login_happy_path(), test_logout_revokes_refresh() (+10 more)

### Community 32 - "Bill Loader Pipeline"
Cohesion: 0.18
Nodes (14): ABC, _build_loader(), Pick a loader based on what the user sent. Returns None if     this isn't a bill, BaseLoader, ImageLoader, loader_from_upload(), PDFLoader, Bill loaders — convert uploaded bytes/text into a `BillExtractionResult`.  Three (+6 more)

### Community 33 - "Wallet Endpoint Tests"
Cohesion: 0.25
Nodes (17): Integration tests for the wallet endpoints (DVA provisioning)., POST /wallet/topup returns a Paystack-hosted URL and persists a     pending `Tra, End-to-end: POST /topup → simulate charge.success webhook → assert     the user', _signup(), test_create_telegram_link_code(), test_create_telegram_link_code_requires_auth(), test_provision_requires_auth(), test_provision_virtual_account_creates_dva() (+9 more)

### Community 34 - "Agent Decision Unit Tests"
Cohesion: 0.18
Nodes (16): decide(), Pure function. Returns the decision + human-readable reason.      `days_until_du, Tests for the LangGraph decision agent.  The rule (from `app/agents/nodes.py`):, The graph must produce the same answer as `decide()`., 4 days is strictly greater than the 3-day cutoff → schedule., Balance == total → not 'less than', so not HOLD., test_decide_boundary_4_days_is_schedule(), test_decide_exact_balance_does_not_hold() (+8 more)

### Community 35 - "Payment Provider Interface"
Cohesion: 0.12
Nodes (10): PaymentProvider, The contract every payment-gateway implementation must satisfy., Create a customer at the provider; return provider's customer_id/code., Issue a dedicated virtual account for `customer_code`., Look up the name on `account_number` at `bank_code`., Create a transfer recipient; return provider's recipient_code., Move `amount_kobo` (1 NGN = 100 kobo) from our balance to recipient., Return True iff `signature_header` is a valid HMAC of `raw_body`. (+2 more)

### Community 36 - "Project Architecture Concepts"
Cohesion: 0.14
Nodes (17): APScheduler Job Scheduler, AutoPay AI, Bills API, BVN Encryption, Deferred DVA Provisioning, FastAPI Application, JWT Authentication, KYC API (+9 more)

### Community 37 - "Wallet Transaction Tests"
Cohesion: 0.28
Nodes (14): _insert_txn(), Tests for `GET /api/v1/wallet/transactions`., User A can't see User B's transactions — the WHERE clause     filters by `user_i, Signup a new user via the API and return (access_token, user_id)., Insert a transaction row for `user_id` and return its id., No Bearer token → 401., _signup(), test_list_transactions_filter_by_type() (+6 more)

### Community 38 - "Paystack Provider Implementation"
Cohesion: 0.21
Nodes (3): PaystackProvider, Make a Paystack call and return the `data` payload.          Raises a typed `Pay, Start a hosted Checkout session. The user is redirected to         `authorizatio

### Community 39 - "FastAPI Application Entrypoint"
Cohesion: 0.19
Nodes (13): _http_metrics_middleware(), lifespan(), Request, FastAPI application entry point., Serve the SPA shell at the root path., serve_index(), Configure root logger with a sensible formatter., setup_logging() (+5 more)

### Community 40 - "Bill Endpoint Tests"
Cohesion: 0.34
Nodes (13): _auth_header(), Integration tests for the bills endpoints., The /upload endpoint with a `request_bill` form field should     create a bill a, test_cancel_bill(), test_create_bill_via_json(), test_get_bill_404_for_other_users_bill(), test_list_bills_returns_only_own(), test_pay_bill_402_on_insufficient_balance() (+5 more)

### Community 41 - "Handler Ordering Tests"
Cohesion: 0.15
Nodes (13): built_app(), _find_bill_conversation(), _find_topup_conversation(), Regression tests for handler-ordering bugs in the bot.  Background: PTB v21 walk, The topup ConversationHandler must be registered before the     bill Conversatio, End-to-end regression: build the real Application, link a     user to chat 99000, Build the real bot Application. Uses a dummy token; we never     initialize it (, Locate the bill ConversationHandler by inspecting the entry     point's text/pho (+5 more)

### Community 42 - "Webhook Payload Processing"
Cohesion: 0.23
Nodes (11): FastAPI routers — all HTTP endpoints under one namespace., _handle_charge_success(), _handle_dva_assigned(), _handle_transfer_update(), paystack_webhook(), Paystack webhook handler.  Mounted at /webhooks/paystack in `app.main`.  CRITICA, User's VA received money. Credit their wallet and update txn.      Idempotent: a, Our outbound transfer completed / failed / was reversed. (+3 more)

### Community 43 - "Bill Extraction & LLM Loaders"
Cohesion: 0.22
Nodes (9): BillExtractionResult, BillExtractionResult, What the loader + LLM extracted from the upload.      `due_date` is intentionall, _get_llm_client(), _llm_extract(), Return a Groq client wrapped by instructor, or None if no key.      The loaders, Call Groq with structured output. Returns None on any failure., Best-effort bill extraction when no LLM is available. (+1 more)

### Community 44 - "Name Matching Service"
Cohesion: 0.23
Nodes (11): names_match(), _normalize(), Fuzzy name matching for payee account validation.  When a user submits a bill fo, Lowercase, strip accents, drop suffixes + punctuation, collapse     whitespace., Return True iff `extracted` and `resolved` are likely the same     entity, per t, Unit tests for `app.services.name_match`.  Pure-Python (no DB) — the helper is f, test_names_match_accepts_subsidiary_variations(), test_names_match_custom_threshold() (+3 more)

### Community 45 - "Provider Stub & Webhook Events"
Cohesion: 0.18
Nodes (5): Stub parse_webhook that actually verifies the signature and         parses the r, A verified webhook from the provider.      `provider_reference` ties the event b, WebhookEvent, In-memory provider that records every call., _StubProvider

### Community 46 - "Payment Math Utilities"
Cohesion: 0.26
Nodes (11): _ngn_to_kobo(), ₦ → kobo (integer). Paystack wants whole kobo only., Tests for the payout service's money math.  These are pure unit tests — no DB, n, Pin the rule that we use Decimal (not float) for money math., test_decimal_arithmetic_for_balance(), test_ngn_to_kobo_fractional(), test_ngn_to_kobo_large(), test_ngn_to_kobo_rounds_nearest_kobo() (+3 more)

### Community 47 - "Database Engine & Sessions"
Cohesion: 0.20
Nodes (9): Session, _build_engine(), get_session(), init_db(), SQLAlchemy / SQLModel engine + session management., Create the SQLAlchemy engine with sane pool defaults for Postgres., Create all tables. Used in tests; production uses Alembic., FastAPI dependency that yields a session and closes it after the request. (+1 more)

### Community 48 - "KYC Endpoint Tests"
Cohesion: 0.42
Nodes (9): _auth_header(), Integration tests for the KYC endpoints., test_bvn_must_be_11_digits(), test_get_kyc_404_when_absent(), test_get_kyc_returns_status(), test_submit_bvn_409_on_duplicate(), test_submit_bvn_stores_encrypted(), Session (+1 more)

### Community 49 - "Smoke Tests"
Cohesion: 0.20
Nodes (9): Smoke tests — confirm the app skeleton imports and settings are valid.  These ru, Settings class can be constructed and exposes expected defaults., Pydantic should strip surrounding quotes from DATABASE_URL., Version literal is set in app/__init__.py., Helpers reflect the current environment.      Conftest sets ENVIRONMENT=test, so, test_app_version_is_set(), test_database_url_quoted_handling(), test_is_production_and_is_test_helpers() (+1 more)

### Community 50 - "Health Check Endpoints"
Cohesion: 0.22
Nodes (7): metrics(), Liveness and readiness probes.  - /healthz  liveness: process is up - /readyz, Exposes the registered `prometheus_client` collectors in     text/plain expositi, readyz(), Response, Session, JSONResponse

### Community 51 - "Logging Configuration"
Cohesion: 0.25
Nodes (6): Any, get_logger(), LoggerAdapter, Structured logging configuration.  Call `setup_logging()` once at app startup; e, Adapter that prefixes every message with a context dict (request_id, etc.)., Logger

### Community 52 - "Topup Initialization Stub"
Cohesion: 0.25
Nodes (5): Stub for hosted Checkout top-up. Records the call and         returns a fake aut, Start a hosted top-up flow. The user is redirected to the         returned `auth, Result of starting a top-up. The user is redirected to     `authorization_url`;, A provider-hosted top-up flow that the user is about to enter.      `authorizati, TopupInit

### Community 53 - "Start Command Tests"
Cohesion: 0.33
Nodes (6): start_command(), The /start output must list /transactions as a discoverable     command., `/start` should suggest `/topup` for new users., test_start_mentions_topup(), test_start_mentions_transactions(), test_start_sends_welcome_message()

### Community 54 - "Payment Provider Injection"
Cohesion: 0.33
Nodes (5): handle_final_confirm(), PaymentProvider, get_payment_provider(), Build the configured provider. Swapped in tests via `app.dependency_overrides`., RuntimeError

### Community 55 - "Alembic Migration Config"
Cohesion: 0.33
Nodes (5): Alembic environment configuration.  Reads DATABASE_URL from app settings so migr, Run migrations in 'offline' mode (emit SQL without a live connection)., Run migrations in 'online' mode (against a live DB connection)., run_migrations_offline(), run_migrations_online()

### Community 56 - "HTTP Client IP Helper"
Cohesion: 0.40
Nodes (4): Request, client_ip(), HTTP-related helpers used by multiple routers., Return the client's IP, or None if it isn't a valid IPv4/IPv6.      The `audit_l

### Community 57 - "Baseline Migration"
Cohesion: 0.40
Nodes (4): downgrade(), No-op. See module docstring., No-op. To wipe the schema, drop and recreate the database., upgrade()

### Community 58 - "Docker Compose Services"
Cohesion: 0.50
Nodes (4): Production App Service, Production Database Service, Test App Service, Test Database Service

### Community 59 - "Test Conftest Fixtures"
Cohesion: 0.50
Nodes (3): Shared pytest fixtures + test env setup.  This conftest runs before any test mod, Placeholder factory for tests that land in Chunk 2., sample_user_dict()

### Community 60 - "CI & Code Quality"
Cohesion: 0.50
Nodes (4): Coverage Reporting, pytest Test Runner, ruff Linter, CI Workflow

## Knowledge Gaps
- **27 isolated node(s):** `Session`, `JSONResponse`, `Response`, `Engine`, `Request` (+22 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `User` connect `Bills API Endpoints` to `Telegram Conversation Handlers`, `JWT Security & Token Management`, `Auth API Routes`, `KYC BVN Verification`, `Wallet API & Transactions`, `Telegram Handler Unit Tests`, `SPA Wallet Render Test`, `Telegram Notification Tests`, `LangGraph Decision Agent`, `Schedule Conversation Helpers`, `Bill Conversation Flow`, `Telegram Auth Handlers`, `Telegram Fake Message Tests`, `SQLModel Model Tests`, `Webhook Endpoint Tests`, `SQLModel ORM Models`, `Auto-Pay Scheduler Tests`, `Payout ACID Tests`, `Payout Name Validation`, `APScheduler Integration`, `Auth Endpoint Tests`, `Wallet Endpoint Tests`, `Agent Decision Unit Tests`, `Wallet Transaction Tests`, `Bill Endpoint Tests`, `Handler Ordering Tests`, `Webhook Payload Processing`, `Provider Stub & Webhook Events`, `KYC Endpoint Tests`?**
  _High betweenness centrality (0.184) - this node is a cross-community bridge._
- **Why does `Bill` connect `Auto-Pay Scheduler Tests` to `Telegram Conversation Handlers`, `Agent Decision Unit Tests`, `KYC BVN Verification`, `Bill Endpoint Tests`, `SPA Wallet Render Test`, `Telegram Notification Tests`, `LangGraph Decision Agent`, `Provider Stub & Webhook Events`, `Bill Conversation Flow`, `Bills API Endpoints`, `Telegram Auth Handlers`, `SQLModel Model Tests`, `Webhook Endpoint Tests`, `SQLModel ORM Models`, `Payout ACID Tests`, `Payout Name Validation`, `APScheduler Integration`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `session_scope()` connect `Telegram Auth Handlers` to `Telegram Conversation Handlers`, `Wallet Endpoint Tests`, `Wallet Transaction Tests`, `Telegram Handler Unit Tests`, `Topup Conversation Flow`, `SPA Wallet Render Test`, `Telegram Notification Tests`, `Schedule Conversation Helpers`, `Database Engine & Sessions`, `Bill Conversation Flow`, `Telegram Fake Message Tests`, `Payment Provider Injection`, `Auto-Pay Scheduler Tests`, `Payout Name Validation`, `APScheduler Integration`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 100 inferred relationships involving `User` (e.g. with `AgentState` and `TelegramLinkCodeResponse`) actually correct?**
  _`User` has 100 INFERRED edges - model-reasoned connections that need verification._
- **Are the 48 inferred relationships involving `Bill` (e.g. with `AgentState` and `Bill`) actually correct?**
  _`Bill` has 48 INFERRED edges - model-reasoned connections that need verification._
- **Are the 49 inferred relationships involving `Transaction` (e.g. with `ProvisionResponse` and `TopupRequest`) actually correct?**
  _`Transaction` has 49 INFERRED edges - model-reasoned connections that need verification._
- **Are the 60 inferred relationships involving `AuditActor` (e.g. with `TelegramLinkCodeResponse` and `ProvisionResponse`) actually correct?**
  _`AuditActor` has 60 INFERRED edges - model-reasoned connections that need verification._