# main.py
from fastapi import FastAPI, Request, Response
from api.billRoute import router
from api.authRoute import router as authRouter
from api.webHookRoute import router as webhookRouter
from core.database import init_db
from handlers import start_command, link_command, build_bill_conversation
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os
from handlers.auth import wallet_command
import datetime
from core.scheduler import process_scheduled_bills


load_dotenv(dotenv_path='.env')

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

ptb_app = Application.builder().token(TOKEN).build()

# ── Register handlers ──────────────────────────────────────────────
ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(CommandHandler("link", link_command))
ptb_app.add_handler(CommandHandler("wallet", wallet_command))   
ptb_app.add_handler(build_bill_conversation())


# ── Lifespan ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    from core.scheduler import scheduler

    scheduler.add_job(
        process_scheduled_bills,
        trigger="interval",
        hours=24,
        id="scheduled_bills",
        replace_existing=True,
        next_run_time=datetime.datetime.now()
    )
    scheduler.start()
    


    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.bot.set_webhook(url=WEBHOOK_URL)
    logger.info(f"Webhook set to: {WEBHOOK_URL}")

    yield

    await ptb_app.bot.delete_webhook()
    await ptb_app.stop()
    await ptb_app.shutdown()
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://autopayaiagent.netlify.app",
        "https://auto-pay-ai-production.up.railway.app"
    ],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(authRouter)
app.include_router(webhookRouter)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        req_json = await request.json()
        update = Update.de_json(req_json, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
    return Response(status_code=200)


@app.get("/")
def health_check():
    return {"status": "alive", "agent": "AutoPay AI"}
