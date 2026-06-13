# app.py
import os
import re
import threading
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import telebot
from telebot import types

app = Flask(__name__)
app.secret_key = os.urandom(24)

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
