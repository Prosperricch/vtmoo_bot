# app.py
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
from datetime import datetime
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
BOT_TOKEN = os.environ.get('BOT_TOKEN', "8828586999:AAH2o_6ch_Il3vw563UuOn3zrT2uA3IMplY")
PUBLIC_URL = os.environ.get('PUBLIC_URL', 'https://your-app-name.onrender.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'changeme')

# ===================== PAYSTACK CONFIG =====================
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')
PAYSTACK_PUBLIC_KEY = os.environ.get('PAYSTACK_PUBLIC_KEY', '')
PAYSTACK_BASE_URL = 'https://api.paystack.co'
FUNDING_FEE_PERCENT = float(os.environ.get('FUNDING_FEE_PERCENT', '0.05'))  # 5% default

bot = telebot.TeleBot(BOT_TOKEN)


# ===================== USER MODEL =====================
class User(db.Model):
    __tablename__ = 'vtmoo_users'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False, index=True)

    username = db.Column(db.String(80), unique=True, nullable=True)
    name = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(20), unique=True, nullable=True, index=True)
    school = db.Column(db.String(150), nullable=True)

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


# ===================== NOTIFICATION MODEL =====================
class Notification(db.Model):
    __tablename__ = 'vtmoo_notifications'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, nullable=False, index=True)

    type = db.Column(db.String(20), default='info')  # info, success, warning
    title = db.Column(db.String(150), nullable=False)
    body = db.Column(db.Text, nullable=True)
    read = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def create_notification(telegram_id, title, body="", ntype="info"):
    notif = Notification(
        telegram_id=telegram_id,
        title=title,
        body=body,
        type=ntype
    )
    db.session.add(notif)
    db.session.commit()
    return notif


# ===================== PERK APPLICATION MODEL =====================
class PerkApplication(db.Model):
    __tablename__ = 'vtmoo_perk_applications'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, nullable=False, index=True)

    school = db.Column(db.String(150), nullable=False)
    matric_number = db.Column(db.String(50), nullable=False)
    level = db.Column(db.String(20), nullable=False)

    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    rejection_reason = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== NETWORK MODEL =====================
