import os
import re
import hmac
import hashlib
import threading
import time
from functools import wraps
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
import telebot
from telebot import types

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'vtmoo-dev-secret-change-me')

# ===================== DATABASE CONFIG =====================
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres.hmhztencjtycadsmodif:V9syIHsOdN015qNf@aws-1-eu-west-1.pooler.supabase.com:5432/postgres'
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config['SQLALCHEMY_ECHO'] = False

db = SQLAlchemy(app)

# ===================== CONFIG =====================
BOT_TOKEN    = os.environ.get('BOT_TOKEN', "8828586999:AAH2o_6ch_Il3vw563UuOn3zrT2uA3IMplY")
PUBLIC_URL   = os.environ.get('PUBLIC_URL', 'https://your-app-name.onrender.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')

# ===================== CHANNEL CONFIG =====================
CHANNEL_URL = os.environ.get('CHANNEL_URL', 'https://t.me/+5Tocjkgd5PdjNTU0')

# ===================== DATA SUPPLIER CONFIG =====================
DATA_SUPPLIER_BASE_URL = os.environ.get('DATA_SUPPLIER_BASE_URL', '')
DATA_SUPPLIER_API_KEY  = os.environ.get('DATA_SUPPLIER_API_KEY', '')

# ===================== PAYSTACK CONFIG =====================
PAYSTACK_SECRET_KEY  = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY  = os.environ.get('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_BASE_URL    = 'https://api.paystack.co'
FUNDING_FEE_PERCENT  = float(os.environ.get('FUNDING_FEE_PERCENT', '0.05'))

bot = telebot.TeleBot(BOT_TOKEN)


# ===================== KEEP-ALIVE =====================
# Pings the app every 14 minutes so Render free tier never sleeps.
# PUBLIC_URL must be set to your Render URL (e.g. https://vtmoo.onrender.com).
def _keep_alive():
    # Wait 30 s at startup so the server is fully up before the first ping.
    time.sleep(30)
    while True:
        try:
            if PUBLIC_URL and 'your-app-name' not in PUBLIC_URL:
                requests.get(f"{PUBLIC_URL}/health", timeout=10)
        except Exception:
            pass          # silently ignore — network hiccups are fine
        time.sleep(840)  # 14 minutes  (Render sleeps after 15 min of no traffic)

_keep_alive_thread = threading.Thread(target=_keep_alive, name='keep-alive', daemon=True)
_keep_alive_thread.start()
# ======================================================


# ===================== USER MODEL =====================
class User(db.Model):
    __tablename__ = 'vtmoo_users'

    id            = db.Column(db.Integer, primary_key=True)
    telegram_id   = db.Column(db.BigInteger, unique=True, nullable=False, index=True)
    username      = db.Column(db.String(80),  unique=True, nullable=True)
    name          = db.Column(db.String(100), nullable=True)
    phone         = db.Column(db.String(20),  unique=True, nullable=True, index=True)
    school        = db.Column(db.String(150), nullable=True)

    # 'regular' | 'student'
    user_type         = db.Column(db.String(20),  default='regular')
    pricing_suspended = db.Column(db.Boolean,      default=False)

    transaction_pin = db.Column(db.String(256), nullable=True)
    # True while we're waiting for the user to send their first PIN via Telegram
    pin_pending     = db.Column(db.Boolean, default=False)
    balance         = db.Column(db.Float, default=0.0)

    is_active   = db.Column(db.Boolean, default=True)
    is_verified = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    transaction_history = db.Column(db.JSON, default=list)

    def add_transaction(self, amount, transaction_type, description, status="success"):
        transaction = {
            "id":            len(self.transaction_history) + 1,
            "date":          datetime.utcnow().isoformat(),
            "amount":        float(amount),
            "type":          transaction_type,
            "description":   description,
            "status":        status,
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


# ===================== NOTIFICATION MODEL =====================
class Notification(db.Model):
    __tablename__ = 'vtmoo_notifications'

    id          = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, nullable=False, index=True)
    type        = db.Column(db.String(20), default='info')
    title       = db.Column(db.String(150), nullable=False)
    body        = db.Column(db.Text, nullable=True)
    read        = db.Column(db.Boolean, default=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


def create_notification(telegram_id, title, body="", ntype="info"):
    notif = Notification(telegram_id=telegram_id, title=title, body=body, type=ntype)
    db.session.add(notif)
    db.session.commit()
    return notif


# ===================== PERK APPLICATION MODEL =====================
class PerkApplication(db.Model):
    __tablename__ = 'vtmoo_perk_applications'

    id               = db.Column(db.Integer, primary_key=True)
    telegram_id      = db.Column(db.BigInteger, nullable=False, index=True)
    school           = db.Column(db.String(150), nullable=False)
    matric_number    = db.Column(db.String(50),  nullable=False)
    level            = db.Column(db.String(20),  nullable=False)
    status           = db.Column(db.String(20),  default='pending')
    rejection_reason = db.Column(db.String(255), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== PERKS SETTINGS MODEL =====================
class PerksSettings(db.Model):
    __tablename__ = 'vtmoo_perks_settings'

    id                 = db.Column(db.Integer, primary_key=True)
    applications_open  = db.Column(db.Boolean, default=True)
    total_spots        = db.Column(db.Integer,  default=40)
    show_spots         = db.Column(db.Boolean, default=True)
    allow_applications = db.Column(db.Boolean, default=True)
    updated_at         = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        """Always returns the single settings row, creating it if needed."""
        s = cls.query.first()
        if not s:
            s = cls()
            db.session.add(s)
            db.session.commit()
        return s


# ===================== NETWORK MODEL =====================
class Network(db.Model):
    __tablename__ = 'vtmoo_networks'

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(50), unique=True, nullable=False)
    provider_id   = db.Column(db.String(80), nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== DATA PLAN MODEL =====================
class DataPlan(db.Model):
    __tablename__ = 'vtmoo_data_plans'

    id               = db.Column(db.Integer, primary_key=True)
    supplier_plan_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    network          = db.Column(db.String(20), nullable=False, index=True)
    plan_name        = db.Column(db.String(30), nullable=False)
    plan_type        = db.Column(db.String(50), nullable=False)
    duration         = db.Column(db.String(50), nullable=False)
    wholesale_price  = db.Column(db.Float, default=0.0)
    regular_price    = db.Column(db.Float, default=0.0)
    student_price    = db.Column(db.Float, default=0.0)
    is_active        = db.Column(db.Boolean, default=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== PAYSTACK TRANSACTION MODEL =====================
class PaystackTransaction(db.Model):
    __tablename__ = 'vtmoo_paystack_transactions'

    id            = db.Column(db.Integer, primary_key=True)
    telegram_id   = db.Column(db.BigInteger, nullable=False, index=True)
    reference     = db.Column(db.String(100), unique=True, nullable=False, index=True)
    amount        = db.Column(db.Float, nullable=False)
    fee           = db.Column(db.Float, default=0.0)
    total_charged = db.Column(db.Float, nullable=False)
    status        = db.Column(db.String(20), default='pending')
    channel       = db.Column(db.String(30), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== HELPERS =====================
def prompt_pin_setup(telegram_id, is_new_user=False):
    """Mark user as pin_pending in DB, then ask them to send their PIN."""
    with app.app_context():
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if user and not user.transaction_pin:
            user.pin_pending = True
            db.session.commit()
    if is_new_user:
        bot.send_message(
            telegram_id,
            "✅ *Welcome to VTMoo Virtual Top-up!*\n\n"
            "To get started, please set a *4-digit* transaction PIN.\n"
            "You will use this PIN to confirm every purchase.\n\n"
            "👇 Reply with your 4-digit PIN now:",
            parse_mode="Markdown"
        )
    else:
        bot.send_message(
            telegram_id,
            "🔑 You haven't set a transaction PIN yet.\n\n"
            "Please reply with your *4-digit* PIN to continue:",
            parse_mode="Markdown"
        )


def get_or_create_user(telegram_id, username, name):
    with app.app_context():
        user = User.query.filter_by(telegram_id=telegram_id).first()
        if not user:
            user = User(telegram_id=telegram_id, username=username, name=name, pin_pending=True)
            db.session.add(user)
            db.session.commit()
            prompt_pin_setup(telegram_id, is_new_user=True)
        else:
            if not user.transaction_pin:
                prompt_pin_setup(telegram_id, is_new_user=False)
            else:
                user.pin_pending = False
                db.session.commit()
                send_dashboard_link(telegram_id)
    return user


def send_dashboard_link(telegram_id):
    """
    Sends the 'Welcome back' message with the dashboard button, and follows
    it up with a prompt encouraging the user to join the official channel.
    """
    dashboard_url = f"{PUBLIC_URL}/dashboard?telegram_id={telegram_id}"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "📊 Open Dashboard",
        web_app=types.WebAppInfo(url=dashboard_url)
    ))
    bot.send_message(telegram_id, "Welcome back! Tap below to view your dashboard:", reply_markup=markup)

    # ── CHANNEL FOLLOW PROMPT ──
    send_channel_prompt(telegram_id)


def send_channel_prompt(telegram_id):
    """Sends a follow-up message inviting the user to join the official channel."""
    channel_markup = types.InlineKeyboardMarkup()
    channel_markup.add(types.InlineKeyboardButton(
        "📢 Join Our Channel",
        url=CHANNEL_URL
    ))
    bot.send_message(
        telegram_id,
        "📢 *Stay updated!*\n\n"
        "Join our official channel for announcements, price updates, "
        "and important news about VTMoo:",
        reply_markup=channel_markup,
        parse_mode="Markdown"
    )


def get_telegram_id_from_request(data):
    telegram_id = data.get('telegram_id')
    if telegram_id is None:
        return None
    try:
        return int(telegram_id)
    except (TypeError, ValueError):
        return None


def relative_time(dt):
    if not dt:
        return ""
    now     = datetime.utcnow()
    seconds = (now - dt).total_seconds()
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    if days < 7:
        return f"{days}d ago"
    return dt.strftime('%d %b %Y')


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return view_func(*args, **kwargs)
    return wrapped


def call_data_supplier_api(plan, phone, network):
    if not DATA_SUPPLIER_BASE_URL or not DATA_SUPPLIER_API_KEY:
        app.logger.error("Data supplier API not configured")
        return {"ok": False, "error_type": "request_failed",
                "message": "Data supplier API not configured.", "supplier_reference": None}
    try:
        resp = requests.post(
            f"{DATA_SUPPLIER_BASE_URL}/data/purchase",
            headers={"Authorization": f"Bearer {DATA_SUPPLIER_API_KEY}",
                     "Content-Type": "application/json"},
            json={"network": network, "plan_id": plan.supplier_plan_id, "phone": phone},
            timeout=20
        )
        payload = resp.json()
    except Exception as e:
        app.logger.exception(f"Data supplier request failed: {e}")
        return {"ok": False, "error_type": "request_failed",
                "message": str(e), "supplier_reference": None}

    status  = (payload.get('status') or '').lower()
    message = (payload.get('message') or '')

    if status in ('success', 'successful', 'completed'):
        return {"ok": True, "error_type": None, "message": message,
                "supplier_reference": payload.get('reference') or payload.get('order_id')}

    lowered = message.lower()
    if 'insufficient balance' in lowered or 'insufficient funds' in lowered or status in ('insufficient_balance',):
        return {"ok": False, "error_type": "insufficient_wholesale_balance",
                "message": message, "supplier_reference": None}

    return {"ok": False, "error_type": "rejected",
            "message": message or "Supplier rejected the request.", "supplier_reference": None}


def paystack_verify_transaction(reference):
    if not PAYSTACK_SECRET_KEY:
        app.logger.error("PAYSTACK_SECRET_KEY not configured")
        return None
    try:
        resp = requests.get(
            f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            timeout=15
        )
        payload = resp.json()
    except Exception as e:
        app.logger.exception(f"Paystack verify failed: {e}")
        return None
    if not payload.get('status'):
        return None
    return payload.get('data')


def credit_wallet_for_reference(reference, paystack_data):
    txn = PaystackTransaction.query.filter_by(reference=reference).first()
    if not txn:
        return False, "Transaction record not found.", None
    if txn.status == 'success':
        user = User.query.filter_by(telegram_id=txn.telegram_id).first()
        return True, "Already credited.", user

    gateway_status = (paystack_data.get('status') or '').lower()
    if gateway_status != 'success':
        txn.status = 'failed'
        txn.updated_at = datetime.utcnow()
        db.session.commit()
        return False, f"Payment not successful (status: {gateway_status}).", None

    user = User.query.filter_by(telegram_id=txn.telegram_id).first()
    if not user:
        txn.status = 'failed'
        db.session.commit()
        return False, "User not found.", None

    user.balance       = round((user.balance or 0.0) + txn.amount, 2)
    txn.status         = 'success'
    txn.channel        = (paystack_data.get('channel') or '')[:30]
    txn.updated_at     = datetime.utcnow()
    db.session.commit()

    user.add_transaction(
        amount=txn.amount,
        transaction_type="wallet_funding",
        description=f"Wallet funded via Paystack ({txn.channel or 'card'})"
    )
    create_notification(
        user.telegram_id,
        "Wallet funded successfully",
        f"Your wallet was credited with ₦{txn.amount:,.2f}. New balance: ₦{user.balance:,.2f}.",
        ntype="success"
    )
    return True, "Wallet credited successfully.", user


NETWORK_IMAGES = {
    'MTN':     '3d-mtn.jpg',
    'AIRTEL':  '3d-airtel.jpg',
    'GLO':     '3d-glo.jpg',
    '9MOBILE': '3d-9mobile.jpg',
}

PLAN_TYPE_LABELS = {
    'sme':       'SME',
    'gifting':   'Gifting',
    'corporate': 'Corporate',
    'awoof':     'Awoof',
}


# ===================== BOT COMMAND MENU =====================
# These show up in Telegram's "/" menu (the little [ / ] button next to the
# message box) so users can discover available commands without /help.
BOT_COMMANDS = [
    types.BotCommand("start", "Start the bot / open your account"),
    types.BotCommand("dashboard", "Open your VTMoo dashboard"),
    types.BotCommand("setpin", "Set or reset your transaction PIN"),
    types.BotCommand("channel", "Join our official announcement channel"),
    types.BotCommand("help", "Show all available commands"),
]


def register_bot_commands():
    """Registers the command menu with Telegram so it appears in the UI."""
    try:
        bot.set_my_commands(BOT_COMMANDS)
        app.logger.info("✅ Bot command menu registered.")
    except Exception as e:
        app.logger.warning(f"Could not register bot commands: {e}")


# ===================== BOT COMMANDS =====================
@bot.message_handler(commands=['start'])
def start(message):
    get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)


@bot.message_handler(commands=['setpin'])
def setpin_command(message):
    """Allow any user to (re-)enter the PIN setup flow on demand."""
    with app.app_context():
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if not user:
            bot.send_message(message.chat.id,
                             "You don't have an account yet. Send /start to create one.")
            return
        if user.transaction_pin:
            bot.send_message(message.chat.id,
                             "You already have a PIN set.\n"
                             "To change it, use the Profile section in your dashboard.")
            return
        prompt_pin_setup(message.from_user.id, is_new_user=False)


@bot.message_handler(commands=['dashboard'])
def dashboard_command(message):
    with app.app_context():
        user = User.query.filter_by(telegram_id=message.from_user.id).first()
        if not user or not user.transaction_pin:
            prompt_pin_setup(message.from_user.id, is_new_user=(user is None))
            return
    send_dashboard_link(message.chat.id)


@bot.message_handler(commands=['channel'])
def channel_command(message):
    """Lets a user pull up the channel invite at any time."""
    send_channel_prompt(message.chat.id)


@bot.message_handler(command
