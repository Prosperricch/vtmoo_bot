# app.py
import os
import re
import threading
from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import telebot
from telebot import types

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ===================== DATABASE CONFIG =====================
# HARDCODED FOR LOCAL TESTING ONLY — move to env vars before production
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres.hmhztencjtycadsmodif:V9syIHsOdN015qNf@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['SQLALCHEMY_ECHO'] = False

db = SQLAlchemy(app)

# ===================== CONFIG =====================
# HARDCODED FOR LOCAL TESTING ONLY — move to env vars before production
BOT_TOKEN = "8828586999:AAH2o_6ch_Il3vw563UuOn3zrT2uA3IMplY"
PUBLIC_URL = "https://your-ngrok-url.ngrok-free.app/dashboard?telegram_id=5966603094"  # ← update this after starting ngrok

bot = telebot.TeleBot(BOT_TOKEN)


# ===================== USER MODEL =====================
class User(db.Model):
    __tablename__ = 'vtmoo_users'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False, index=True)

    username = db.Column(db.String(80), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)

    transaction_pin = db.Column(db.String(256), nullable=True)  # Hashed 4-digit PIN
    balance = db.Column(db.Float, default=0.0)

    is_active = db.Column(db.Boolean, default=True)
    is_verified = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    transaction_history = db.Column(db.JSON, default=list)

    def add_transaction(self, amount, transaction_type, description, status="success"):
        transaction = {
            "id": len(self.transaction_history) + 1,
            "date": datetime.utcnow().isoformat(),
            "amount": float(amount),
            "type": transaction_type,
            "description": description,
            "status": status,
            "balance_after": round(self.balance, 2)
        }
        history = list(self.transaction_history or [])
        history.append(transaction)
        if len(history) > 100:
            history = history[-100:]
        self.transaction_history = history
        db.session.commit()

    def set_pin(self, pin: str):
        self.transaction_pin = generate_password_hash(pin)

    def check_pin(self, pin: str) -> bool:
        if not self.transaction_pin:
            return False
        return check_password_hash(self.transaction_pin, pin)


# ===================== HELPERS =====================
def get_or_create_user(telegram_id, username, name):
    with app.app_context():
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                name=name
            )
            db.session.add(user)
            db.session.commit()

            bot.send_message(
                telegram_id,
                "✅ Welcome to VTMoo Virtual Top-up!\n\n"
                "As a new user, please set your **4-digit** transaction PIN. \n\n"
                "It should be something you can remember:"
            )
            bot.register_next_step_handler_by_chat_id(telegram_id, process_first_pin)
        else:
            if not user.transaction_pin:
                bot.send_message(telegram_id, "🔑 Please set your **4-digit** transaction PIN:")
                bot.register_next_step_handler_by_chat_id(telegram_id, process_first_pin)
            else:
                send_dashboard_link(telegram_id)
    return user


def send_dashboard_link(telegram_id):
    dashboard_url = f"{PUBLIC_URL}/dashboard?telegram_id={telegram_id}"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📊 Open Dashboard", url=dashboard_url))
    bot.send_message(telegram_id, "Welcome back! Tap below to view your dashboard:", reply_markup=markup)


def process_first_pin(message):
    pin = message.text.strip()

    if not re.match(r'^\d{4}$', pin):
        bot.send_message(message.chat.id, "❌ PIN must be exactly **4 digits**. Please try again:")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_first_pin)
        return

    with app.app_context():
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if user:
            user.set_pin(pin)
            db.session.commit()
            bot.send_message(message.chat.id, "✅ PIN set successfully!\n\nYou can now use the bot.")
            send_dashboard_link(message.chat.id)


# ===================== BOT COMMANDS =====================
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )


@bot.message_handler(commands=['dashboard'])
def dashboard_command(message):
    send_dashboard_link(message.chat.id)


@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(message.chat.id,
                     "📋 **VTMoo Commands**\n\n"
                     "/start - Start the bot\n"
                     "/dashboard - View your dashboard\n"
                     "/help - Show this message\n\n"
                     "More features coming soon...")


# ===================== FLASK ROUTES =====================
@app.route('/dashboard')
def dashboard():
    telegram_id = request.args.get('telegram_id')

    if not telegram_id:
        return "Missing telegram_id", 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return "User not found", 404

    # Most recent transactions first, limit to last 5
    recent_transactions = list(reversed(user.transaction_history or []))[:5]

    return render_template(
        'dashboardd.html',
        user=user,
        recent_transactions=recent_transactions
    )


# ===================== FLASK SETUP =====================
with app.app_context():
    db.create_all()
    print("✅ vtmoo Database tables created successfully!")


def run_bot():
    print("🤖 Telegram Bot is Running...")
    bot.infinity_polling()


if __name__ == '__main__':
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()

    print("🚀 Flask + Telegram Bot Started!")

    # Use debug=False to avoid multiple bot instances
    app.run(debug=False, host="0.0.0.0", port=5000)