class Network(db.Model):
    __tablename__ = 'vtmoo_networks'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    provider_id = db.Column(db.String(80), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    display_order = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== DATA PLAN MODEL =====================
class DataPlan(db.Model):
    __tablename__ = 'vtmoo_data_plans'

    id = db.Column(db.Integer, primary_key=True)
    supplier_plan_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    network = db.Column(db.String(20), nullable=False, index=True)
    plan_name = db.Column(db.String(30), nullable=False)
    plan_type = db.Column(db.String(50), nullable=False)
    duration = db.Column(db.String(50), nullable=False)

    wholesale_price = db.Column(db.Float, default=0.0)
    regular_price = db.Column(db.Float, default=0.0)
    student_price = db.Column(db.Float, default=0.0)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===================== PAYSTACK TRANSACTION MODEL =====================
class PaystackTransaction(db.Model):
    __tablename__ = 'vtmoo_paystack_transactions'

    id = db.Column(db.Integer, primary_key=True)
    telegram_id = db.Column(db.BigInteger, nullable=False, index=True)

    reference = db.Column(db.String(100), unique=True, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)       # amount credited to wallet (excludes fee)
    fee = db.Column(db.Float, default=0.0)             # processing fee charged on top
    total_charged = db.Column(db.Float, nullable=False)  # amount + fee, what Paystack actually charged

    status = db.Column(db.String(20), default='pending')  # pending, success, failed
    channel = db.Column(db.String(30), nullable=True)     # card, bank_transfer, ussd, etc.

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    markup.add(types.InlineKeyboardButton(
        "📊 Open Dashboard",
        web_app=types.WebAppInfo(url=dashboard_url)
    ))
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


def get_telegram_id_from_request(data):
    """Safely extract and validate telegram_id from a JSON request body."""
    telegram_id = data.get('telegram_id')
    if telegram_id is None:
        return None
    try:
        return int(telegram_id)
    except (TypeError, ValueError):
        return None


def relative_time(dt):
    """Return a human-friendly relative time string for a datetime."""
    if not dt:
        return ""
    now = datetime.utcnow()
    diff = now - dt
    seconds = diff.total_seconds()

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
    """Require an authenticated admin session for the wrapped view."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('admin_login'))
        return view_func(*args, **kwargs)
    return wrapped


def paystack_verify_transaction(reference):
    """
    Call Paystack's Verify Transaction endpoint.
    Returns the 'data' dict from Paystack on success, or None on failure.
    """
    if not PAYSTACK_SECRET_KEY:
        app.logger.error("PAYSTACK_SECRET_KEY is not configured")
        return None

    try:
        resp = requests.get(
            f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            timeout=15
        )
        payload = resp.json()
    except Exception as e:
        app.logger.exception(f"Paystack verify request failed: {e}")
        return None

    if not payload.get('status'):
        app.logger.warning(f"Paystack verify returned status=false: {payload}")
        return None

    return payload.get('data')


def credit_wallet_for_reference(reference, paystack_data):
    """
    Idempotently credit a user's wallet based on a verified Paystack transaction.

    `paystack_data` is the 'data' object returned by Paystack's verify endpoint.
    Returns (success: bool, message: str, user: User|None).
    """
    txn = PaystackTransaction.query.filter_by(reference=reference).first()
    if not txn:
        app.logger.warning(f"No PaystackTransaction record found for reference={reference}")
        return False, "Transaction record not found.", None

    # Already processed — don't credit twice
    if txn.status == 'success':
        user = User.query.filter_by(telegram_id=txn.telegram_id).first()
        return True, "Already credited.", user

    gateway_status = (paystack_data.get('status') or '').lower()
    if gateway_status != 'success':
        txn.status = 'failed'
        txn.updated_at = datetime.utcnow()
        db.session.commit()
        return False, f"Payment not successful (status: {gateway_status}).", None

    # Sanity-check the amount Paystack charged matches what we expected
    amount_paid_kobo = paystack_data.get('amount', 0)
    expected_total_kobo = round(txn.total_charged * 100)
    if amount_paid_kobo != expected_total_kobo:
        app.logger.warning(
            f"Amount mismatch for reference={reference}: "
            f"expected {expected_total_kobo}, got {amount_paid_kobo}"
        )
        # Still proceed using the amount we originally recorded (txn.amount),
        # since that's what we quoted the user.

    user = User.query.filter_by(telegram_id=txn.telegram_id).first()
    if not user:
        txn.status = 'failed'
        db.session.commit()
        return False, "User not found.", None

    # Credit the wallet
    user.balance = round((user.balance or 0.0) + txn.amount, 2)

    txn.status = 'success'
    txn.channel = (paystack_data.get('channel') or '')[:30]
    txn.updated_at = datetime.utcnow()

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


# Maps network names (as stored in vtmoo_networks.name) to the static image
# files under /static. Used to render the data page network carousel.
NETWORK_IMAGES = {
    'MTN': '3d-mtn.jpg',
    'AIRTEL': '3d-airtel.jpg',
    'GLO': '3d-glo.jpg',
    '9MOBILE': '3d-9mobile.jpg',
}

# Human-friendly labels for plan_type tabs on the data page.
PLAN_TYPE_LABELS = {
    'sme': 'SME',
    'gifting': 'Gifting',
    'corporate': 'Corporate',
    'awoof': 'Awoof',
}


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
LOADER_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VTMoo — Loading...</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body {
      margin: 0;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #ffffff;
      font-family: sans-serif;
      color: #000000;
    }
  </style>
</head>
<body>
  <p id="msg">Loading dashboard...</p>
  <script>
    (function () {
      try {
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg) {
          tg.ready();
          tg.expand();
          const tgUser = tg.initDataUnsafe && tg.initDataUnsafe.user;
          if (tgUser && tgUser.id) {
            window.location.replace('{target}?telegram_id=' + tgUser.id);
            return;
          }
        }
        document.getElementById('msg').textContent =
          'Unable to load Telegram user data. Please open this from the bot inside Telegram.';
      } catch (e) {
        document.getElementById('msg').textContent = 'Error: ' + e.message;
      }
    })();
  </script>
</body>
</html>
"""


def get_user_or_404(telegram_id_str):
    """Validate telegram_id query param and fetch the user, or return an error response."""
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


@app.route('/dashboard')
def dashboard():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    # Most recent transactions first, limit to last 5
    recent_transactions = list(reversed(user.transaction_history or []))[:5]

    # Build display-friendly dates for activity rows
    display_transactions = []
    for tx in recent_transactions:
        tx_copy = dict(tx)
        try:
            tx_date = datetime.fromisoformat(tx_copy.get('date'))
            tx_copy['date'] = relative_time(tx_date)
        except (TypeError, ValueError):
            pass
        display_transactions.append(tx_copy)

    unread_notifications = Notification.query.filter_by(
        telegram_id=user.telegram_id, read=False
    ).count()

    try:
        return render_template(
            'dashboard.html',
            user=user,
            recent_transactions=display_transactions,
            unread_notifications=unread_notifications
        )
    except Exception as e:
        app.logger.exception("Dashboard render error")
        return f"Template error: {e}", 500


@app.route('/notifications')
def notifications():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    notifs = Notification.query.filter_by(
        telegram_id=user.telegram_id
    ).order_by(Notification.created_at.desc()).limit(50).all()

    notification_list = [{
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "date": relative_time(n.created_at),
        "read": n.read
    } for n in notifs]

    unread_notifications = sum(1 for n in notification_list if not n["read"])

    try:
        return render_template(
            'notification.html',
            user=user,
            notifications=notification_list,
            unread_notifications=unread_notifications
        )
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

    application = PerkApplication.query.filter_by(
        telegram_id=user.telegram_id
    ).order_by(PerkApplication.created_at.desc()).first()

    perk_status = application.status if application else None
    perk_rejection_reason = application.rejection_reason if application else None

    try:
        return render_template(
            'perks.html',
            user=user,
            perk_status=perk_status,
            perk_rejection_reason=perk_rejection_reason
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

    # Only show active networks, in their configured display order
    networks = Network.query.filter_by(is_active=True).order_by(
        Network.display_order.asc().nullslast(), Network.id.asc()
    ).all()

    network_list = [{
        "name": n.name,
        "image": NETWORK_IMAGES.get(n.name.upper(), '3d-mtn.jpg')
    } for n in networks]

    # Only show active plans
    plans = DataPlan.query.filter_by(is_active=True).order_by(
        DataPlan.plan_type.asc(), DataPlan.network.asc(), DataPlan.plan_name.asc()
    ).all()

    plan_types_seen = []
    data_plans = {}

    for p in plans:
        ptype = (p.plan_type or '').strip().lower()
        if not ptype:
            continue

        if ptype not in plan_types_seen:
            plan_types_seen.append(ptype)

        data_plans.setdefault(ptype, {}).setdefault(p.network, [])

        # Students (users who have applied for/have a school on file) get
        # student pricing, everyone else gets regular pricing.
        price = p.student_price if user.school else p.regular_price

        label = f"{p.plan_name} - {p.duration} - ₦{price:,.0f}"

        data_plans[ptype][p.network].append({
            "value": p.supplier_plan_id,
            "label": label
        })

    plan_types = [
        {"value": pt, "label": PLAN_TYPE_LABELS.get(pt, pt.title())}
        for pt in plan_types_seen
    ]

    try:
        return render_template(
            'data.html',
            user=user,
            networks=network_list,
            plan_types=plan_types,
            data_plans=data_plans
        )
    except Exception as e:
        app.logger.exception("Data page render error")
        return f"Template error: {e}", 500


@app.route('/fund')
def fund_page():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    # Most recent funding-related transactions first, limit to last 5
    all_transactions = list(reversed(user.transaction_history or []))
    recent_transactions = all_transactions[:5]

    display_transactions = []
    for tx in recent_transactions:
        tx_copy = dict(tx)
        try:
            tx_date = datetime.fromisoformat(tx_copy.get('date'))
            tx_copy['date'] = relative_time(tx_date)
        except (TypeError, ValueError):
            pass
        display_transactions.append(tx_copy)

    recent_notifs = Notification.query.filter_by(
        telegram_id=user.telegram_id
    ).order_by(Notification.created_at.desc()).limit(5).all()

    notification_list = [{
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "date": relative_time(n.created_at),
        "read": n.read
    } for n in recent_notifs]

    try:
        return render_template(
            'fund.html',
            user=user,
            paystack_public_key=PAYSTACK_PUBLIC_KEY,
            fee_percent=FUNDING_FEE_PERCENT,
            recent_transactions=display_transactions,
            recent_notifications=notification_list
        )
    except Exception as e:
        app.logger.exception("Fund page render error")
        return f"Template error: {e}", 500


# ===================== ADMIN ROUTES =====================
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
    total_users = User.query.count()
    total_balance = db.session.query(db.func.coalesce(db.func.sum(User.balance), 0.0)).scalar()
    pending_perks = PerkApplication.query.filter_by(status='pending').count()
    unread_notifications_total = Notification.query.filter_by(read=False).count()
    recent_users = User.query.order_by(User.created_at.desc()).limit(6).all()

    return render_template(
        'admin/welcome.html',
        active_page='welcome',
        page_title='Welcome',
        page_crumb='ADMIN / WELCOME',
        total_users=total_users,
        total_balance=total_balance,
        pending_perks=pending_perks,
        unread_notifications_total=unread_notifications_total,
        recent_users=recent_users
    )


@app.route('/admin/network')
@admin_required
def admin_network():
    networks = Network.query.order_by(Network.display_order.asc().nullslast(), Network.id.asc()).all()
    return render_template(
        'admin/network.html',
        active_page='network',
        page_title='Network',
        page_crumb='ADMIN / NETWORK',
        networks=networks
    )


@app.route('/admin/data')
@admin_required
def admin_data():
    plans = DataPlan.query.order_by(DataPlan.network.asc(), DataPlan.plan_name.asc()).all()
    networks = Network.query.order_by(Network.display_order.asc().nullslast()).all()
    plan_types = db.session.query(DataPlan.plan_type).distinct().order_by(DataPlan.plan_type).all()
    plan_types = [pt[0] for pt in plan_types]
    return render_template(
        'admin/data.html',
        active_page='data',
        page_title='Data Plans',
        page_crumb='ADMIN / DATA PLANS',
        plans=plans,
        networks=networks,
        plan_types=plan_types,
        total_plans=len(plans)
    )


@app.route('/admin/perks')
@admin_required
def admin_perks():
    return render_template(
        'admin/perks.html',
        active_page='perks',
        page_title='Perks',
        page_crumb='ADMIN / PERKS'
    )


@app.route('/admin/users-code')
@admin_required
def admin_users_code():
    return render_template(
        'admin/users_code.html',
        active_page='users_code',
        page_title='Add Users Code',
        page_crumb='ADMIN / USERS CODE'
    )


# ===================== API ROUTES =====================
@app.route('/api/notifications/read', methods=['POST'])
def mark_notification_read():
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
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
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400

    Notification.query.filter_by(telegram_id=telegram_id, read=False).update({"read": True})
    db.session.commit()
    return jsonify(success=True)


@app.route('/api/profile/update-pin', methods=['POST'])
def update_pin():
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    current_pin = (data.get('current_pin') or '').strip()
    new_pin = (data.get('new_pin') or '').strip()

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

    create_notification(
        telegram_id,
        "Transaction PIN updated",
        "Your transaction PIN was changed successfully.",
        ntype="success"
    )

    return jsonify(success=True, message="PIN updated successfully.")


@app.route('/api/perks/apply', methods=['POST'])
def apply_for_perks():
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    school = (data.get('school') or '').strip()
    matric_number = (data.get('matric_number') or '').strip()
    level = (data.get('level') or '').strip()

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400

    if not school or not matric_number or not level:
        return jsonify(success=False, message="Please fill in all fields."), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404

    existing = PerkApplication.query.filter_by(
        telegram_id=telegram_id
    ).order_by(PerkApplication.created_at.desc()).first()

    if existing and existing.status in ('approved', 'pending'):
        return jsonify(success=False, message="You already have an application in progress."), 400

    application = PerkApplication(
        telegram_id=telegram_id,
        school=school,
        matric_number=matric_number,
        level=level,
        status='pending'
    )
    db.session.add(application)

    user.school = school
    db.session.commit()

    create_notification(
        telegram_id,
        "Perks application submitted",
        "We're reviewing your student perks application. We'll let you know once it's processed.",
        ntype="info"
    )

    return jsonify(success=True, message="Application submitted! We'll review it shortly.")


@app.route('/api/data/purchase', methods=['POST'])
def purchase_data():
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    network = (data.get('network') or '').strip().upper()
    plan_value = (data.get('plan_value') or '').strip()
    phone = (data.get('phone') or '').strip()
    pin = (data.get('pin') or '').strip()
    bypass = bool(data.get('bypass'))

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400

    if not bypass and not re.match(r'^0\d{10}$', phone):
        return jsonify(success=False, message="Invalid phone number."), 400

    if not re.match(r'^\d{4}$', pin):
        return jsonify(success=False, message="PIN must be exactly 4 digits."), 400

    if not network or not plan_value:
        return jsonify(success=False, message="Please select a network and a data plan."), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404

    if not user.check_pin(pin):
        return jsonify(success=False, message="Incorrect transaction PIN."), 400

    plan = DataPlan.query.filter_by(
        supplier_plan_id=plan_value, network=network, is_active=True
    ).first()
    if not plan:
        return jsonify(success=False, message="Selected plan is no longer available."), 400

    price = plan.student_price if user.school else plan.regular_price

    if user.balance < price:
        return jsonify(success=False, message="Insufficient balance. Please fund your wallet."), 400

    # TODO: integrate with the actual data top-up provider here using
    # plan.supplier_plan_id and the recipient phone number. If the provider
    # call fails, do not deduct the user's balance / return an error instead.

    user.balance = round(user.balance - price, 2)
    db.session.commit()

    user.add_transaction(
        amount=-price,
        transaction_type="data_purchase",
        description=f"{network} {plan.plan_name} ({plan.duration}) data for {phone}"
    )

    create_notification(
        telegram_id,
        "Data purchase successful",
        f"You purchased {plan.plan_name} ({plan.duration}) {network} data for {phone}.",
        ntype="success"
    )

    return jsonify(success=True, message="Data purchase successful!")


# ===================== PAYSTACK — WALLET FUNDING =====================
@app.route('/api/wallet/status')
def wallet_status():
    telegram_id_str = request.args.get('telegram_id')
    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error if isinstance(error, tuple) else (error, 200)

    recent_transactions = list(reversed(user.transaction_history or []))[:5]
    display_transactions = []
    for tx in recent_transactions:
        tx_copy = dict(tx)
        try:
            tx_date = datetime.fromisoformat(tx_copy.get('date'))
            tx_copy['date'] = relative_time(tx_date)
        except (TypeError, ValueError):
            pass
        display_transactions.append(tx_copy)

    recent_notifs = Notification.query.filter_by(
        telegram_id=user.telegram_id
    ).order_by(Notification.created_at.desc()).limit(5).all()

    notification_list = [{
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body,
        "date": relative_time(n.created_at),
        "read": n.read
    } for n in recent_notifs]

    return jsonify(
        success=True,
        balance=round(user.balance, 2),
        transactions=display_transactions,
        notifications=notification_list
    )


@app.route('/api/wallet/initialize', methods=['POST'])
def wallet_initialize():
    data = request.get_json(silent=True) or {}
    telegram_id = get_telegram_id_from_request(data)
    amount_raw = data.get('amount')

    if telegram_id is None:
        return jsonify(success=False, message="Missing telegram_id"), 400

    if not PAYSTACK_SECRET_KEY:
        return jsonify(success=False, message="Payments are not configured yet. Please try again later."), 503

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return jsonify(success=False, message="Invalid amount."), 400

    if amount < 100:
        return jsonify(success=False, message="Minimum funding amount is ₦100."), 400

    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify(success=False, message="User not found"), 404

    fee = round(amount * FUNDING_FEE_PERCENT, 2)
    total = round(amount + fee, 2)

    # Build a unique reference for this attempt
    reference = f"vtmoo_{telegram_id}_{int(datetime.utcnow().timestamp())}"

    # Paystack requires an email; fall back to a synthetic one tied to telegram_id
    email = f"user{telegram_id}@vtmoo.app"

    callback_url = f"{PUBLIC_URL}/payment/callback?telegram_id={telegram_id}"

    try:
        resp = requests.post(
            f"{PAYSTACK_BASE_URL}/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "email": email,
                "amount": int(round(total * 100)),  # kobo
                "reference": reference,
                "callback_url": callback_url,
                "metadata": {
                    "telegram_id": telegram_id,
                    "wallet_amount": amount,
                    "fee": fee
                }
            },
            timeout=15
        )
        payload = resp.json()
    except Exception as e:
        app.logger.exception(f"Paystack initialize request failed: {e}")
        return jsonify(success=False, message="Could not reach payment gateway. Please try again."), 502

    if not payload.get('status'):
        message = payload.get('message', 'Failed to initialize payment.')
        return jsonify(success=False, message=message), 502

    pdata = payload.get('data', {})

    # Record the pending transaction so we can verify/credit it later
    txn = PaystackTransaction(
        telegram_id=telegram_id,
        reference=reference,
        amount=amount,
        fee=fee,
        total_charged=total,
        status='pending'
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify(
        success=True,
        authorization_url=pdata.get('authorization_url'),
        access_code=pdata.get('access_code'),
        reference=reference,
        amount=amount,
        fee=fee,
        total=total,
        email=email
    )


@app.route('/api/wallet/verify', methods=['POST'])
def wallet_verify():
    data = request.get_json(silent=True) or {}
    reference = (data.get('reference') or '').strip()

    if not reference:
        return jsonify(success=False, message="Missing reference"), 400

    pdata = paystack_verify_transaction(reference)
    if pdata is None:
        return jsonify(success=False, message="Could not verify transaction with payment gateway."), 502

    success, message, user = credit_wallet_for_reference(reference, pdata)

    if not success:
        return jsonify(success=False, message=message), 400

    return jsonify(
        success=True,
        message=message,
        balance=round(user.balance, 2) if user else None
    )


@app.route('/api/paystack/webhook', methods=['POST'])
def paystack_webhook():
    if not PAYSTACK_SECRET_KEY:
        return jsonify(success=False), 503

    signature = request.headers.get('x-paystack-signature', '')
    raw_body = request.get_data()

    expected_signature = hmac.new(
        PAYSTACK_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        app.logger.warning("Paystack webhook signature mismatch")
        return jsonify(success=False), 401

    event = request.get_json(silent=True) or {}
    event_type = event.get('event')
    pdata = event.get('data', {})
    reference = pdata.get('reference')

    if event_type == 'charge.success' and reference:
        # Re-verify directly with Paystack rather than trusting the webhook body alone
        verified = paystack_verify_transaction(reference)
        if verified is not None:
            credit_wallet_for_reference(reference, verified)

    # Always 200 so Paystack doesn't keep retrying
    return jsonify(success=True), 200


@app.route('/payment/callback')
def payment_callback():
    telegram_id_str = request.args.get('telegram_id')
    reference = request.args.get('reference') or request.args.get('trxref')

    user, error = get_user_or_404(telegram_id_str)
    if error:
        return error

    if reference:
        pdata = paystack_verify_transaction(reference)
        if pdata is not None:
            credit_wallet_for_reference(reference, pdata)

    # Redirect back to the fund page; the frontend will show the latest balance/toast
    return redirect(url_for('fund_page', telegram_id=user.telegram_id, ref=reference or ''))


# ===================== ADMIN API — NETWORK =====================
@app.route('/admin/api/network/add', methods=['POST'])
@admin_required
def admin_api_network_add():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().upper()
    if not name:
        return jsonify(success=False, message="Network name is required.")
    if Network.query.filter_by(name=name).first():
        return jsonify(success=False, message=f"Network '{name}' already exists.")
    net = Network(
        name=name,
        provider_id=(data.get('provider_id') or '').strip() or None,
        display_order=data.get('display_order') or None,
        is_active=True
    )
    db.session.add(net)
    db.session.commit()
    return jsonify(success=True, message=f"Network '{name}' added successfully.")


@app.route('/admin/api/network/update/<int:net_id>', methods=['POST'])
@admin_required
def admin_api_network_update(net_id):
    net = Network.query.get_or_404(net_id)
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().upper()
    if not name:
        return jsonify(success=False, message="Network name is required.")
    # Check name conflict (excluding self)
    existing = Network.query.filter(Network.name == name, Network.id != net_id).first()
    if existing:
        return jsonify(success=False, message=f"Another network named '{name}' already exists.")
    net.name = name
    net.provider_id = (data.get('provider_id') or '').strip() or None
    net.display_order = data.get('display_order') or None
    net.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(success=True, message=f"Network updated.")


@app.route('/admin/api/network/toggle/<int:net_id>', methods=['POST'])
@admin_required
def admin_api_network_toggle(net_id):
    net = Network.query.get_or_404(net_id)
    data = request.get_json(silent=True) or {}
    net.is_active = bool(data.get('is_active', not net.is_active))
    net.updated_at = datetime.utcnow()
    db.session.commit()
    state = "enabled" if net.is_active else "disabled"
    return jsonify(success=True, message=f"{net.name} {state} successfully.")


@app.route('/admin/api/network/bulk-toggle', methods=['POST'])
@admin_required
def admin_api_network_bulk_toggle():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    action = data.get('action', '')  # 'enable' or 'disable'
    if not ids or action not in ('enable', 'disable'):
        return jsonify(success=False, message="Invalid request.")
    is_active = (action == 'enable')
    Network.query.filter(Network.id.in_(ids)).update({'is_active': is_active}, synchronize_session=False)
    db.session.commit()
    return jsonify(success=True, message=f"{len(ids)} network(s) {action}d.")


# ===================== ADMIN API — DATA PLANS =====================
def _parse_price(raw):
    """Convert supplier price strings like '1,350.00' to float."""
    try:
        return float(str(raw).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


@app.route('/admin/api/plans/import', methods=['POST'])
@admin_required
def admin_api_plans_import():
    data = request.get_json(silent=True) or {}
    if data.get('status') != 'success':
        return jsonify(success=False, message="JSON status is not 'success'. Check the JSON.")
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
            network = str(p.get('network', '')).strip().upper()
            plan_name = str(p.get('plan_name', '')).strip()
            plan_type = str(p.get('plan_type', '')).strip()
            duration = str(p.get('plan_day', '')).strip()

            existing = DataPlan.query.filter_by(supplier_plan_id=supplier_id).first()
            if existing:
                # Only update supplier fields — do NOT touch regular_price/student_price/is_active
                changed = False
                if existing.wholesale_price != wholesale:
                    existing.wholesale_price = wholesale
                    changed = True
                if existing.network != network:
                    existing.network = network
                    changed = True
                if existing.plan_name != plan_name:
                    existing.plan_name = plan_name
                    changed = True
                if existing.plan_type != plan_type:
                    existing.plan_type = plan_type
                    changed = True
                if existing.duration != duration:
                    existing.duration = duration
                    changed = True
                if changed:
                    existing.updated_at = datetime.utcnow()
                    updated += 1
            else:
                # New plan — set regular & student = wholesale (0 margin until admin sets it)
                new_plan = DataPlan(
                    supplier_plan_id=supplier_id,
                    network=network,
                    plan_name=plan_name,
                    plan_type=plan_type,
                    duration=duration,
                    wholesale_price=wholesale,
                    regular_price=wholesale,
                    student_price=wholesale,
                    is_active=True
                )
                db.session.add(new_plan)
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
        plan.is_active = bool(data.get('is_active', plan.is_active))
        plan.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify(success=True, message="Plan updated.")
    except Exception as e:
        return jsonify(success=False, message=str(e))


@app.route('/admin/api/plans/recalculate', methods=['POST'])
@admin_required
def admin_api_plans_recalculate():
    data = request.get_json(silent=True) or {}
    regular_margin = float(data.get('regular_margin', 0))
    student_discount = float(data.get('student_discount', 0))
    target = data.get('target', 'all')
    plan_ids = data.get('plan_ids')  # list of ints, or None

    if target == 'filtered' and plan_ids:
        plans = DataPlan.query.filter(DataPlan.id.in_(plan_ids)).all()
    else:
        plans = DataPlan.query.all()

    updated = 0
    for plan in plans:
        plan.regular_price = round(plan.wholesale_price + regular_margin, 2)
        plan.student_price = round(plan.regular_price - student_discount, 2)
        plan.updated_at = datetime.utcnow()
        updated += 1

    db.session.commit()
    return jsonify(success=True, updated=updated)


# ===================== FLASK SETUP =====================
def run_migrations():
    """Add any columns that may be missing from tables created before this update."""
    migrations = [
        "ALTER TABLE vtmoo_users ADD COLUMN IF NOT EXISTS school VARCHAR(150)",
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


# Start the bot polling thread (works for both local run and gunicorn on Render)
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()


if __name__ == '__main__':
    print("🚀 Flask + Telegram Bot Started!")
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
