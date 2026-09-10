"""
bot.py — The Telegram side of MomVoice AI.

This is the file you actually run. It:
1. Connects to Telegram using your bot token.
2. For every incoming message, builds the agents (agent.py) for that
   specific parent, and runs the message through ADK's Runner.
3. Sends the agent's final reply back to the parent on Telegram.

Run it with:  python bot.py
"""

import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import build_root_agent, build_onboarding_agent
import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

session_service = InMemorySessionService()
APP_NAME = "momvoice_ai"

WELCOME_MESSAGE = (
    "👋 Welcome to MomVoice AI!\n\n"
    "I'm here to help you track your baby's feeding, sleep, diaper changes, "
    "and milestones — just by chatting naturally, no forms to fill out. "
    "You can also ask me everyday parenting questions any time.\n\n"
    "Before we start, tell me your baby's name, date of birth, and gender "
    "(or 'prefer not to say' for gender)."
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start — Telegram shows this as the first interaction when
    someone opens the bot. We send a proper introduction here."""
    await update.message.reply_text(WELCOME_MESSAGE)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text
    user_id_str = str(chat_id)

    logger.info(f"Message from {chat_id}: {user_message}")

    try:
        if db.has_baby_info(chat_id):
            root_agent = build_root_agent(telegram_chat_id=chat_id)
        else:
            root_agent = build_onboarding_agent(telegram_chat_id=chat_id)

        runner = Runner(
            app_name=APP_NAME,
            agent=root_agent,
            session_service=session_service,
        )

        session_id = user_id_str
        existing = await session_service.get_session(
            app_name=APP_NAME, user_id=user_id_str, session_id=session_id
        )
        if existing is None:
            await session_service.create_session(
                app_name=APP_NAME, user_id=user_id_str, session_id=session_id
            )

        content = types.Content(role="user", parts=[types.Part(text=user_message)])

        final_response = None
        async for event in runner.run_async(
            user_id=user_id_str, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_response = event.content.parts[0].text

        await update.message.reply_text(
            final_response or "Sorry, I didn't quite catch that — try again?"
        )

    except Exception:
        logger.exception(f"Error handling message from {chat_id}")
        await update.message.reply_text(
            "Sorry, something went wrong on my end — could you try that again? "
            "If it keeps happening, try rephrasing your message."
        )


class _HealthCheckHandler(BaseHTTPRequestHandler):
    """A tiny HTTP server with no real logic — it exists only so Cloud
    Run's startup check sees something listening on PORT. The actual
    bot logic still runs via Telegram polling, exactly like on your
    laptop. This avoids Telegram's webhook-registration entirely,
    which turned out to be fragile in a fresh container."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"MomVoice AI is running")

    def log_message(self, format, *args):
        pass


def _start_health_check_server(port: int):
    server = HTTPServer(("0.0.0.0", port), _HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server listening on port {port}")


def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # RUN_MODE=cloud_run (used in Cloud Run) starts a tiny health-check
    # server alongside normal Telegram polling, so Cloud Run sees the
    # container as healthy without needing Telegram webhook setup.
    if os.environ.get("RUN_MODE") == "cloud_run":
        port = int(os.environ.get("PORT", 8080))
        _start_health_check_server(port)

    logger.info("MomVoice AI bot starting (polling mode)...")
    app.run_polling()


if __name__ == "__main__":
    main()
