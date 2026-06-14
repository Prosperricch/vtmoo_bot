import os
import re
import hmac
import hashlib
import threading
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

# ===================== DATA SUPPLIER CONFIG =====================
DATA_SUPPLIER_BASE_URL = os.environ.get('DATA_SUPPLIER_BASE_URL', '')
DATA_SUPPLIER_API_KEY  = os.environ.get('DATA_SUPPLIER_API_KEY', '')

# ===================== PAYSTACK CONFIG =====================
PAYSTACK_SECRET_KEY  = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY  = os.environ.get('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_BASE_URL    = 'https://api.paystack.co'
FUNDING_FEE_PERCENT  = float(os.environ.get('FUNDING_FEE_PERCENT', '0.05'))

bot = telebot.TeleBot(BOT_TOKEN)


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
    dashboard_url = f"{PUBLIC_URL}/dashboard?telegram_id={telegram_id}"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "📊 Open Dashboard",
        web_app=types.WebAppInfo(url=dashboard_url)
    ))
    bot.send_message(telegram_id, "Welcome back! Tap below to view your dashboard:", reply_markup=markup)


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


@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(message.chat.id,
                     "📋 *VTMoo Commands*\n\n"
                     "/start — Start or re-register\n"
                     "/dashboard — Open your dashboard\n"
                     "/setpin — Set your transaction PIN\n"
                     "/help — Show this message",
                     parse_mode="Markdown")


@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_any_text(message):
    """
    Persistent PIN collection: intercepts every text message.
    If the user has pin_pending=True (no PIN set yet), treat their
    message as a PIN attempt regardless of when they send it —
    even after a bot restart.
    """
    # Ignore messages that are commands (they're handled above)
    if message.text and message.text.startswith('/'):
        return

    with app.app_context():
        user = User.query.filter_by(telegram_id=message.from_user.id).first()

        # Unknown user — ask them to /start
        if not user:
            bot.send_message(message.chat.id,
                             "Please send /start to create your account first.")
            return

        # User has no PIN yet (pin_pending may or may not be set — we check transaction_pin)
        if not user.transaction_pin:
            pin = (message.text or '').strip()
            if not re.match(r'^\d{4}$', pin):
                bot.send_message(
                    message.chat.id,
                    "❌ That's not a valid PIN.\n"
                    "Please send exactly *4 digits* (e.g. 1234):",
                    parse_mode="Markdown"
                )
                # Make sure pin_pending is set so next message is also caught
                if not user.pin_pending:
                    user.pin_pending = True
                    db.session.commit()
                return

            # Valid 4-digit PIN
            user.set_pin(pin)
            user.pin_pending = False
            db.session.commit()
            bot.send_message(
                message.chat.id,
                "✅ *PIN set successfully!*\n\n"
                "You can now open your dashboard and start using VTMoo.",
                parse_mode="Markdown"
            )
            send_dashboard_link(message.chat.id)
            return

        # User has a PIN — any random text just gets a nudge to use the dashboard
        send_dashboard_link(message.chat.id)


