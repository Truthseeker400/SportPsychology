# -*- coding: utf-8 -*-
"""
An anonymous, code-based Telegram bot that guides players through a sequential
series of prompts to foster psychological safety.

This script implements the following logic:
1.  Connects to a PostgreSQL database with an anonymous structure.
2.  Handles a code-based registration flow for new users.
3.  For registered players, a /prompts command starts a conversation that sends
    all relevant prompts one by one.
4.  Players can respond to each prompt or choose to 'Stop' the sequence.
5.  All responses are logged anonymously to the database.
6.  The weekly scheduler has been removed in favor of this on-demand flow.
"""

import os
import logging
import psycopg2
import pytz
from datetime import datetime

from telegram import ReplyKeyboardMarkup, Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    ConversationHandler,
)
from dotenv import load_dotenv

# --- Configuration & Setup ---
load_dotenv()

# Securely load credentials from .env file
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_NAME = os.getenv("DB_NAME", "Simple_Rugby_Bot_v1")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "MAtc1970!?!")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

# Check for missing bot token
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found! Please check your .env file.")

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State Management for Conversations ---
# States for the two separate conversations
AWAITING_CODE, AWAITING_RESPONSE = range(2)
# Dictionary to hold active prompt sessions for players
prompt_sessions = {}


# --- Database Management ---

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
        )
        return conn
    except psycopg2.OperationalError as e:
        logger.error(f"Could not connect to the database: {e}")
        return None


def get_code_details(conn, code):
    """Checks if a registration code is valid and returns its details."""
    with conn.cursor() as cur:
        cur.execute("SELECT player_role, is_claimed FROM registration_codes WHERE code = %s;", (code,))
        result = cur.fetchone()
        if result:
            return {"role": result[0], "is_claimed": result[1]}
    return None


def claim_code_and_create_player(conn, code, chat_id, player_role):
    """Creates a new anonymous player and marks their registration code as claimed."""
    is_veteran = True if player_role == 'veteran' else False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO players (registration_code, is_veteran, telegram_chat_id) VALUES (%s, %s, %s);",
                (code, is_veteran, str(chat_id))
            )
            cur.execute(
                "UPDATE registration_codes SET is_claimed = TRUE, claimed_by_chat_id = %s WHERE code = %s;",
                (str(chat_id), code)
            )
        conn.commit()
        logger.info(f"Successfully registered player with chat_id {chat_id} using code {code}.")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to register player for chat_id {chat_id}: {e}")
        return False


def get_player_by_chat_id(conn, chat_id):
    """Retrieves a player's details using their Telegram chat ID."""
    with conn.cursor() as cur:
        cur.execute('SELECT id, is_veteran FROM players WHERE telegram_chat_id = %s;', (str(chat_id),))
        result = cur.fetchone()
        if result:
            return {"id": result[0], "is_veteran": result[1]}
    return None


def get_all_prompts_for_player(conn, player):
    """Fetches all relevant prompts for a player, ordered by week."""
    player_role = "veteran" if player['is_veteran'] else "rookie"
    prompts = []
    with conn.cursor() as cur:
        sql = """
              SELECT id, prompt_text, options \
              FROM prompts
              WHERE target_players IN (%s, 'all') \
              ORDER BY week, id; \
              """
        cur.execute(sql, (player_role,))
        results = cur.fetchall()
        for row in results:
            prompts.append({"id": row[0], "text": row[1], "options": row[2]})
    logger.info(f"Found {len(prompts)} total prompts for player ID {player['id']}.")
    return prompts


def log_response(conn, player_id, prompt_id, selected_option):
    """Logs a player's response to the database."""
    try:
        with conn.cursor() as cur:
            sql = 'INSERT INTO responses (player_id, prompt_id, selected_option, "timestamp") VALUES (%s, %s, %s, %s);'
            cur.execute(sql, (player_id, prompt_id, selected_option, datetime.now()))
        conn.commit()
        logger.info(f"Logged response from player {player_id} for prompt {prompt_id}.")
    except Exception as e:
        logger.error(f"Error logging response: {e}")
        conn.rollback()


# --- Bot Actions & Handlers ---

async def send_prompt(context: CallbackContext, chat_id: str, prompt: dict):
    """Sends a prompt with a custom keyboard that includes a 'Stop' button."""
    options_with_stop = prompt['options'] + [['Stop']]
    keyboard = ReplyKeyboardMarkup(options_with_stop, one_time_keyboard=True, resize_keyboard=True)
    await context.bot.send_message(chat_id=chat_id, text=prompt['text'], reply_markup=keyboard)
    logger.info(f"Sent prompt ID {prompt['id']} to chat_id {chat_id}.")


