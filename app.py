# app.py
import os
import re
import threading
from functools import wraps
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