# ===================== FLASK ROUTES =====================
LOADER_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VTMoo — Loading...</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body { margin:0; height:100vh; display:flex; align-items:center;
           justify-content:center; background:#ffffff; font-family:sans-serif; color:#000; }
  </style>
</head>
<body>
  <p id="msg">Loading...</p>
  <script>
    (function(){
      try {
        var tg = window.Telegram && window.Telegram.WebApp;
        if(tg){ tg.ready(); tg.expand();
          var u = tg.initDataUnsafe && tg.initDataUnsafe.user;
          if(u && u.id){ window.location.replace('{target}?telegram_id='+u.id); return; }
        }
        document.getElementById('msg').textContent =
          'Unable to load Telegram user data. Please open this from the bot inside Telegram.';
      } catch(e){ document.getElementById('msg').textContent='Error: '+e.message; }
    })();
  </script>
</body>
</html>
"""


def get_user_or_404(telegram_id_str):
    if not telegram_id_str:
        return None, LOADER_HTML.replace('{target}', request.path)
    try:
        telegram_id = int(telegram_id_str)
    except ValueError:
        return None, ("Invalid telegram_id", 400)
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return None, ("User not found", 404)
    return user, None


# ── USER-FACING PAGE ROUTES ──────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    recent_transactions = list(reversed(user.transaction_history or []))[:5]
    display_transactions = []
    for tx in recent_transactions:
        tx_copy = dict(tx)
        try:
            tx_copy['date'] = relative_time(datetime.fromisoformat(tx_copy.get('date')))
        except (TypeError, ValueError):
            pass
        display_transactions.append(tx_copy)

    unread_notifications = Notification.query.filter_by(
        telegram_id=user.telegram_id, read=False).count()

    try:
        return render_template('dashboard.html', user=user,
                               recent_transactions=display_transactions,
                               unread_notifications=unread_notifications)
    except Exception as e:
        app.logger.exception("Dashboard render error")
        return f"Template error: {e}", 500


@app.route('/notifications')
def notifications():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    notifs = Notification.query.filter_by(telegram_id=user.telegram_id)\
        .order_by(Notification.created_at.desc()).limit(50).all()

    notification_list = [{"id": n.id, "type": n.type, "title": n.title,
                           "body": n.body, "date": relative_time(n.created_at),
                           "read": n.read} for n in notifs]

    unread_notifications = sum(1 for n in notification_list if not n["read"])

    try:
        return render_template('notification.html', user=user,
                               notifications=notification_list,
                               unread_notifications=unread_notifications)
    except Exception as e:
        app.logger.exception("Notifications render error")
        return f"Template error: {e}", 500


@app.route('/profile')
def profile():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error
    try:
        return render_template('profile.html', user=user)
    except Exception as e:
        app.logger.exception("Profile render error")
        return f"Template error: {e}", 500


@app.route('/perks')
def perks():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    application = PerkApplication.query.filter_by(telegram_id=user.telegram_id)\
        .order_by(PerkApplication.created_at.desc()).first()

    # Load perks settings so the user page knows if applications are open
    settings = PerksSettings.get()

    # Count active student members to compute spots remaining
    active_members_count = User.query.filter_by(user_type='student').count()
    spots_remaining = max(0, settings.total_spots - active_members_count)

    try:
        return render_template(
            'perks.html',
            user=user,
            perk_status=application.status if application else None,
            perk_rejection_reason=application.rejection_reason if application else None,
            # Settings passed to template
            apps_open=settings.applications_open,
            allow_applications=settings.allow_applications,
            show_spots=settings.show_spots,
            spots_remaining=spots_remaining,
            total_spots=settings.total_spots,
        )
    except Exception as e:
        app.logger.exception("Perks render error")
        return f"Template error: {e}", 500


@app.route('/data')
def data_page():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    networks = Network.query.filter_by(is_active=True).order_by(
        Network.display_order.asc().nullslast(), Network.id.asc()).all()

    network_list = [{"name": n.name,
                     "image": NETWORK_IMAGES.get(n.name.upper(), '3d-mtn.jpg')}
                    for n in networks]

    plans = DataPlan.query.filter_by(is_active=True).order_by(
        DataPlan.plan_type.asc(), DataPlan.network.asc(), DataPlan.plan_name.asc()).all()

    plan_types_seen = []
    data_plans = {}

    for p in plans:
        ptype = (p.plan_type or '').strip().lower()
        if not ptype:
            continue
        if ptype not in plan_types_seen:
            plan_types_seen.append(ptype)

        price = p.student_price if (user.user_type == 'student') else p.regular_price
        label = f"{p.plan_name} - {p.duration} - ₦{price:,.0f}"

        data_plans.setdefault(ptype, {}).setdefault(p.network, []).append({
            "value": p.supplier_plan_id,
            "label": label
        })

    plan_types = [{"value": pt, "label": PLAN_TYPE_LABELS.get(pt, pt.title())}
                  for pt in plan_types_seen]

    try:
        return render_template('data.html', user=user, networks=network_list,
                               plan_types=plan_types, data_plans=data_plans)
    except Exception as e:
        app.logger.exception("Data page render error")
        return f"Template error: {e}", 500


@app.route('/fund')
def fund_page():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    recent_transactions = []
    for tx in list(reversed(user.transaction_history or []))[:5]:
        tx_copy = dict(tx)
        try:
            tx_copy['date'] = relative_time(datetime.fromisoformat(tx_copy.get('date')))
        except (TypeError, ValueError):
            pass
        recent_transactions.append(tx_copy)

    recent_notifs = Notification.query.filter_by(telegram_id=user.telegram_id)\
        .order_by(Notification.created_at.desc()).limit(5).all()

    notification_list = [{"id": n.id, "type": n.type, "title": n.title,
                           "body": n.body, "date": relative_time(n.created_at),
                           "read": n.read} for n in recent_notifs]

    try:
        return render_template('fund.html', user=user,
                               paystack_public_key=PAYSTACK_PUBLIC_KEY,
                               fee_percent=FUNDING_FEE_PERCENT,
                               recent_transactions=recent_transactions,
                               recent_notifications=notification_list)
    except Exception as e:
        app.logger.exception("Fund page render error")
        return f"Template error: {e}", 500


# ── ADMIN ROUTES ─────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_welcome'))
    error = None
    if request.method == 'POST':
        password = (request.form.get('password') or '').strip()
        if password and password == ADMIN_PASSWORD:
            session['is_admin'] = True
            return redirect(url_for('admin_welcome'))
        error = "Incorrect password. Please try again."
    return render_template('admin/login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin')
def admin_root():
    return redirect(url_for('admin_welcome'))


@app.route('/admin/welcome')
@admin_required
def admin_welcome():
    total_users                = User.query.count()
    total_balance              = db.session.query(db.func.coalesce(db.func.sum(User.balance), 0.0)).scalar()
    pending_perks              = PerkApplication.query.filter_by(status='pending').count()
    unread_notifications_total = Notification.query.filter_by(read=False).count()
    recent_users               = User.query.order_by(User.created_at.desc()).limit(6).all()
    return render_template('admin/welcome.html', active_page='welcome',
                           page_title='Welcome', page_crumb='ADMIN / WELCOME',
                           total_users=total_users, total_balance=total_balance,
                           pending_perks=pending_perks,
                           unread_notifications_total=unread_notifications_total,
                           recent_users=recent_users)


@app.route('/admin/network')
@admin_required
def admin_network():
    networks = Network.query.order_by(Network.display_order.asc().nullslast(), Network.id.asc()).all()
    return render_template('admin/network.html', active_page='network',
                           page_title='Network', page_crumb='ADMIN / NETWORK', networks=networks)


@app.route('/admin/data')
@admin_required
def admin_data():
    plans      = DataPlan.query.order_by(DataPlan.network.asc(), DataPlan.plan_name.asc()).all()
    networks   = Network.query.order_by(Network.display_order.asc().nullslast()).all()
    plan_types = [pt[0] for pt in db.session.query(DataPlan.plan_type).distinct().order_by(DataPlan.plan_type).all()]
    return render_template('admin/data.html', active_page='data',
                           page_title='Data Plans', page_crumb='ADMIN / DATA PLANS',
                           plans=plans, networks=networks, plan_types=plan_types,
                           total_plans=len(plans))


@app.route('/admin/perks')
@admin_required
def admin_perks():
    return render_template('admin/perks.html', active_page='perks',
                           page_title='Perks', page_crumb='ADMIN / PERKS')


@app.route('/admin/users-code')
@admin_required
def admin_users_code():
    return render_template('admin/users_code.html', active_page='users_code',
                           page_title='Add Users Code', page_crumb='ADMIN / USERS CODE')


# ── USER-FACING API ───────────────────────────────────────────────────
@app.route('/api/notifications/read', methods=['POST'])
def mark_notification_read():
    data            = request.get_json(silent=True) or {}
    telegram_id     = get_telegram_id_from_request(data)
    notification_id = data.get('notification_id')
    if telegram_id is None or notification_id is None:
        return jsonify(success=False, message="Missing telegram_id or notification_id"), 400
    notif = Notification.query.filter_by(id=notification_id, telegram_id=telegram_id).first()
    if not notif:
        return jsonify(success=False, message="Notification not found"), 404
    notif.read = True
    db.session.commit()
    return jsonify(success=True)


@app.route('/api/notifications/read-all', methods=['POST'])
def mark_all_notifications_read():
    data        = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400
    Notification.query.filter_by(telegram_id=telegram_id, read=False).update({"read": True})
    db.session.commit()
    return jsonify(success=True)


@app.route('/api/profile/update-pin', methods=['POST'])
def update_pin():
    data        = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    current_pin = (data.get('current_pin') or '').strip()
    new_pin     = (data.get('new_pin') or '').strip()
    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400
    if not re.match(r'^\d{4}$', current_pin) or not re.match(r'^\d{4}$', new_pin):
        return jsonify(success=False, message="PIN must be exactly 4 digits."), 400
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404
    if not user.check_pin(current_pin):
        return jsonify(success=False, message="Current PIN is incorrect."), 400
    user.set_pin(new_pin)
    db.session.commit()
    create_notification(telegram_id, "Transaction PIN updated",
                        "Your transaction PIN was changed successfully.", ntype="success")
    return jsonify(success=True, message="PIN updated successfully.")


@app.route('/api/perks/apply', methods=['POST'])
def apply_for_perks():
    data          = request.get_json(silent=True) or {}
    telegram_id   = get_telegram_id_from_request(data)
    school        = (data.get('school') or '').strip()
    matric_number = (data.get('matric_number') or '').strip()
    level         = (data.get('level') or '').strip()
    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400
    if not school or not matric_number or not level:
        return jsonify(success=False, message="Please fill in all fields."), 400

    # Check if applications are currently open/allowed
    settings = PerksSettings.get()
    if not settings.applications_open or not settings.allow_applications:
        return jsonify(success=False, message="Applications are currently closed. Please check back later."), 403

    # Check spots
    active_count = User.query.filter_by(user_type='student').count()
    if active_count >= settings.total_spots:
        return jsonify(success=False, message="All available spots have been filled. Applications are no longer being accepted at this time."), 403

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404
    existing = PerkApplication.query.filter_by(telegram_id=telegram_id)\
        .order_by(PerkApplication.created_at.desc()).first()
    if existing and existing.status in ('approved', 'pending'):
        return jsonify(success=False, message="You already have an application in progress."), 400
    db.session.add(PerkApplication(telegram_id=telegram_id, school=school,
                                   matric_number=matric_number, level=level, status='pending'))
    user.school = school
    db.session.commit()
    create_notification(telegram_id, "Perks application submitted",
                        "We're reviewing your student perks application. We'll let you know once it's processed.",
                        ntype="info")
    return jsonify(success=True, message="Application submitted! We'll review it shortly.")


@app.route('/api/data/purchase', methods=['POST'])
def purchase_data():
    data        = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    network     = (data.get('network') or '').strip().upper()
    plan_value  = (data.get('plan_value') or '').strip()
    phone       = (data.get('phone') or '').strip()
    pin         = (data.get('pin') or '').strip()
    bypass      = bool(data.get('bypass'))

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400
    if not bypass and not re.match(r'^0\d{10}$', phone):
        return jsonify(success=False, message="Invalid phone number."), 400
    if not re.match(r'^\d{4}$', pin):
        return jsonify(success=False, message="PIN must be exactly 4 digits."), 400
    if not network or not plan_value:
        return jsonify(success=False, message="Please select a network and data plan."), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404
    if not user.check_pin(pin):
        return jsonify(success=False, message="Incorrect transaction PIN."), 400

    if user.pricing_suspended:
        return jsonify(success=False,
                       message="Your account access has been suspended. Please contact support."), 403

    plan = DataPlan.query.filter_by(supplier_plan_id=plan_value,
                                    network=network, is_active=True).first()
    if not plan:
        return jsonify(success=False, message="Selected plan is no longer available."), 400

    price = plan.student_price if user.user_type == 'student' else plan.regular_price

    if (user.balance or 0) < price:
        return jsonify(success=False, message="Insufficient balance. Please fund your wallet."), 400

    supplier_result = call_data_supplier_api(plan, phone, network)
    if not supplier_result.get('ok'):
        error_type = supplier_result.get('error_type')
        app.logger.error(
            f"Data purchase failed: telegram_id={telegram_id}, plan={plan.supplier_plan_id}, "
            f"network={network}, error_type={error_type}, "
            f"msg={supplier_result.get('message')!r}"
        )
        return jsonify(success=False, message="Server error. Please contact customer support."), 502

    user.balance = round((user.balance or 0) - price, 2)
    db.session.commit()
    user.add_transaction(amount=-price, transaction_type="data_purchase",
                         description=f"{network} {plan.plan_name} ({plan.duration}) data for {phone}")
    create_notification(telegram_id, "Data purchase successful",
                        f"You purchased {plan.plan_name} ({plan.duration}) {network} data for {phone}.",
                        ntype="success")
    return jsonify(success=True, message="Data purchase successful!")


# ── PAYSTACK — WALLET FUNDING ─────────────────────────────────────────
@app.route('/api/wallet/status')
def wallet_status():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error if isinstance(error, tuple) else (error, 200)

    recent_transactions = []
    for tx in list(reversed(user.transaction_history or []))[:5]:
        tx_copy = dict(tx)
        try:
            tx_copy['date'] = relative_time(datetime.fromisoformat(tx_copy.get('date')))
        except (TypeError, ValueError):
            pass
        recent_transactions.append(tx_copy)

    recent_notifs = Notification.query.filter_by(telegram_id=user.telegram_id)\
        .order_by(Notification.created_at.desc()).limit(5).all()

    return jsonify(success=True, balance=round(user.balance, 2),
                   transactions=recent_transactions,
                   notifications=[{"id": n.id, "type": n.type, "title": n.title,
                                    "body": n.body, "date": relative_time(n.created_at),
                                    "read": n.read} for n in recent_notifs])


@app.route('/api/wallet/initialize', methods=['POST'])
def wallet_initialize():
    data        = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    amount_raw  = data.get('amount')
    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400
    if not PAYSTACK_SECRET_KEY:
        return jsonify(success=False, message="Payments are not configured yet."), 503
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return jsonify(success=False, message="Invalid amount."), 400
    if amount < 100:
        return jsonify(success=False, message="Minimum funding amount is ₦100."), 400
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404

    fee       = round(amount * FUNDING_FEE_PERCENT, 2)
    total     = round(amount + fee, 2)
    reference = f"vtmoo_{telegram_id}_{int(datetime.utcnow().timestamp())}"
    email     = f"user{telegram_id}@vtmoo.app"

    try:
        resp = requests.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                     "Content-Type": "application/json"},
            json={"email": email, "amount": int(round(total * 100)), "reference": reference,
                  "callback_url": f"{PUBLIC_URL}/payment/callback?telegram_id={telegram_id}",
                  "metadata": {"telegram_id": telegram_id, "wallet_amount": amount, "fee": fee}},
            timeout=15
        )
        payload = resp.json()
    except Exception as e:
        app.logger.exception(f"Paystack initialize failed: {e}")
        return jsonify(success=False, message="Could not reach payment gateway."), 502

    if not payload.get('status'):
        return jsonify(success=False, message=payload.get('message', 'Failed to initialize payment.')), 502

    pdata = payload.get('data', {})
    db.session.add(PaystackTransaction(telegram_id=telegram_id, reference=reference,
                                       amount=amount, fee=fee, total_charged=total, status='pending'))
    db.session.commit()

    return jsonify(success=True, authorization_url=pdata.get('authorization_url'),
                   access_code=pdata.get('access_code'), reference=reference,
                   amount=amount, fee=fee, total=total, email=email)


@app.route('/api/wallet/verify', methods=['POST'])
def wallet_verify():
    data      = request.get_json(silent=True) or {}
    reference = (data.get('reference') or '').strip()
    if not reference:
        return jsonify(success=False, message="Missing reference"), 400
    pdata = paystack_verify_transaction(reference)
    if pdata is None:
        return jsonify(success=False, message="Could not verify transaction."), 502
    success, message, user = credit_wallet_for_reference(reference, pdata)
    if not success:
        return jsonify(success=False, message=message), 400
    return jsonify(success=True, message=message, balance=round(user.balance, 2) if user else None)


@app.route('/api/wallet/check-pending', methods=['POST'])
def wallet_check_pending():
    data        = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    reference   = (data.get('reference') or '').strip()

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404

    references_to_check = []

    if reference:
        references_to_check.append(reference)

    cutoff = datetime.utcnow() - timedelta(hours=24)
    pending_txns = (
        PaystackTransaction.query
        .filter_by(telegram_id=telegram_id, status='pending')
        .filter(PaystackTransaction.created_at >= cutoff)
        .order_by(PaystackTransaction.created_at.desc())
        .all()
    )
    for txn in pending_txns:
        if txn.reference not in references_to_check:
            references_to_check.append(txn.reference)

    if not references_to_check:
        return jsonify(success=True, credited=False,
                       balance=round(user.balance, 2),
                       message="No pending transactions found")

    for ref in references_to_check:
        pdata = paystack_verify_transaction(ref)
        if pdata is None:
            continue
        ok, msg, updated_user = credit_wallet_for_reference(ref, pdata)
        if ok and updated_user:
            app.logger.info(
                f"check-pending credited ref={ref!r} for telegram_id={telegram_id}"
            )
            return jsonify(
                success=True,
                credited=True,
                balance=round(updated_user.balance, 2),
                message="Wallet funded successfully!"
            )

    return jsonify(success=True, credited=False,
                   balance=round(user.balance, 2),
                   message="No new payments confirmed yet.")


@app.route('/api/paystack/webhook', methods=['POST'])
def paystack_webhook():
    if not PAYSTACK_SECRET_KEY:
        app.logger.error("Webhook received but PAYSTACK_SECRET_KEY not set")
        return jsonify(success=False), 503

    signature = request.headers.get('x-paystack-signature', '')
    raw_body  = request.get_data()

    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        app.logger.warning(
            f"Webhook HMAC mismatch. Got: {signature[:30]}... Expected: {expected[:30]}..."
        )
        return jsonify(success=False), 401

    event      = request.get_json(silent=True) or {}
    event_type = event.get('event', '')
    reference  = (event.get('data') or {}).get('reference')

    app.logger.info(f"Paystack webhook: event={event_type!r}, reference={reference!r}")

    if event_type == 'charge.success' and reference:
        verified = paystack_verify_transaction(reference)
        if verified is not None:
            ok, msg, user = credit_wallet_for_reference(reference, verified)
            app.logger.info(
                f"Webhook credit: ok={ok}, msg={msg!r}, "
                f"user={user.telegram_id if user else None}"
            )
        else:
            app.logger.error(f"Webhook: could not verify reference={reference!r}")

    return jsonify(success=True), 200


@app.route('/payment/callback')
def payment_callback():
    telegram_id_str = request.args.get('telegram_id')
    reference       = request.args.get('reference') or request.args.get('trxref')
    user, error     = get_user_or_404(telegram_id_str)
    if error:
        return error
    if reference:
        pdata = paystack_verify_transaction(reference)
        if pdata is not None:
            credit_wallet_for_reference(reference, pdata)
    return redirect(url_for('fund_page', telegram_id=user.telegram_id, ref=reference or ''))


# ── ADMIN API — PERKS SETTINGS ───────────────────────────────────────
@app.route('/admin/api/perks/settings', methods=['GET'])
@admin_required
def admin_api_perks_settings_get():
    s = PerksSettings.get()
    # Also compute live counts
    active_members = User.query.filter_by(user_type='student').count()
    pending_apps   = PerkApplication.query.filter_by(status='pending').count()
    return jsonify(success=True, settings={
        'applications_open':  s.applications_open,
        'total_spots':        s.total_spots,
        'show_spots':         s.show_spots,
        'allow_applications': s.allow_applications,
        'active_members':     active_members,
        'spots_remaining':    max(0, s.total_spots - active_members),
        'pending_apps':       pending_apps,
    })


@app.route('/admin/api/perks/settings', methods=['POST'])
@admin_required
def admin_api_perks_settings_save():
    data = request.get_json(silent=True) or {}
    s = PerksSettings.get()
    s.applications_open  = bool(data.get('applications_open',  s.applications_open))
    s.total_spots        = int(data.get('total_spots',         s.total_spots))
    s.show_spots         = bool(data.get('show_spots',         s.show_spots))
    s.allow_applications = bool(data.get('allow_applications', s.allow_applications))
    s.updated_at         = datetime.utcnow()
    db.session.commit()
    # Return updated counts
    active_members = User.query.filter_by(user_type='student').count()
    return jsonify(success=True, message='Settings saved.', settings={
        'applications_open':  s.applications_open,
        'total_spots':        s.total_spots,
        'show_spots':         s.show_spots,
        'allow_applications': s.allow_applications,
        'active_members':     active_members,
        'spots_remaining':    max(0, s.total_spots - active_members),
    })


# ── ADMIN API: PERKS APPLICATIONS ────────────────────────────────────
@app.route('/admin/api/perks/applications', methods=['GET'])
@admin_required
def admin_api_perks_applications_list():
    apps = PerkApplication.query.order_by(PerkApplication.created_at.desc()).all()
    result = []
    for a in apps:
        user = User.query.filter_by(telegram_id=a.telegram_id).first()
        result.append({
            'id':               a.id,
            'telegram_id':      a.telegram_id,
            'user_name':        user.name     if user else 'Unknown',
            'username':         user.username if user else '',
            'school':           a.school,
            'matric_number':    a.matric_number,
            'level':            a.level,
            'status':           a.status,
            'rejection_reason': a.rejection_reason or '',
            'created_at':       a.created_at.isoformat() if a.created_at else '',
        })
    return jsonify(success=True, applications=result)


@app.route('/admin/api/perks/applications/approve', methods=['POST'])
@admin_required
def admin_api_perks_approve():
    data   = request.get_json(silent=True) or {}
    app_id = data.get('application_id')
    if not app_id:
        return jsonify(success=False, message='Missing application_id'), 400

    application = PerkApplication.query.get(app_id)
    if not application:
        return jsonify(success=False, message='Application not found'), 404

    user = User.query.filter_by(telegram_id=application.telegram_id).first()
    if not user:
        return jsonify(success=False, message='User not found'), 404

    settings     = PerksSettings.get()
    active_count = User.query.filter_by(user_type='student').count()
    if active_count >= settings.total_spots:
        return jsonify(success=False, message='No spots remaining. Increase total spots in settings.'), 400

    application.status     = 'approved'
    application.updated_at = datetime.utcnow()
    user.user_type         = 'student'
    user.pricing_suspended = False
    if application.school:
        user.school = application.school
    db.session.commit()

    create_notification(
        user.telegram_id,
        '🎉 Student Perks Approved!',
        'Congratulations! You are now a Student Perks member. Student prices have been activated on your account.',
        ntype='success'
    )
    return jsonify(success=True, message='Application approved.')


@app.route('/admin/api/perks/applications/reject', methods=['POST'])
@admin_required
def admin_api_perks_reject():
    data   = request.get_json(silent=True) or {}
    app_id = data.get('application_id')
    reason = (data.get('reason') or '').strip()
    if not app_id:
        return jsonify(success=False, message='Missing application_id'), 400

    application = PerkApplication.query.get(app_id)
    if not application:
        return jsonify(success=False, message='Application not found'), 404

    application.status           = 'rejected'
    application.rejection_reason = reason or None
    application.updated_at       = datetime.utcnow()
    db.session.commit()

    user = User.query.filter_by(telegram_id=application.telegram_id).first()
    if user:
        create_notification(
            user.telegram_id,
            'Student Perks application not approved',
            f'Your application was not approved.{" Reason: " + reason if reason else " Please contact support for more information."}',
            ntype='warning'
        )
    return jsonify(success=True, message='Application rejected.')


@app.route('/admin/api/perks/applications/bulk-approve', methods=['POST'])
@admin_required
def admin_api_perks_bulk_approve():
    data    = request.get_json(silent=True) or {}
    ids     = data.get('application_ids', [])
    if not ids:
        return jsonify(success=False, message='No application IDs provided.'), 400

    settings     = PerksSettings.get()
    active_count = User.query.filter_by(user_type='student').count()
    spots_left   = settings.total_spots - active_count
    approved     = 0
    skipped      = 0

    for app_id in ids:
        if spots_left <= 0:
            skipped += 1
            continue
        application = PerkApplication.query.get(app_id)
        if not application or application.status != 'pending':
            continue
        user = User.query.filter_by(telegram_id=application.telegram_id).first()
        if not user:
            continue
        application.status     = 'approved'
        application.updated_at = datetime.utcnow()
        user.user_type         = 'student'
        user.pricing_suspended = False
        if application.school:
            user.school = application.school
        create_notification(
            user.telegram_id,
            '🎉 Student Perks Approved!',
            'Congratulations! You are now a Student Perks member. Student prices have been activated on your account.',
            ntype='success'
        )
        approved   += 1
        spots_left -= 1

    db.session.commit()
    msg = f'{approved} approved.'
    if skipped:
        msg += f' {skipped} skipped (spots full).'
    return jsonify(success=True, approved=approved, skipped=skipped, message=msg)


@app.route('/admin/api/perks/applications/bulk-reject', methods=['POST'])
@admin_required
def admin_api_perks_bulk_reject():
    data   = request.get_json(silent=True) or {}
    ids    = data.get('application_ids', [])
    reason = (data.get('reason') or '').strip()
    if not ids:
        return jsonify(success=False, message='No application IDs provided.'), 400

    rejected = 0
    for app_id in ids:
        application = PerkApplication.query.get(app_id)
        if not application or application.status != 'pending':
            continue
        application.status           = 'rejected'
        application.rejection_reason = reason or None
        application.updated_at       = datetime.utcnow()
        user = User.query.filter_by(telegram_id=application.telegram_id).first()
        if user:
            create_notification(
                user.telegram_id,
                'Student Perks application not approved',
                f'Your application was not approved.{" Reason: " + reason if reason else " Please contact support for more information."}',
                ntype='warning'
            )
        rejected += 1

    db.session.commit()
    return jsonify(success=True, rejected=rejected, message=f'{rejected} application(s) rejected.')


# ── ADMIN API: ADD MEMBER MANUALLY ────────────────────────────────────
@app.route('/admin/api/perks/add-member', methods=['POST'])
@admin_required
def admin_api_perks_add_member():
    data       = request.get_json(silent=True) or {}
    identifier = (data.get('identifier') or '').strip().lstrip('@')
    school     = (data.get('school') or '').strip()
    department = (data.get('department') or '').strip()
    level      = (data.get('level') or '').strip()

    if not identifier:
        return jsonify(success=False, message='Please provide a username or Telegram ID.'), 400

    user = None
    try:
        tid  = int(identifier)
        user = User.query.filter_by(telegram_id=tid).first()
    except ValueError:
        user = User.query.filter_by(username=identifier).first()

    if not user:
        return jsonify(success=False, message=f'No user found for "{identifier}".'), 404

    settings     = PerksSettings.get()
    active_count = User.query.filter_by(user_type='student').count()
    if active_count >= settings.total_spots and user.user_type != 'student':
        return jsonify(success=False, message='No spots remaining. Increase total spots first.'), 400

    user.user_type         = 'student'
    user.pricing_suspended = False
    if school:
        user.school = school
    db.session.commit()

    create_notification(
        user.telegram_id,
        '🎓 Student Perks Activated',
        'An admin has granted you Student Perks. Student pricing is now active on your account.',
        ntype='success'
    )
    return jsonify(success=True, message=f'{user.name or user.username or "User"} added to Student Perks.')


# ── ADMIN API — NETWORK ───────────────────────────────────────────────
@app.route('/admin/api/network/add', methods=['POST'])
@admin_required
def admin_api_network_add():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().upper()
    if not name:
        return jsonify(success=False, message="Network name is required.")
    if Network.query.filter_by(name=name).first():
        return jsonify(success=False, message=f"Network '{name}' already exists.")
    db.session.add(Network(name=name,
                           provider_id=(data.get('provider_id') or '').strip() or None,
                           display_order=data.get('display_order') or None, is_active=True))
    db.session.commit()
    return jsonify(success=True, message=f"Network '{name}' added successfully.")


@app.route('/admin/api/network/update/<int:net_id>', methods=['POST'])
@admin_required
def admin_api_network_update(net_id):
    net  = Network.query.get_or_404(net_id)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().upper()
    if not name:
        return jsonify(success=False, message="Network name is required.")
    if Network.query.filter(Network.name == name, Network.id != net_id).first():
        return jsonify(success=False, message=f"Another network named '{name}' already exists.")
    net.name          = name
    net.provider_id   = (data.get('provider_id') or '').strip() or None
    net.display_order = data.get('display_order') or None
    net.updated_at    = datetime.utcnow()
    db.session.commit()
    return jsonify(success=True, message="Network updated.")


@app.route('/admin/api/network/toggle/<int:net_id>', methods=['POST'])
@admin_required
def admin_api_network_toggle(net_id):
    net           = Network.query.get_or_404(net_id)
    data          = request.get_json(silent=True) or {}
    net.is_active  = bool(data.get('is_active', not net.is_active))
    net.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(success=True, message=f"{net.name} {'enabled' if net.is_active else 'disabled'} successfully.")


@app.route('/admin/api/network/bulk-toggle', methods=['POST'])
@admin_required
def admin_api_network_bulk_toggle():
    data   = request.get_json(silent=True) or {}
    ids    = data.get('ids', [])
    action = data.get('action', '')
    if not ids or action not in ('enable', 'disable'):
        return jsonify(success=False, message="Invalid request.")
    Network.query.filter(Network.id.in_(ids)).update(
        {'is_active': action == 'enable'}, synchronize_session=False)
    db.session.commit()
    return jsonify(success=True, message=f"{len(ids)} network(s) {action}d.")


# ── ADMIN API — DATA PLANS ────────────────────────────────────────────
def _parse_price(raw):
    try:
        return float(str(raw).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


@app.route('/admin/api/plans/import', methods=['POST'])
@admin_required
def admin_api_plans_import():
    data = request.get_json(silent=True) or {}
    if data.get('status') != 'success':
        return jsonify(success=False, message="JSON status is not 'success'.")
    plans = data.get('plan', [])
    if not plans:
        return jsonify(success=False, message="No plans found in JSON.")
    imported = updated = errors = 0
    for p in plans:
        try:
            supplier_id = str(p.get('plan_id', '')).strip()
            if not supplier_id:
                errors += 1
                continue
            wholesale = _parse_price(p.get('amount', 0))
            network   = str(p.get('network', '')).strip().upper()
            plan_name = str(p.get('plan_name', '')).strip()
            plan_type = str(p.get('plan_type', '')).strip()
            duration  = str(p.get('plan_day', '')).strip()
            existing  = DataPlan.query.filter_by(supplier_plan_id=supplier_id).first()
            if existing:
                changed = False
                for attr, val in [('wholesale_price', wholesale), ('network', network),
                                   ('plan_name', plan_name), ('plan_type', plan_type),
                                   ('duration', duration)]:
                    if getattr(existing, attr) != val:
                        setattr(existing, attr, val)
                        changed = True
                if changed:
                    existing.updated_at = datetime.utcnow()
                    updated += 1
            else:
                db.session.add(DataPlan(supplier_plan_id=supplier_id, network=network,
                                        plan_name=plan_name, plan_type=plan_type,
                                        duration=duration, wholesale_price=wholesale,
                                        regular_price=wholesale, student_price=wholesale,
                                        is_active=True))
                imported += 1
        except Exception as e:
            app.logger.warning(f"Plan import error: {e}")
            errors += 1
    db.session.commit()
    return jsonify(success=True, imported=imported, updated=updated, errors=errors)


@app.route('/admin/api/plans/update/<int:plan_id>', methods=['POST'])
@admin_required
def admin_api_plans_update(plan_id):
    plan = DataPlan.query.get_or_404(plan_id)
    data = request.get_json(silent=True) or {}
    try:
        plan.regular_price = float(data.get('regular_price', plan.regular_price))
        plan.student_price = float(data.get('student_price', plan.student_price))
        plan.is_active     = bool(data.get('is_active', plan.is_active))
        plan.updated_at    = datetime.utcnow()
        db.session.commit()
        return jsonify(success=True, message="Plan updated.")
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/admin/api/plans/recalculate', methods=['POST'])
@admin_required
def admin_api_plans_recalculate():
    data             = request.get_json(silent=True) or {}
    regular_margin   = float(data.get('regular_margin', 0))
    student_discount = float(data.get('student_discount', 0))
    target           = data.get('target', 'all')
    plan_ids         = data.get('plan_ids')
    plans = DataPlan.query.filter(DataPlan.id.in_(plan_ids)).all() \
        if target == 'filtered' and plan_ids else DataPlan.query.all()
    for plan in plans:
        plan.regular_price = round(plan.wholesale_price + regular_margin, 2)
        plan.student_price = round(plan.regular_price - student_discount, 2)
        plan.updated_at    = datetime.utcnow()
    db.session.commit()
    return jsonify(success=True, updated=len(plans))


# ── ADMIN API — USER MANAGEMENT ──────────────────────────────────────
@app.route('/admin/api/users/list')
@admin_required
def admin_api_users_list():
    users = User.query.order_by(User.created_at.desc()).all()
    return jsonify(success=True, users=[{
        "id":                str(u.id),
        "telegram_id":       u.telegram_id,
        "name":              u.name or "",
        "username":          u.username or "",
        "phone":             u.phone or "",
        "school":            u.school or "",
        "balance":           round(u.balance or 0.0, 2),
        "user_type":         u.user_type or 'regular',
        "pricing_suspended": bool(u.pricing_suspended),
        "is_active":         u.is_active,
        "created_at":        u.created_at.isoformat() if u.created_at else "",
    } for u in users])


@app.route('/admin/api/users/set-type', methods=['POST'])
@admin_required
def admin_api_users_set_type():
    data      = request.get_json(silent=True) or {}
    user_id   = data.get('user_id')
    user_type = (data.get('user_type') or '').strip().lower()
    if not user_id:
        return jsonify(success=False, message="Missing user_id"), 400
    if user_type not in ('regular', 'student'):
        return jsonify(success=False, message="user_type must be 'regular' or 'student'."), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify(success=False, message="User not found."), 404
    old_type       = user.user_type or 'regular'
    user.user_type = user_type
    if user_type == 'student':
        user.pricing_suspended = False
    db.session.commit()
    label = 'Student' if user_type == 'student' else 'Regular'
    create_notification(user.telegram_id, f"Account type updated to {label}",
                        f"An admin updated your account type from {old_type.title()} to {label}.",
                        ntype="info")
    return jsonify(success=True, message=f"{user.name or user.username or 'User'} set to {label}.")


@app.route('/admin/api/users/suspend', methods=['POST'])
@admin_required
def admin_api_users_suspend():
    data      = request.get_json(silent=True) or {}
    user_id   = data.get('user_id')
    suspended = bool(data.get('suspended', True))
    if not user_id:
        return jsonify(success=False, message="Missing user_id"), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify(success=False, message="User not found."), 404
    user.pricing_suspended = suspended
    db.session.commit()
    action = "suspended" if suspended else "reinstated"
    create_notification(user.telegram_id, f"Account {action}",
                        "Your pricing access has been suspended. Please contact support."
                        if suspended else
                        "Your account has been reinstated. You can now make purchases.",
                        ntype="warning" if suspended else "success")
    return jsonify(success=True, message=f"{user.name or user.username or 'User'} {action}.")


@app.route('/admin/api/users/revoke', methods=['POST'])
@admin_required
def admin_api_users_revoke():
    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify(success=False, message="Missing user_id"), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify(success=False, message="User not found."), 404
    user.user_type         = 'regular'
    user.school            = None
    user.pricing_suspended = False
    db.session.commit()
    create_notification(user.telegram_id, "Student Perks removed",
                        "Your student perks and school affiliation have been removed. You now have regular pricing.",
                        ntype="warning")
    return jsonify(success=True, message=f"Perks revoked for {user.name or user.username or 'User'}.")


# ===================== FLASK SETUP =====================
def run_migrations():
    migrations = [
        "ALTER TABLE vtmoo_users ADD COLUMN IF NOT EXISTS school VARCHAR(150)",
        "ALTER TABLE vtmoo_users ADD COLUMN IF NOT EXISTS user_type VARCHAR(20) DEFAULT 'regular'",
        "ALTER TABLE vtmoo_users ADD COLUMN IF NOT EXISTS pricing_suspended BOOLEAN DEFAULT FALSE",
        "ALTER TABLE vtmoo_users ADD COLUMN IF NOT EXISTS pin_pending BOOLEAN DEFAULT FALSE",
        "CREATE TABLE IF NOT EXISTS vtmoo_perks_settings "
        "(id SERIAL PRIMARY KEY, applications_open BOOLEAN DEFAULT TRUE, "
        "total_spots INTEGER DEFAULT 40, show_spots BOOLEAN DEFAULT TRUE, "
        "allow_applications BOOLEAN DEFAULT TRUE, updated_at TIMESTAMP DEFAULT NOW())",
    ]
    with db.engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                app.logger.warning(f"Migration skipped/failed: {stmt} -> {e}")


with app.app_context():
    db.create_all()
    run_migrations()
    print("✅ vtmoo Database tables created successfully!")


def run_bot():
    print("🤖 Telegram Bot is Running...")
    bot.infinity_polling()


bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()


if __name__ == '__main__':
    print("🚀 Flask + Telegram Bot Started!")
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