# --- Registration Conversation ---
async def start(update: Update, context: CallbackContext) -> int:
    """Handles /start. Checks registration and either starts registration flow or directs user to /prompts."""
    chat_id = str(update.message.chat_id)
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("Database is unavailable. Please try again later.")
        return ConversationHandler.END

    player = get_player_by_chat_id(conn, chat_id)
    conn.close()

    if player:
        await update.message.reply_text(
            "Welcome back! You are already registered. Type /prompts to begin the question sequence.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Welcome! To ensure anonymity, please enter your unique registration code.")
        return AWAITING_CODE


async def handle_registration_code(update: Update, context: CallbackContext) -> int:
    """Validates registration code and creates a new player."""
    chat_id = str(update.message.chat_id)
    user_code = update.message.text.strip().upper()

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("Database error. Please try again later.")
        return ConversationHandler.END

    code_details = get_code_details(conn, user_code)

    if code_details and not code_details['is_claimed']:
        if claim_code_and_create_player(conn, user_code, chat_id, code_details['role']):
            await update.message.reply_text("Registration successful! Type /prompts to begin.")
            conn.close()
            return ConversationHandler.END
    else:
        await update.message.reply_text("Sorry, that code is invalid or has been used. Please try again.")
        conn.close()
        return AWAITING_CODE


# --- Prompt Sequence Conversation ---
async def start_prompts(update: Update, context: CallbackContext) -> int:
    """Starts the sequential prompt session for a registered player."""
    chat_id = str(update.message.chat_id)
    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("Database is unavailable.")
        return ConversationHandler.END

    player = get_player_by_chat_id(conn, chat_id)
    if not player:
        await update.message.reply_text("You are not registered. Please use /start to register first.")
        conn.close()
        return ConversationHandler.END

    all_prompts = get_all_prompts_for_player(conn, player)
    conn.close()

    if not all_prompts:
        await update.message.reply_text("There are no prompts available for you right now.")
        return ConversationHandler.END

    # Store the session data for this player
    prompt_sessions[chat_id] = {'prompts': all_prompts, 'current_index': 0, 'player_id': player['id']}

    await update.message.reply_text(f"Starting the prompt sequence. There are {len(all_prompts)} questions.")
    await send_prompt(context, chat_id, all_prompts[0])
    return AWAITING_RESPONSE


async def handle_prompt_response(update: Update, context: CallbackContext) -> int:
    """Logs a response and sends the next prompt in the sequence."""
    chat_id = str(update.message.chat_id)
    user_response = update.message.text

    if user_response == 'Stop':
        return await stop_prompts(update, context)

    if chat_id not in prompt_sessions:
        await update.message.reply_text("No active session found. Type /prompts to begin.")
        return ConversationHandler.END

    session = prompt_sessions[chat_id]
    current_prompt = session['prompts'][session['current_index']]

    conn = get_db_connection()
    if not conn:
        await update.message.reply_text("Database error, could not log response.")
        return ConversationHandler.END

    log_response(conn, session['player_id'], current_prompt['id'], user_response)
    conn.close()

    session['current_index'] += 1
    if session['current_index'] < len(session['prompts']):
        next_prompt = session['prompts'][session['current_index']]
        await send_prompt(context, chat_id, next_prompt)
        return AWAITING_RESPONSE
    else:
        await update.message.reply_text("You have completed all prompts. Thank you!",
                                        reply_markup=ReplyKeyboardRemove())
        del prompt_sessions[chat_id]
        return ConversationHandler.END


async def stop_prompts(update: Update, context: CallbackContext) -> int:
    """Stops the prompt sequence."""
    chat_id = str(update.message.chat_id)
    if chat_id in prompt_sessions:
        del prompt_sessions[chat_id]
    await update.message.reply_text("Prompt sequence stopped. Type /prompts to start again.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    """Main function to set up and run the bot."""
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for the initial user registration
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            AWAITING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_registration_code)]
        },
        fallbacks=[CommandHandler('stop', stop_prompts)]  # Use same stop command
    )

    # Conversation handler for the main prompt sequence
    prompts_handler = ConversationHandler(
        entry_points=[CommandHandler('prompts', start_prompts)],
        states={
            AWAITING_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_prompt_response)]
        },
        fallbacks=[CommandHandler('stop', stop_prompts)]
    )

    application.add_handler(registration_handler)
    application.add_handler(prompts_handler)

    logger.info("Bot is polling for messages...")
    application.run_polling()


if __name__ == '__main__':
    main()








