import os, random, math, time, telebot, threading, json, pathlib, hmac, hashlib
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, ReplyKeyboardMarkup, KeyboardButton, ChatPermissions, WebAppInfo

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN: raise ValueError("Не задан BOT_TOKEN")

ADMIN_IDS = [8158495271]
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
API_NOTE = "Адрес задаётся переменной WEBAPP_URL в Railway"
DATA_DIR = pathlib.Path("/app/data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = DATA_DIR / "database.json"

data = {"players": {}, "promos": {}, "activations": [], "history": [], "clans": {}, "next_id": 1, "active_games": {}, "roulette_bets": {}, "duels": {}}
db_lock = threading.Lock()

def load_data():
    global data
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                for key in loaded: data[key] = loaded[key]
        except Exception as e: print(f"⚠️ Ошибка загрузки: {e}")

def save_data():
    with db_lock:
        with open(DATA_FILE, "w", encoding="utf-8") as f: 
            json.dump(data, f, ensure_ascii=False, indent=4)

load_data()

def migrate_data():
    global data
    changed = False
    current_max = max([p.get("short_id", 0) for p in data["players"].values() if isinstance(p.get("short_id"), int)], default=0)
    if data.get("next_id", 1) <= current_max: data["next_id"] = current_max + 1
    for uid, pdata in data["players"].items():
        if "short_id" not in pdata or pdata["short_id"] == 0:
            pdata["short_id"] = data["next_id"]; data["next_id"] += 1; changed = True
        if "bank" not in pdata: pdata["bank"] = 0; pdata["deposit"] = 0; pdata["deposit_start"] = 0; changed = True
        if "biz_territory" not in pdata: pdata["biz_territory"] = 0; pdata["biz_building"] = 1; pdata["biz_tax"] = 0; pdata["biz_last_collect"] = int(time.time()); changed = True
        if "title" not in pdata: pdata["title"] = "Нет"; changed = True
        if "quests" not in pdata: pdata["quests"] = {}; changed = True
        if "donate_status" not in pdata: pdata["donate_status"] = "Нет"; changed = True
        if "last_bonus" not in pdata: pdata["last_bonus"] = 0; changed = True
    if "roulette_bets" not in data: data["roulette_bets"] = {}
    if "duels" not in data: data["duels"] = {}
    if "promos" not in data: data["promos"] = {}
    if "activations" not in data: data["activations"] = []
    if changed: save_data()

migrate_data()
bot = telebot.TeleBot(TOKEN)

# ==================== АНТИСПАМ ====================
from collections import deque

ANTISPAM_LIMIT = 15      # больше 15 сообщений...
ANTISPAM_WINDOW = 30     # ...за 30 секунд — мут
ANTISPAM_MUTE = 600      # на 10 минут

spam_events = {}   # (chat_id, uid) -> deque таймстемпов
muted_until = {}   # (chat_id, uid) -> до какого времени мут

def _as_is_muted(key):
    return muted_until.get(key, 0) > time.time()

def _as_cleanup():
    now = time.time()
    if len(muted_until) > 500:
        for k in list(muted_until.keys()):
            if muted_until[k] < now: del muted_until[k]
    if len(spam_events) > 2000:
        for k in list(spam_events.keys()):
            if not spam_events[k]: del spam_events[k]

def _as_do_mute(chat_id, uid, display_name):
    muted_until[(chat_id, uid)] = time.time() + ANTISPAM_MUTE
    muted_until[(0, uid)] = time.time() + ANTISPAM_MUTE  # мут и в боте
    if chat_id < 0:
        try:
            bot.restrict_chat_member(chat_id, int(uid),
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time() + ANTISPAM_MUTE))
        except Exception as e:
            print("⚠️ Антиспам restrict:", e)
        try:
            bot.send_message(chat_id,
                f"🔇 **МУТ ЗА СПАМ**\n━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 Игрок: **{display_name}**\n"
                f"⚖️ Причина: {ANTISPAM_LIMIT}+ сообщений за {ANTISPAM_WINDOW} сек\n"
                f"🕐 Срок: **10 минут**", parse_mode="Markdown")
        except Exception: pass
    else:
        try:
            bot.send_message(chat_id,
                f"🔇 **МУТ ЗА СПАМ**\n{ANTISPAM_LIMIT}+ сообщений за {ANTISPAM_WINDOW} сек — бот игнорирует вас **10 минут**.", parse_mode="Markdown")
        except Exception: pass

def _as_hit(chat_id, uid, user):
    _as_cleanup()
    key = (chat_id, uid)
    if _as_is_muted(key) or _as_is_muted((0, uid)): return False
    now = time.time()
    dq = spam_events.setdefault(key, deque())
    dq.append(now)
    while dq and dq[0] < now - ANTISPAM_WINDOW: dq.popleft()
    if len(dq) > ANTISPAM_LIMIT:
        dq.clear()
        _as_do_mute(chat_id, uid, (user.first_name or "Игрок") if user else "Игрок")
        return False
    return True

_orig_pnu = bot.process_new_updates
def antispam_process_updates(updates):
    keep = []
    for u in updates:
        try:
            cb = getattr(u, "callback_query", None)
            if cb is not None:
                user = cb.from_user
                if user and user.id not in ADMIN_IDS:
                    chat_id = cb.message.chat.id if cb.message else 0
                    if not _as_hit(chat_id, str(user.id), user):
                        try: bot.answer_callback_query(cb.id, "🔇 Мут за спам: 10 минут", show_alert=True)
                        except Exception: pass
                        continue
                keep.append(u); continue
            m = getattr(u, "message", None)
            if m is not None:
                user = m.from_user
                if user and user.id not in ADMIN_IDS:
                    chat_id = m.chat.id if m.chat else 0
                    if not _as_hit(chat_id, str(user.id), user): continue
            keep.append(u)
        except Exception:
            keep.append(u)
    _orig_pnu(keep)
bot.process_new_updates = antispam_process_updates

MULT = {3: 1.15, 5: 1.25, 9: 1.45}
BIZ_TERRITORY_COST = {0: 10000, 1: 50000, 2: 250000, 3: 1000000, 4: 5000000, 5: 25000000}
BIZ_BUILDING_COST = {1: 20000, 2: 100000, 3: 500000, 4: 2000000, 5: 10000000}

def get_balance(uid): return data["players"].get(str(uid), {}).get("balance", 0)
def get_nick(uid): return data["players"].get(str(uid), {}).get("username", "Player")
def get_user_short_id(uid): return data["players"].get(str(uid), {}).get("short_id", "0")

def get_user_by_short_id(sid):
    for uid, pdata in data["players"].items():
        if str(pdata.get("short_id")) == str(sid): return uid
    return None

def get_user_by_username(uname):
    uname = uname.replace("@", "").lower()
    for uid, pdata in data["players"].items():
        if pdata.get("username", "").lower() == uname: return uid
    return None

def get_win_multiplier(uid):
    status = data["players"].get(str(uid), {}).get("donate_status", "Нет")
    if status == "VIP": return 1.05
    if status == "Premium": return 1.15
    if status == "Ultra": return 1.25
    return 1.0

def add_balance(uid, amt):
    uid = str(uid)
    if uid not in data["players"]: register_user_by_id(uid)
    data["players"][uid]["balance"] += amt; save_data()

def register_user(user):
    uid = str(user.id)
    if uid not in data["players"]:
        sid = data["next_id"]; data["next_id"] += 1
        data["players"][uid] = {"username": user.username or user.first_name, "balance": 1000, "last_bonus": 0, "short_id": sid, "clan_id": None, "role": "none", "bank": 0, "deposit": 0, "deposit_start": 0, "biz_territory": 0, "biz_building": 1, "biz_tax": 0, "biz_last_collect": int(time.time()), "title": "Нет", "quests": {}, "donate_status": "Нет"}
        save_data()

def register_user_by_id(uid):
    if uid not in data["players"]:
        sid = data["next_id"]; data["next_id"] += 1
        data["players"][uid] = {"username": "Player", "balance": 1000, "last_bonus": 0, "short_id": sid, "clan_id": None, "role": "none", "bank": 0, "deposit": 0, "deposit_start": 0, "biz_territory": 0, "biz_building": 1, "biz_tax": 0, "biz_last_collect": int(time.time()), "title": "Нет", "quests": {}, "donate_status": "Нет"}
        save_data()

# ==================== СТАРТ И ПОМОЩЬ ====================

CHAT_LINK = "https://t.me/+cdHaQDl-5ro0OWQy"
REF_BONUS_INVITER = 5000
REF_BONUS_NEW = 1000

@bot.message_handler(commands=["start"])
def start(message):
    uid = str(message.from_user.id)
    is_new = uid not in data["players"]
    register_user(message.from_user)
    payload = ""
    try: payload = message.text.split(maxsplit=1)[1].strip()
    except Exception: pass
    if is_new and payload.startswith("ref_") and message.chat.type == "private":
        ref_uid = str(payload[4:])
        if ref_uid != uid and ref_uid in data["players"]:
            data["players"][uid]["referrer"] = ref_uid
            add_balance(ref_uid, REF_BONUS_INVITER)
            add_balance(uid, REF_BONUS_NEW)
            save_data()
            bot.send_message(message.chat.id, f"👥 **РЕФЕРАЛЬНАЯ ПРОГРАММА**\n━━━━━━━━━━━━━━━━━━━━\nТы пришёл по ссылке от **{get_nick(ref_uid)}**\n🎁 Начислено: **{REF_BONUS_NEW} 🪙**", parse_mode="Markdown")
            try:
                bot.send_message(int(ref_uid), f"👥 **НОВЫЙ РЕФЕРАЛ!**\n━━━━━━━━━━━━━━━━━━━━\n👤 {get_nick(uid)} присоединился по твоей ссылке\n🎁 Начислено: **+{REF_BONUS_INVITER} 🪙**", parse_mode="Markdown")
            except Exception: pass
    bot.send_message(message.chat.id, 
        f"⚓ **SAILIX CASINO** ⚓\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Добро пожаловать, **{get_nick(message.from_user.id)}**!\n\n"
        f"💰 Ваш баланс: **{get_balance(message.from_user.id)} 🪙**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Забирай ежедневный бонус в меню\n"
        f"💼 Открой бизнес — пассивный доход\n"
        f"🛡 Создай клан и играй с друзьями\n"
        f"💬 Наш чат — [вступить в чат]({CHAT_LINK})\n"
        f"📖 Помощь — по любому вопросу\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Управление — кнопками внизу экрана 👇",
        parse_mode="Markdown", reply_markup=main_menu_kb(message.from_user.id in ADMIN_IDS))

@bot.message_handler(commands=['help'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ['помощь', 'help'])
def help_command(message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🎮 Игры", callback_data="help_games"), InlineKeyboardButton("💼 Экономика", callback_data="help_eco"))
    kb.add(InlineKeyboardButton("🛡 Кланы", callback_data="help_clans"), InlineKeyboardButton("💎 Донат", callback_data="help_donate"))
    bot.reply_to(message, "📖 **МЕНЮ ПОМОЩИ**\n━━━━━━━━━━━━━━━━━━━━\nВыберите раздел кнопками ниже:", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("help_"))
def help_callbacks(call):
    section = call.data.split("_")[1]
    if section == "games": text = "🎮 **ИГРЫ**\n━━━━━━━━━━━━━━━━━━━━\n• `слот 1000` — автомат\n• `мины 1000 5` — поле (3, 5, 9 бомб)\n• `краш 1000` — растущий коэф.\n• `джокер 1000` — лесенка удачи\n• `1000 5 красное` — рулетка (запуск `го`)"
    elif section == "eco": text = "💼 **ЭКОНОМИКА**\n━━━━━━━━━━━━━━━━━━━━\n• `б` — баланс\n• `бонус` — ежедневный бонус\n• `профиль` — профиль\n• `узнать ид` — ID игрока\n• `топ` — топ-10\n• `банк` — банк\n• `мой бизнес` — бизнес\n• `квесты` — задания\n• `перевод 1000 ник`\n• `передать 1000` (в ответ)\n• `промо КОД`\n• `рефералка`\n• `сменить ник НОВЫЙ_НИК`"
    elif section == "clans": text = "🛡 **КЛАНЫ**\n━━━━━━━━━━━━━━━━━━━━\n• `создать клан ИМЯ` (10000)\n• `вступить клан ID`\n• `мой клан`\n• `выйти из клана`\n• `топ кланов`\n• `клан казна положить/снять СУММА`"
    elif section == "donate": text = "💎 **ДОНАТ**\n━━━━━━━━━━━━━━━━━━━━\n• `донат` — пополнить\n• `донат статус` — купить статус\n• `мой статус` — узнать статус"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=call.message.reply_markup, parse_mode="Markdown")
    bot.answer_callback_query(call.id)

# ==================== БОНУС ====================

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'бонус')
def daily_bonus(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    p = data["players"][uid]
    now = int(time.time())
    last = p.get("last_bonus", 0)
    if now - last < 86400:
        left = 86400 - (now - last)
        return bot.reply_to(message, f"⏳ **Подождите!**\nСледующий бонус через **{left // 3600} ч. {(left % 3600) // 60} мин.**", parse_mode="Markdown")
    reward = random.randint(1000, 3000)
    add_balance(uid, reward)
    data["players"][uid]["last_bonus"] = now; save_data()
    bot.reply_to(message, f"🎁 **ЕЖЕДНЕВНЫЙ БОНУС**\n━━━━━━━━━━━━━━━━━━━━\nВы получили: **{reward} 🪙**\n━━━━━━━━━━━━━━━━━━━━\nПриходите завтра!", parse_mode="Markdown")

# ==================== ДОНАТ И СТАТУСЫ ====================

@bot.message_handler(commands=['donate'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'донат')
def donate_menu(message):
    text = "💎 **ПОПОЛНЕНИЕ БАЛАНСА**\n━━━━━━━━━━━━━━━━━━━━\n1 ⭐ = 1000 🪙\n\nВыберите пакет:"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("25 ⭐ = 25000 🪙", callback_data="donate_pack_25"))
    kb.add(InlineKeyboardButton("50 ⭐ = 50000 🪙", callback_data="donate_pack_50"))
    kb.add(InlineKeyboardButton("100 ⭐ = 100000 🪙", callback_data="donate_pack_100"))
    kb.add(InlineKeyboardButton("✏️ Ввести свою сумму", callback_data="donate_custom"))
    bot.reply_to(message, text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("donate_"))
def donate_process(call):
    if call.data == "donate_custom":
        msg = bot.send_message(call.message.chat.id, "✏️ Введите количество звезд (от 1 до 10000):")
        bot.register_next_step_handler(msg, process_custom_stars)
        return
    stars = int(call.data.split("_")[2])
    coins = stars * 1000
    prices = [LabeledPrice(label=f"{coins} монет", amount=stars)]
    try:
        bot.send_invoice(chat_id=call.message.chat.id, title=f"Покупка {coins} монет", description=f"Вы получите {coins} монет на баланс.", invoice_payload=f"pay_{stars}", provider_token="", currency="XTR", prices=prices)
        bot.answer_callback_query(call.id, "✅ Счет выставлен!")
    except Exception as e: bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

def process_custom_stars(message):
    try:
        stars = int(message.text)
        if stars < 1 or stars > 10000: return bot.reply_to(message, "❌ От 1 до 10000 звезд.")
        coins = stars * 1000
        prices = [LabeledPrice(label=f"{coins} монет", amount=stars)]
        bot.send_invoice(chat_id=message.chat.id, title=f"Покупка {coins} монет", description=f"Вы получите {coins} монет на баланс.", invoice_payload=f"pay_{stars}", provider_token="", currency="XTR", prices=prices)
    except: bot.reply_to(message, "❌ Введите число.")

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("status_"):
        status = payload.split("_")[1]
        uid = str(message.from_user.id)
        if uid not in data["players"]: register_user(message.from_user)
        data["players"][uid]["donate_status"] = status
        save_data()
        bot.send_message(message.chat.id, f"🎉 **ОПЛАТА УСПЕШНА!**\n━━━━━━━━━━━━━━━━━━━━\nВы получили статус **{status}**!", parse_mode="Markdown")
        return

    stars = int(payload.split("_")[1])
    coins = stars * 1000
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    add_balance(uid, coins)
    
    current_status = data["players"][uid].get("donate_status", "Нет")
    if stars >= 750 and current_status != "Ultra": data["players"][uid]["donate_status"] = "Ultra"; bot.send_message(message.chat.id, f"🎉 **ОПЛАТА УСПЕШНА!**\n━━━━━━━━━━━━━━━━━━━━\nВы получили **{coins} 🪙** и статус **Ultra**!", parse_mode="Markdown")
    elif stars >= 450 and current_status not in ["Premium", "Ultra"]: data["players"][uid]["donate_status"] = "Premium"; bot.send_message(message.chat.id, f"🎉 **ОПЛАТА УСПЕШНА!**\n━━━━━━━━━━━━━━━━━━━━\nВы получили **{coins} 🪙** и статус **Premium**!", parse_mode="Markdown")
    elif stars >= 200 and current_status not in ["VIP", "Premium", "Ultra"]: data["players"][uid]["donate_status"] = "VIP"; bot.send_message(message.chat.id, f"🎉 **ОПЛАТА УСПЕШНА!**\n━━━━━━━━━━━━━━━━━━━━\nВы получили **{coins} 🪙** и статус **VIP**!", parse_mode="Markdown")
    else: bot.send_message(message.chat.id, f"🎉 **ОПЛАТА УСПЕШНА!**\n━━━━━━━━━━━━━━━━━━━━\nВы получили **{coins} 🪙**.", parse_mode="Markdown")
    save_data()

@bot.message_handler(commands=['donate_status'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'донат статус')
def donate_status_menu(message):
    text = "💎 **ДОНАТ-СТАТУСЫ**\n━━━━━━━━━━━━━━━━━━━━\n👑 **VIP** (200 ⭐) — +5% к выигрышу\n💎 **Premium** (450 ⭐) — +15% к выигрышу\n⚡ **Ultra** (750 ⭐) — +25% к выигрышу + просмотр профилей"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("👑 VIP (200 ⭐)", callback_data="buy_status_VIP"))
    kb.add(InlineKeyboardButton("💎 Premium (450 ⭐)", callback_data="buy_status_Premium"))
    kb.add(InlineKeyboardButton("⚡ Ultra (750 ⭐)", callback_data="buy_status_Ultra"))
    bot.reply_to(message, text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_status_"))
def buy_status(call):
    status = call.data.split("_")[2]
    stars = {"VIP": 200, "Premium": 450, "Ultra": 750}[status]
    prices = [LabeledPrice(label=f"Статус {status}", amount=stars)]
    try:
        bot.send_invoice(chat_id=call.message.chat.id, title=f"Покупка статуса {status}", description=f"Вы получите статус {status}.", invoice_payload=f"status_{status}", provider_token="", currency="XTR", prices=prices)
        bot.answer_callback_query(call.id, "✅ Счет выставлен!")
    except Exception as e: bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "мой статус")
def my_status(message):
    uid = str(message.from_user.id)
    status = data["players"].get(uid, {}).get("donate_status", "Нет")
    bot.reply_to(message, f"💎 **ВАШ СТАТУС**\n━━━━━━━━━━━━━━━━━━━━\nТекущий статус: **{status}**", parse_mode="Markdown")

# ==================== БАЗОВЫЕ КОМАНДЫ ====================

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'б')
def balance_command(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    bot.reply_to(message, f"💰 **БАЛАНС**\n━━━━━━━━━━━━━━━━━━━━\nВаш баланс: **{get_balance(uid)} 🪙**", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ['узнать ид', 'мой ид', 'мой id'])
def get_id_command(message):
    uid = str(message.from_user.id)
    target_uid = uid
    if message.reply_to_message:
        target_uid = str(message.reply_to_message.from_user.id)
        caller_status = data["players"].get(uid, {}).get("donate_status", "Нет")
        if uid not in [str(a) for a in ADMIN_IDS] and caller_status != "Ultra":
            return bot.reply_to(message, "❌ **ДОСТУП ЗАПРЕЩЕН**\nСмотреть чужой ID могут только админы и Ultra.", parse_mode="Markdown")
    if target_uid not in data["players"]: register_user_by_id(target_uid)
    sid = get_user_short_id(target_uid)
    bot.reply_to(message, f"🆔 **ID ИГРОКА**\n━━━━━━━━━━━━━━━━━━━━\n`{sid}`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("сменить ник"))
def change_nick(message):
    uid = str(message.from_user.id)
    try:
        new_nick = message.text[12:].strip()
        if len(new_nick) < 3 or len(new_nick) > 15: return bot.reply_to(message, "❌ Ник от 3 до 15 символов.")
        data["players"][uid]["username"] = new_nick
        save_data()
        bot.reply_to(message, f"✅ **НИК ИЗМЕНЕН**\n━━━━━━━━━━━━━━━━━━━━\nНовый ник: **{new_nick}**", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `сменить ник НОВЫЙ_НИК`")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'профиль')
def profile_command(message):
    uid = str(message.from_user.id)
    target_uid = uid
    if message.reply_to_message:
        target_uid = str(message.reply_to_message.from_user.id)
        caller_status = data["players"].get(uid, {}).get("donate_status", "Нет")
        if uid not in [str(a) for a in ADMIN_IDS] and caller_status != "Ultra":
            return bot.reply_to(message, "❌ **ДОСТУП ЗАПРЕЩЕН**\nСмотреть чужой профиль могут только админы и Ultra.", parse_mode="Markdown")
    if target_uid not in data["players"]: register_user_by_id(target_uid)
    p = data["players"][target_uid]
    clan_name = "Нет"
    if p.get("clan_id"):
        clan = data["clans"].get(str(p["clan_id"]))
        if clan: clan_name = clan.get("name", "Нет")
    text = (f"👤 **ПРОФИЛЬ ИГРОКА**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: `{p.get('short_id', '?')}`\n"
            f"👤 Ник: {p.get('username', '?')}\n"
            f"💎 Статус: {p.get('donate_status', 'Нет')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Баланс: {p.get('balance', 0)} 🪙\n"
            f"🏦 Банк: {p.get('bank', 0)} 🪙\n"
            f"💎 Депозит: {p.get('deposit', 0)} 🪙\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡 Клан: {clan_name}\n"
            f"🏢 Бизнес: {p.get('biz_territory', 0)} м²\n"
            f"👥 Рефералов: {ref_count(target_uid)}")
    if target_uid == uid:
        rkb = InlineKeyboardMarkup()
        rkb.add(InlineKeyboardButton("👥 Рефералка", callback_data="ref_panel"))
        bot.reply_to(message, text, parse_mode="Markdown", reply_markup=rkb)
    else:
        bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ["топ", "топ игроков"])
def top_cmd(message):
    sp = sorted(data["players"].items(), key=lambda x: x[1].get("balance", 0), reverse=True)[:10]
    text = "🏆 **ТОП-10 ИГРОКОВ**\n━━━━━━━━━━━━━━━━━━━━\n" + "".join([f"{i}. {p.get('username', '?')} — {p.get('balance', 0)} 🪙\n" for i, (u, p) in enumerate(sp, 1)])
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "чаты")
def chats_cmd(message):
    bot.reply_to(message, f"💬 **НАШИ ЧАТЫ**\n━━━━━━━━━━━━━━━━━━━━\n💬 Наш чат: [вступить в чат]({CHAT_LINK})", parse_mode="Markdown")

# ==================== ДУЭЛИ (Камень-Ножницы-Бумага) ====================

RPS_MAP = {
    "камень": "🪨", "ножницы": "✂️", "бумага": "📄",
    "к": "🪨", "н": "✂️", "б": "📄",
    "rock": "🪨", "scissors": "✂️", "paper": "📄"
}
RPS_CHOICES = {"камень": "камень", "ножницы": "ножницы", "бумага": "бумага",
               "к": "камень", "н": "ножницы", "б": "бумага",
               "rock": "камень", "scissors": "ножницы", "paper": "бумага"}

def rps_winner(c1, c2):
    """Возвращает 1 если c1 победил, 2 если c2, 0 ничья"""
    if c1 == c2:
        return 0
    wins = {"камень": "ножницы", "ножницы": "бумага", "бумага": "камень"}
    if wins[c1] == c2:
        return 1
    return 2

def find_pending_duel_for(uid):
    uid = str(uid)
    for duel_id, duel in data["duels"].items():
        if duel["status"] == "pending" and duel["opponent"] == uid:
            return duel_id, duel
    return None, None

def find_active_duel_for(uid):
    uid = str(uid)
    for duel_id, duel in data["duels"].items():
        if duel["status"] == "choosing" and uid in (duel["challenger"], duel["opponent"]):
            return duel_id, duel
    return None, None

def resolve_rps_duel(duel_id, duel):
    c1 = duel.get("choice1")
    c2 = duel.get("choice2")
    if not c1 or not c2:
        return

    amount = duel["amount"]
    pot = amount * 2
    ch = duel["challenger"]
    op = duel["opponent"]

    result = rps_winner(c1, c2)
    emoji1 = RPS_MAP.get(c1, c1)
    emoji2 = RPS_MAP.get(c2, c2)

    if result == 0:
        # Ничья — возвращаем ставки
        add_balance(ch, amount)
        add_balance(op, amount)
        duel["status"] = "draw"
        save_data()
        text = (
            f"⚔️ **ДУЭЛЬ — НИЧЬЯ**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {get_nick(ch)}: {emoji1} **{c1}**\n"
            f"👤 {get_nick(op)}: {emoji2} **{c2}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤝 Ставки возвращены ({amount} 🪙 каждому)"
        )
    else:
        if result == 1:
            winner, loser = ch, op
            w_choice, l_choice = c1, c2
            w_emoji, l_emoji = emoji1, emoji2
        else:
            winner, loser = op, ch
            w_choice, l_choice = c2, c1
            w_emoji, l_emoji = emoji2, emoji1

        add_balance(winner, pot)
        duel["status"] = "finished"
        duel["winner"] = winner
        save_data()

        pot_str = f"{pot:,}".replace(",", " ")
        text = (
            f"⚔️ **РЕЗУЛЬТАТ ДУЭЛИ**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 **{get_nick(winner)}**: {w_emoji} **{w_choice}**\n"
            f"💀 **{get_nick(loser)}**: {l_emoji} **{l_choice}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Банк: **{pot_str} 🪙**"
        )

    try:
        bot.send_message(int(ch), text, parse_mode="Markdown")
        bot.send_message(int(op), text, parse_mode="Markdown")
    except:
        pass

def send_choice_buttons(uid, duel_id):
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("🪨 Камень", callback_data=f"rps_{duel_id}_камень"),
        InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_{duel_id}_ножницы"),
        InlineKeyboardButton("📄 Бумага", callback_data=f"rps_{duel_id}_бумага")
    )
    try:
        bot.send_message(int(uid),
            "🎮 **Выбери ход:**\n"
            "Нажми кнопку или напиши: `камень` / `ножницы` / `бумага`",
            reply_markup=kb, parse_mode="Markdown")
    except:
        pass

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("дуэль "))
def duel_challenge(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]:
        register_user(message.from_user)

    parts = message.text.split()
    # Поддержка: дуэль сумма  |  дуэль ID сумма  |  дуэль @ник сумма
    target_uid = None
    amount = None

    if len(parts) == 2:
        # дуэль 5000 — нужно ответить на сообщение игрока
        try:
            amount = int(parts[1].replace(" ", "").replace(",", ""))
        except:
            return bot.reply_to(message, "❌ Формат: `дуэль сумма` (в ответ) или `дуэль ID/@ник сумма`", parse_mode="Markdown")
        if message.reply_to_message:
            target_uid = str(message.reply_to_message.from_user.id)
        else:
            return bot.reply_to(message, "❌ Укажи игрока: `дуэль ID сумма` или ответь на его сообщение командой `дуэль сумма`", parse_mode="Markdown")
    elif len(parts) >= 3:
        target_raw = parts[1]
        try:
            amount = int(parts[2].replace(" ", "").replace(",", ""))
        except:
            return bot.reply_to(message, "❌ Сумма должна быть числом.")
        if target_raw.startswith("@"):
            target_uid = get_user_by_username(target_raw)
        else:
            try:
                sid = int(target_raw)
                target_uid = get_user_by_short_id(sid)
            except:
                pass
    else:
        return bot.reply_to(message,
            "❌ Формат:\n`дуэль ID сумма`\n`дуэль @ник сумма`\n`дуэль сумма` (в ответ на сообщение)\n\nПример: `дуэль 15 5000`",
            parse_mode="Markdown")

    if amount is None or amount < 100:
        return bot.reply_to(message, "❌ Минимальная ставка — 100 🪙")
    if amount > 10_000_000:
        return bot.reply_to(message, "❌ Максимальная ставка — 10 000 000 🪙")
    if not target_uid:
        return bot.reply_to(message, "❌ Игрок не найден.")

    target_uid = str(target_uid)
    if target_uid == uid:
        return bot.reply_to(message, "❌ Нельзя вызвать самого себя.")
    if get_balance(uid) < amount:
        return bot.reply_to(message, "❌ У тебя недостаточно средств.")
    if get_balance(target_uid) < amount:
        return bot.reply_to(message, "❌ У соперника недостаточно средств.")

    for d in data["duels"].values():
        if d["status"] in ("pending", "choosing"):
            participants = [d["challenger"], d["opponent"]]
            if uid in participants or target_uid in participants:
                return bot.reply_to(message, "❌ У одного из вас уже есть активный вызов.")

    duel_id = str(int(time.time() * 1000))
    data["duels"][duel_id] = {
        "challenger": uid,
        "opponent": target_uid,
        "amount": amount,
        "status": "pending",
        "choice1": None,
        "choice2": None,
        "created": int(time.time())
    }
    save_data()

    amount_str = f"{amount:,}".replace(",", " ")
    challenger_name = get_nick(uid)

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Принять", callback_data=f"duel_accept_{duel_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"duel_reject_{duel_id}")
    )

    try:
        bot.send_message(
            int(target_uid),
            f"⚔️ **ВЫЗОВ НА ДУЭЛЬ (КНБ)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **{challenger_name}** вызывает тебя!\n"
            f"💰 Ставка: **{amount_str} 🪙**\n"
            f"🎮 Камень · Ножницы · Бумага\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Напиши `принять` или нажми кнопку.\n"
            f"У тебя **2 минуты**.",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except Exception:
        del data["duels"][duel_id]
        save_data()
        return bot.reply_to(message, "❌ Не удалось отправить вызов.\nСоперник, скорее всего, ещё не запускал бота.")

    bot.reply_to(message,
        f"⚔️ Вызов (КНБ) отправлен **{get_nick(target_uid)}**\n"
        f"Ставка: **{amount_str} 🪙**\n"
        f"Ожидай ответа...",
        parse_mode="Markdown")

    def auto_cancel(duel_id=duel_id, uid=uid, target_uid=target_uid):
        time.sleep(120)
        if duel_id in data["duels"] and data["duels"][duel_id]["status"] == "pending":
            data["duels"][duel_id]["status"] = "expired"
            save_data()
            try:
                bot.send_message(int(uid), "⌛️ Твой вызов на дуэль истёк.")
                bot.send_message(int(target_uid), "⌛️ Вызов на дуэль истёк.")
            except:
                pass
    threading.Thread(target=auto_cancel, daemon=True).start()


# Принятие текстом (без «го» — оно используется рулеткой)
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ["принять", "accept", "да"])
def duel_accept_text(message):
    uid = str(message.from_user.id)
    duel_id, duel = find_pending_duel_for(uid)
    if not duel:
        return bot.reply_to(message, "❌ У тебя нет входящих вызовов на дуэль.")

    challenger = duel["challenger"]
    opponent = duel["opponent"]
    amount = duel["amount"]

    if get_balance(challenger) < amount or get_balance(opponent) < amount:
        duel["status"] = "cancelled"
        save_data()
        return bot.reply_to(message, "❌ Недостаточно средств у одного из игроков. Дуэль отменена.")

    add_balance(challenger, -amount)
    add_balance(opponent, -amount)

    duel["status"] = "choosing"
    duel["choice1"] = None
    duel["choice2"] = None
    save_data()

    bot.reply_to(message, "⚔️ Дуэль принята! Выбирайте ход.")
    try:
        bot.send_message(int(challenger), f"⚔️ **{get_nick(opponent)}** принял вызов! Выбирайте ход.")
    except:
        pass

    send_choice_buttons(challenger, duel_id)
    send_choice_buttons(opponent, duel_id)


# Отклонение текстом
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ["отклонить", "нет", "no", "отмена"])
def duel_reject_text(message):
    uid = str(message.from_user.id)
    duel_id, duel = find_pending_duel_for(uid)
    if not duel:
        return
    duel["status"] = "rejected"
    save_data()
    bot.reply_to(message, "❌ Ты отклонил вызов.")
    try:
        bot.send_message(int(duel["challenger"]), f"❌ **{get_nick(uid)}** отклонил твой вызов.")
    except:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("duel_"))
def duel_callback(call):
    try:
        parts = call.data.split("_")
        action = parts[1]
        duel_id = parts[2]
    except:
        return bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)

    duel = data["duels"].get(duel_id)
    if not duel or duel["status"] != "pending":
        return bot.answer_callback_query(call.id, "❌ Вызов уже недействителен.", show_alert=True)

    uid = str(call.from_user.id)
    if uid != duel["opponent"]:
        return bot.answer_callback_query(call.id, "❌ Это не твой вызов.", show_alert=True)

    challenger = duel["challenger"]
    opponent = duel["opponent"]
    amount = duel["amount"]

    if action == "reject":
        duel["status"] = "rejected"
        save_data()
        bot.edit_message_text("❌ Ты отклонил вызов.", call.message.chat.id, call.message.message_id)
        try:
            bot.send_message(int(challenger), f"❌ **{get_nick(opponent)}** отклонил твой вызов.")
        except:
            pass
        return bot.answer_callback_query(call.id)

    # accept
    if get_balance(challenger) < amount or get_balance(opponent) < amount:
        duel["status"] = "cancelled"
        save_data()
        bot.edit_message_text("❌ Недостаточно средств.", call.message.chat.id, call.message.message_id)
        return bot.answer_callback_query(call.id, "Недостаточно средств.", show_alert=True)

    add_balance(challenger, -amount)
    add_balance(opponent, -amount)

    duel["status"] = "choosing"
    duel["choice1"] = None
    duel["choice2"] = None
    save_data()

    bot.edit_message_text("⚔️ Дуэль принята! Выбирайте ход.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Дуэль началась!")

    try:
        bot.send_message(int(challenger), f"⚔️ **{get_nick(opponent)}** принял вызов! Выбирайте ход.")
    except:
        pass

    send_choice_buttons(challenger, duel_id)
    send_choice_buttons(opponent, duel_id)


# Выбор хода кнопкой
@bot.callback_query_handler(func=lambda call: call.data.startswith("rps_"))
def rps_choice_callback(call):
    try:
        _, duel_id, choice = call.data.split("_", 2)
    except:
        return bot.answer_callback_query(call.id, "Ошибка.", show_alert=True)

    duel = data["duels"].get(duel_id)
    if not duel or duel["status"] != "choosing":
        return bot.answer_callback_query(call.id, "❌ Дуэль уже закончена.", show_alert=True)

    uid = str(call.from_user.id)
    if uid not in (duel["challenger"], duel["opponent"]):
        return bot.answer_callback_query(call.id, "❌ Это не твоя дуэль.", show_alert=True)

    choice = RPS_CHOICES.get(choice.lower())
    if not choice:
        return bot.answer_callback_query(call.id, "Неверный выбор.")

    if uid == duel["challenger"]:
        if duel.get("choice1"):
            return bot.answer_callback_query(call.id, "Ты уже выбрал!", show_alert=True)
        duel["choice1"] = choice
    else:
        if duel.get("choice2"):
            return bot.answer_callback_query(call.id, "Ты уже выбрал!", show_alert=True)
        duel["choice2"] = choice

    save_data()
    bot.answer_callback_query(call.id, f"Выбрано: {choice}")
    bot.edit_message_text(f"✅ Ты выбрал: **{RPS_MAP.get(choice, choice)} {choice}**\nЖдём соперника...",
                          call.message.chat.id, call.message.message_id, parse_mode="Markdown")

    if duel.get("choice1") and duel.get("choice2"):
        resolve_rps_duel(duel_id, duel)


# Выбор хода текстом
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ["камень", "ножницы", "бумага", "к", "н", "б", "rock", "scissors", "paper"])
def rps_choice_text(message):
    uid = str(message.from_user.id)
    duel_id, duel = find_active_duel_for(uid)
    if not duel:
        return

    choice = RPS_CHOICES.get(message.text.lower().strip())
    if not choice:
        return

    if uid == duel["challenger"]:
        if duel.get("choice1"):
            return bot.reply_to(message, "❌ Ты уже выбрал ход.")
        duel["choice1"] = choice
    else:
        if duel.get("choice2"):
            return bot.reply_to(message, "❌ Ты уже выбрал ход.")
        duel["choice2"] = choice

    save_data()
    bot.reply_to(message, f"✅ Ты выбрал: **{RPS_MAP.get(choice, choice)} {choice}**\nЖдём соперника...", parse_mode="Markdown")

    if duel.get("choice1") and duel.get("choice2"):
        resolve_rps_duel(duel_id, duel)



# ==================== АДМИН-ПАНЕЛЬ ====================

@bot.message_handler(commands=['admin'])
@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ['админ', 'админка'])
def admin_panel(message):
    if message.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📊 Статистика", callback_data="adm_stats"), InlineKeyboardButton("💰 Выдать деньги", callback_data="adm_give"))
    kb.add(InlineKeyboardButton("👑 Выдать статус", callback_data="adm_setstatus"), InlineKeyboardButton("🗑 Обнулить игрока", callback_data="adm_reset"))
    kb.add(InlineKeyboardButton("🎁 Создать промо", callback_data="adm_promo"), InlineKeyboardButton("❌ Удалить промо", callback_data="adm_delpromo"))
    kb.add(InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast"))
    bot.reply_to(message, "⚙️ **АДМИН-ПАНЕЛЬ**\n━━━━━━━━━━━━━━━━━━━━\nВыберите действие:", reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callbacks(call):
    if call.from_user.id not in ADMIN_IDS: return bot.answer_callback_query(call.id, "❌ Нет прав.")
    act = call.data.split("_")[1]
    if act == "stats":
        total_players = len(data["players"])
        total_money = sum(p.get("balance", 0) for p in data["players"].values())
        total_clans = len(data["clans"])
        text = f"📊 **СТАТИСТИКА**\n━━━━━━━━━━━━━━━━━━━━\n👥 Игроков: {total_players}\n💰 Общий баланс: {total_money} 🪙\n🛡 Кланов: {total_clans}"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data="adm_back")), parse_mode="Markdown")
    elif act == "give":
        msg = bot.send_message(call.message.chat.id, "Введите `ID СУММА` (например: `3 5000`):")
        bot.register_next_step_handler(msg, admin_give_step)
        bot.answer_callback_query(call.id)
    elif act == "setstatus":
        msg = bot.send_message(call.message.chat.id, "Введите `ID СТАТУС` (например: `3 Ultra`).\nДоступные статусы: `VIP`, `Premium`, `Ultra`, `Нет`:")
        bot.register_next_step_handler(msg, admin_setstatus_step)
        bot.answer_callback_query(call.id)
    elif act == "reset":
        msg = bot.send_message(call.message.chat.id, "Введите `ID` игрока для обнуления баланса (например: `3`):")
        bot.register_next_step_handler(msg, admin_reset_step)
        bot.answer_callback_query(call.id)
    elif act == "promo":
        msg = bot.send_message(call.message.chat.id, "Введите `КОД СУММА` (например: `NEWYEAR 1000`):")
        bot.register_next_step_handler(msg, admin_promo_step)
        bot.answer_callback_query(call.id)
    elif act == "delpromo":
        msg = bot.send_message(call.message.chat.id, "Введите `КОД` промокода для удаления:")
        bot.register_next_step_handler(msg, admin_delpromo_step)
        bot.answer_callback_query(call.id)
    elif act == "broadcast":
        msg = bot.send_message(call.message.chat.id, "Введите текст для рассылки всем игрокам:")
        bot.register_next_step_handler(msg, admin_broadcast_step)
        bot.answer_callback_query(call.id)
    elif act == "back":
        admin_panel(call.message)

def admin_give_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        sid, amount = message.text.split(); amount = int(amount)
        target_uid = get_user_by_short_id(sid)
        if not target_uid: return bot.reply_to(message, "❌ Игрок не найден.")
        add_balance(target_uid, amount)
        bot.reply_to(message, f"✅ Выдано {amount} 🪙 игроку с ID {sid}.")
    except: bot.reply_to(message, "❌ Ошибка формата.")

def admin_setstatus_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        sid, status = message.text.split()
        smap = {"vip": "VIP", "premium": "Premium", "ultra": "Ultra", "нет": "Нет"}
        status = smap.get(status.lower(), status)
        if status not in ["VIP", "Premium", "Ultra", "Нет"]:
            return bot.reply_to(message, "❌ Неверный статус. Доступно: VIP, Premium, Ultra, Нет.")
        target_uid = get_user_by_short_id(sid)
        if not target_uid: return bot.reply_to(message, "❌ Игрок не найден.")
        data["players"][target_uid]["donate_status"] = status
        save_data()
        bot.reply_to(message, f"✅ Игроку с ID {sid} выдан статус **{status}**.", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ Ошибка формата. Введите `ID Статус`")

def admin_reset_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        sid = message.text.strip()
        target_uid = get_user_by_short_id(sid)
        if not target_uid: return bot.reply_to(message, "❌ Игрок не найден.")
        data["players"][target_uid]["balance"] = 0
        data["players"][target_uid]["bank"] = 0
        data["players"][target_uid]["deposit"] = 0
        data["players"][target_uid]["deposit_start"] = 0
        save_data()
        bot.reply_to(message, f"✅ Баланс и банк игрока с ID {sid} обнулены.")
    except: bot.reply_to(message, "❌ Ошибка.")

def admin_promo_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        code, amount = message.text.upper().split(); amount = int(amount)
        data["promos"][code] = amount; save_data()
        bot.reply_to(message, f"✅ Промокод {code} на {amount} 🪙 создан.")
    except: bot.reply_to(message, "❌ Ошибка формата.")

def admin_delpromo_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    code = message.text.upper().strip()
    if code in data["promos"]: del data["promos"][code]; save_data(); bot.reply_to(message, f"✅ Промокод {code} удален.")
    else: bot.reply_to(message, "❌ Не найден.")

def admin_broadcast_step(message):
    if message.from_user.id not in ADMIN_IDS: return
    text = message.text
    success = 0
    for uid in list(data["players"].keys()):
        try: bot.send_message(uid, text); success += 1
        except: pass
    bot.reply_to(message, f"✅ Рассылка отправлена {success} игрокам.")

# ==================== СТАРЫЕ АДМИН-КОМАНДЫ ====================

@bot.message_handler(commands=['wipe'])
def wipe_command(message):
    if message.from_user.id not in ADMIN_IDS: return
    global data
    data = {"players": {}, "promos": {}, "activations": [], "history": [], "clans": {}, "next_id": 1, "active_games": {}, "roulette_bets": {}}
    save_data()
    bot.reply_to(message, "💥 **База данных очищена!**", parse_mode="Markdown")

@bot.message_handler(commands=['give'])
def give_command(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        parts = message.text.split(); target_sid = parts[1]; amount = int(parts[2])
        target_uid = get_user_by_short_id(target_sid)
        if not target_uid: return bot.reply_to(message, f"❌ Игрок {target_sid} не найден.")
        add_balance(target_uid, amount)
        bot.reply_to(message, f"✅ Выдано {amount} 🪙 игроку {get_nick(target_uid)}.", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `/give ID СУММА`", parse_mode="Markdown")

@bot.message_handler(commands=['reset'])
def reset_user_command(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        target_sid = message.text.split()[1]; target_uid = get_user_by_short_id(target_sid)
        if not target_uid: return bot.reply_to(message, f"❌ Игрок {target_sid} не найден.")
        data["players"][target_uid]["balance"] = 0
        data["players"][target_uid]["bank"] = 0
        data["players"][target_uid]["deposit"] = 0
        data["players"][target_uid]["deposit_start"] = 0
        save_data()
        bot.reply_to(message, f"✅ Баланс игрока {target_sid} обнулен.")
    except: bot.reply_to(message, "❌ `/reset ID`", parse_mode="Markdown")

@bot.message_handler(commands=['get_profile'])
def get_profile_command(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        target_sid = message.text.split()[1]; target_uid = get_user_by_short_id(target_sid)
        if not target_uid: return bot.reply_to(message, f"❌ Игрок {target_sid} не найден.")
        p = data["players"][target_uid]
        bot.reply_to(message, f"👤 **Профиль (Админ)**\n🆔 ID: `{p.get('short_id')}`\nНик: {p.get('username')}\nСтатус: {p.get('donate_status')}\nБаланс: {p.get('balance')} 🪙", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `/get_profile ID`", parse_mode="Markdown")

# ==================== ПРОМОКОДЫ ====================

@bot.message_handler(commands=['createpromo'])
def create_promo(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        parts = message.text.split(); code = parts[1].upper(); amount = int(parts[2])
        if code in data["promos"]: return bot.reply_to(message, "❌ Такой промокод уже существует.")
        data["promos"][code] = amount; save_data()
        bot.reply_to(message, f"✅ Промокод **{code}** на {amount} 🪙 создан!", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `/createpromo КОД СУММА`", parse_mode="Markdown")

@bot.message_handler(commands=['deletepromo'])
def delete_promo(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        code = message.text.split()[1].upper()
        if code in data["promos"]:
            del data["promos"][code]; save_data()
            bot.reply_to(message, f"✅ Промокод **{code}** удален.", parse_mode="Markdown")
        else: bot.reply_to(message, "❌ Такого промокода нет.")
    except: bot.reply_to(message, "❌ `/deletepromo КОД`", parse_mode="Markdown")

def activate_promo_code(uid, code):
    code = (code or "").strip().upper()
    if not code: return "❌ Введите код промокода."
    if code not in data["promos"]: return "❌ **НЕСУЩЕСТВУЮЩИЙ ПРОМОКОД**\nТакого кода нет или он уже удалён."
    key = f"{uid}_{code}"
    if key in data["activations"]: return "❌ Вы уже активировали этот промокод."
    amount = data["promos"][code]
    add_balance(uid, amount)
    data["activations"].append(key); save_data()
    return f"🎉 **ПРОМОКОД АКТИВИРОВАН**\n━━━━━━━━━━━━━━━━━━━━\n🎁 Код: **{code}**\n💰 Начислено: **{amount} 🪙**"

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("промо"))
def activate_promo(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    try: code = message.text.split()[1]
    except: return bot.reply_to(message, "❌ Использование: `промо КОД`", parse_mode="Markdown")
    bot.reply_to(message, activate_promo_code(uid, code), parse_mode="Markdown")

# ==================== КЛАНЫ ====================

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("создать клан"))
def create_clan(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    p = data["players"][uid]
    if p.get("clan_id"): return bot.reply_to(message, "❌ Вы уже состоите в клане.")
    cost = 10000
    if get_balance(uid) < cost: return bot.reply_to(message, f"❌ Недостаточно средств. Нужно {cost} 🪙.")
    try:
        clan_name = message.text[12:].strip()
        if len(clan_name) < 3 or len(clan_name) > 20: return bot.reply_to(message, "❌ Название от 3 до 20 символов.")
    except: return bot.reply_to(message, "❌ `создать клан НАЗВАНИЕ`")
    for c in data["clans"].values():
        if c.get("name", "").lower() == clan_name.lower(): return bot.reply_to(message, "❌ Клан с таким названием уже есть.")
    add_balance(uid, -cost)
    clan_id = str(len(data["clans"]) + 1)
    while clan_id in data["clans"]: clan_id = str(int(clan_id) + 1)
    data["clans"][clan_id] = {"name": clan_name, "owner_id": uid, "owner_name": p.get("username", "?"), "treasury": 0, "members": [uid]}
    p["clan_id"] = clan_id; p["role"] = "создатель"; save_data()
    bot.reply_to(message, f"✅ Клан **{clan_name}** создан!\n🆔 ID клана: `{clan_id}`\n👑 Вы — создатель.\n💰 Списано: {cost} 🪙", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("вступить клан"))
def join_clan(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    p = data["players"][uid]
    if p.get("clan_id"): return bot.reply_to(message, "❌ Вы уже в клане.")
    try: clan_id = message.text.split()[2]
    except: return bot.reply_to(message, "❌ `вступить клан ID`")
    clan = data["clans"].get(clan_id)
    if not clan: return bot.reply_to(message, "❌ Клан не найден.")
    members = [m for m in data["players"].values() if m.get("clan_id") == clan_id]
    if len(members) >= 15: return bot.reply_to(message, "❌ Клан переполнен (макс 15).")
    p["clan_id"] = clan_id; p["role"] = "игрок"; save_data()
    bot.reply_to(message, f"✅ Вы вступили в клан **{clan['name']}**!", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "выйти из клана")
def leave_clan(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {})
    if not p.get("clan_id"): return bot.reply_to(message, "❌ Вы не в клане.")
    if p.get("role") == "создатель": return bot.reply_to(message, "❌ Создатель не может выйти.")
    p["clan_id"] = None; p["role"] = "none"; save_data()
    bot.reply_to(message, "✅ Вы вышли из клана.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "мой клан")
def my_clan_info(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {})
    if not p.get("clan_id"): return bot.reply_to(message, "🛡 У вас нет клана.\n`создать клан ИМЯ` (10000)\n`вступить клан ID`", parse_mode="Markdown")
    cid = str(p["clan_id"]); clan = data["clans"].get(cid)
    if not clan: return bot.reply_to(message, "❌ Клан удален.")
    members = [m for m in data["players"].values() if m.get("clan_id") == cid]
    text = f"🛡 **Клан: {clan['name']}**\n🆔 ID: `{cid}`\n👑 Создатель: {clan['owner_name']}\n👥 Участники: {len(members)}/15\n💰 Казна: {clan.get('treasury', 0)} 🪙\n\nВаша роль: **{p.get('role', 'игрок').upper()}**"
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Участники", callback_data=f"clan_members_{cid}"))
    bot.reply_to(message, text, reply_markup=kb, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "топ кланов")
def top_clans(message):
    clans = sorted(data["clans"].items(), key=lambda x: x[1].get("treasury", 0), reverse=True)[:10]
    if not clans: return bot.reply_to(message, "❌ Нет кланов.")
    text = "🏆 **ТОП КЛАНОВ**\n━━━━━━━━━━━━━━━━━━━━\n" + "".join([f"{i}. {c.get('name')} — {c.get('treasury', 0)} 🪙\n" for i, (cid, c) in enumerate(clans, 1)])
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("клан казна"))
def clan_treasury(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {})
    if not p.get("clan_id"): return bot.reply_to(message, "❌ Вы не в клане.")
    cid = str(p["clan_id"]); clan = data["clans"].get(cid)
    if not clan: return bot.reply_to(message, "❌ Клан не найден.")
    try:
        parts = message.text.split(); action = parts[2].lower(); amount = int(parts[3])
        if amount <= 0: return bot.reply_to(message, "❌ Сумма > 0.")
        if action == "снять":
            if p.get("role") != "создатель": return bot.reply_to(message, "❌ Только создатель может снимать.")
            if clan.get("treasury", 0) < amount: return bot.reply_to(message, "❌ В казне мало денег.")
            clan["treasury"] -= amount; add_balance(uid, amount); save_data()
            bot.reply_to(message, f"✅ Вы сняли {amount} 🪙 из казны.")
        else:
            if get_balance(uid) < amount: return bot.reply_to(message, "❌ Недостаточно средств.")
            add_balance(uid, -amount); clan["treasury"] = clan.get("treasury", 0) + amount; save_data()
            bot.reply_to(message, f"✅ Вы положили {amount} 🪙 в казну.")
    except: bot.reply_to(message, "❌ `клан казна положить 1000`\n`клан казна снять 1000`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("clan_members_"))
def clan_members(call):
    cid = call.data.split("_")[2]; clan = data["clans"].get(cid)
    if not clan: return bot.answer_callback_query(call.id, "❌ Клан не найден.")
    members = [(m.get('username', '?'), m.get('short_id', '?'), m.get('role', 'игрок')) for m in data["players"].values() if m.get("clan_id") == cid]
    text = f"👥 **Участники клана {clan['name']}**\n\n"
    for name, sid, role in members: text += f"• {name} (ID: `{sid}`) — {role}\n"
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Назад", callback_data=f"clan_back_{cid}")), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("clan_back_"))
def clan_back(call):
    uid = str(call.from_user.id); p = data["players"].get(uid, {}); cid = str(p.get("clan_id"))
    if not cid: return
    clan = data["clans"].get(cid)
    if not clan: return
    members = [m for m in data["players"].values() if m.get("clan_id") == cid]
    text = f"🛡 **Клан: {clan['name']}**\n🆔 ID: `{cid}`\n👑 Создатель: {clan['owner_name']}\n👥 Участники: {len(members)}/15\n💰 Казна: {clan.get('treasury', 0)} 🪙\n\nВаша роль: **{p.get('role', 'игрок').upper()}**"
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Участники", callback_data=f"clan_members_{cid}"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown")

def has_perm(uid, act):
    role = data["players"].get(str(uid), {}).get("role", "none")
    if role == "создатель": return True
    if role == "заместитель" and act in ["rename", "appoint", "kick"]: return True
    if role == "модер" and act == "rename": return True
    return False

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("клан исключить"))
def clan_kick(message):
    uid = str(message.from_user.id)
    if not has_perm(uid, "kick"): return bot.reply_to(message, "❌ Нет прав.")
    try:
        target_sid = message.text.split()[2]; target_uid = get_user_by_short_id(target_sid)
        if not target_uid: return bot.reply_to(message, "❌ Игрок не найден.")
        cid = data["players"][uid].get("clan_id")
        if data["players"].get(target_uid, {}).get("clan_id") != cid: return bot.reply_to(message, "❌ Не в вашем клане.")
        if data["players"].get(target_uid, {}).get("role") == "создатель": return bot.reply_to(message, "❌ Нельзя исключить создателя.")
        data["players"][target_uid]["clan_id"] = None; data["players"][target_uid]["role"] = "none"; save_data()
        bot.reply_to(message, f"✅ Игрок `{target_sid}` исключен.")
    except: bot.reply_to(message, "❌ `клан исключить ID`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("клан повысить"))
def clan_promote(message):
    uid = str(message.from_user.id)
    if not has_perm(uid, "appoint"): return bot.reply_to(message, "❌ Нет прав.")
    try:
        target_sid = message.text.split()[2]; target_uid = get_user_by_short_id(target_sid)
        if not target_uid: return bot.reply_to(message, "❌ Игрок не найден.")
        cid = data["players"][uid].get("clan_id")
        if data["players"].get(target_uid, {}).get("clan_id") != cid: return bot.reply_to(message, "❌ Не в вашем клане.")
        current_role = data["players"][target_uid].get("role", "игрок")
        if current_role == "создатель": return bot.reply_to(message, "❌ Это создатель.")
        elif current_role == "заместитель": return bot.reply_to(message, "❌ Уже заместитель.")
        elif current_role == "модер": data["players"][target_uid]["role"] = "заместитель"; new_role = "заместитель"
        else: data["players"][target_uid]["role"] = "модер"; new_role = "модер"
        save_data()
        bot.reply_to(message, f"✅ Игрок `{target_sid}` повышен до **{new_role}**.", parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `клан повысить ID`", parse_mode="Markdown")

# ==================== ИГРЫ ====================

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("слот"))
def slot_machine(message):
    uid = str(message.from_user.id)
    try:
        bet = int(message.text.split()[1])
        if bet <= 0 or get_balance(uid) < bet: return bot.reply_to(message, "❌ Недостаточно средств.")
        add_balance(uid, -bet)
        s = [random.choice(["🍒", "🍋", "💎", "🎰"]) for _ in range(3)]
        win = 0
        if s[0] == s[1] == s[2]:
            if s[0] == "🎰": win = bet * 50
            elif s[0] == "💎": win = bet * 10
            elif s[0] == "🍋": win = bet * 5
            elif s[0] == "🍒": win = bet * 3
        elif s[0] == s[1] or s[1] == s[2] or s[0] == s[2]: win = bet
        
        if win > 0:
            win = int(win * get_win_multiplier(uid))
            add_balance(uid, win)
            msg = f"🎰 **СЛОТ**\n[ {s[0]} | {s[1]} | {s[2]} ]\n🎉 **ВЫИГРЫШ!** +{win} 🪙\nБаланс: {get_balance(uid)}"
        else:
            msg = f"🎰 **СЛОТ**\n[ {s[0]} | {s[1]} | {s[2]} ]\n😢 Проигрыш.\nБаланс: {get_balance(uid)}"
        bot.reply_to(message, msg, parse_mode="Markdown")
        complete_quest(uid, "Сделать ставку в слот")
    except: bot.reply_to(message, "❌ `слот 1000`")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("мины"))
def start_mines(message):
    uid = str(message.from_user.id)
    try:
        p = message.text.split(); bet = int(p[1]); bmb = int(p[2]) if len(p) > 2 else 5
        if bet <= 0 or get_balance(uid) < bet: return bot.reply_to(message, "❌ Мало.")
        if bmb not in MULT: return bot.reply_to(message, "❌ Мины: 3, 5 или 9.")
        add_balance(uid, -bet); gid = str(int(time.time() * 1000))
        data["active_games"][gid] = {"type": "mines", "user": uid, "bet": bet, "mines": random.sample(range(25), bmb), "opened": [], "bombs": bmb}; save_data()
        bot.reply_to(message, f"💣 **МИНЫ** #{gid}\nСтавка: {bet} 🪙\nМин: {bmb}", reply_markup=mines_kb(gid), parse_mode="Markdown")
    except: bot.reply_to(message, "❌ `мины 1000 5`")

def mines_kb(gid):
    g = data["active_games"].get(gid)
    if not g: return None
    kb = InlineKeyboardMarkup(row_width=5)
    for r in range(5): kb.row(*[InlineKeyboardButton("💎" if (r*5+c) in g["opened"] else "❔", callback_data=f"m_{gid}_{r*5+c}") for c in range(5)])
    kb.row(InlineKeyboardButton("⭐ ЗАБРАТЬ", callback_data=f"mcash_{gid}"))
    return kb

@bot.callback_query_handler(func=lambda call: call.data.startswith("m_"))
def mines_play(call):
    _, gid, c = call.data.split("_"); c = int(c); uid = str(call.from_user.id); g = data["active_games"].get(gid)
    if not g or g["user"] != uid or c in g["opened"]: return bot.answer_callback_query(call.id, "❌ Ошибка.")
    if c in g["mines"]:
        del data["active_games"][gid]; save_data()
        kb = InlineKeyboardMarkup(row_width=5)
        for r in range(5): kb.row(*[InlineKeyboardButton("💣" if (r*5+x) in g["mines"] else "💎", callback_data="ignore") for x in range(5)])
        bot.edit_message_text(f"💥 **МИНА!** Потеряно {g['bet']} 🪙.", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode="Markdown"); return
    g["opened"].append(c); mult = MULT[g["bombs"]] ** len(g["opened"]); rw = math.floor(g["bet"] * mult); save_data()
    bot.edit_message_text(f"💣 **МИНЫ**\nСтавка: {g['bet']} 🪙\nОткрыто: {len(g['opened'])}\nМнож: x{mult:.2f}\nЗабрать: {rw} 🪙", call.message.chat.id, call.message.message_id, reply_markup=mines_kb(gid), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("mcash_"))
def mines_out(call):
    gid = call.data.split("_")[1]; uid = str(call.from_user.id); g = data["active_games"].get(gid)
    if not g or g["user"] != uid or not g["opened"]: return
    rw = math.floor(g["bet"] * (MULT[g["bombs"]] ** len(g["opened"])))
    rw = int(rw * get_win_multiplier(uid))
    add_balance(uid, rw); del data["active_games"][gid]; save_data()
    bot.edit_message_text(f"🏆 **Забрано!** +{rw} 🪙", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("краш"))
def start_crash(message):
    uid = str(message.from_user.id)
    try:
        bet = int(message.text.split()[1])
        if bet <= 0 or get_balance(uid) < bet: return bot.reply_to(message, "❌ Мало.")
        add_balance(uid, -bet); gid = str(int(time.time() * 1000))
        data["active_games"][gid] = {"type": "crash", "user": uid, "bet": bet, "crash_point": round(random.uniform(1.1, 10.0), 2), "current": 1.0, "active": True}; save_data()
        complete_quest(uid, "Сыграть в краш")
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💰 ЗАБРАТЬ", callback_data=f"ccash_{gid}"))
        msg = bot.reply_to(message, f"🚀 **CRASH**\nСтавка: {bet}\nКоэф: x1.00", reply_markup=kb)
        threading.Thread(target=crash_loop, args=(gid, uid, msg.chat.id, msg.message_id)).start()
    except: bot.reply_to(message, "❌ `краш 1000`")

def crash_loop(gid, uid, cid, mid):
    time.sleep(1)
    while True:
        g = data["active_games"].get(gid)
        if not g or not g["active"]: break
        g["current"] = round(g["current"] + 0.10, 2); save_data()
        if g["current"] >= g["crash_point"] or random.random() < 0.06:
            g["active"] = False; save_data()
            try: bot.edit_message_text(f"💥 **КРАШ на x{g['current']:.2f}!**\nПотеряно {g['bet']} 🪙.", cid, mid, reply_markup=None, parse_mode="Markdown")
            except: pass
            del data["active_games"][gid]; save_data(); break
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💰 ЗАБРАТЬ", callback_data=f"ccash_{gid}"))
        try: bot.edit_message_text(f"🚀 **CRASH**\nСтавка: {g['bet']}\nКоэф: x{g['current']:.2f}", cid, mid, reply_markup=kb, parse_mode="Markdown")
        except: pass
        time.sleep(1)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ccash_"))
def crash_out(call):
    gid = call.data.split("_")[1]; uid = str(call.from_user.id); g = data["active_games"].get(gid)
    if not g or g["user"] != uid or not g["active"]: return
    g["active"] = False
    rw = math.floor(g["bet"] * g["current"])
    rw = int(rw * get_win_multiplier(uid))
    add_balance(uid, rw); del data["active_games"][gid]; save_data()
    bot.edit_message_text(f"🏆 **Забрано на x{g['current']:.2f}!** +{rw} 🪙", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("джокер"))
def start_joker(message):
    uid = str(message.from_user.id)
    try:
        bet = int(message.text.split()[1])
        if bet <= 0 or get_balance(uid) < bet: return bot.reply_to(message, "❌ Недостаточно средств.")
        add_balance(uid, -bet); gid = str(int(time.time() * 1000))
        data["active_games"][gid] = {"type": "joker", "user": uid, "bet": bet, "step": 0, "mult": 1.0, "active": True}
        save_data()
        show_joker_menu(message.chat.id, gid, uid, None)
    except: bot.reply_to(message, "❌ `джокер 1000`")

def show_joker_menu(cid, gid, uid, msg_id):
    g = data["active_games"].get(gid)
    if not g: return
    mult = g["mult"]
    text = f"🃏 **ДЖОКЕР**\nСтавка: {g['bet']} 🪙\nШаг: {g['step']} / 12\nТекущий множитель: x{mult:.2f}\nЗабрать: {math.floor(g['bet'] * mult)} 🪙\n\nВыберите ячейку:"
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(InlineKeyboardButton("🎴 1", callback_data=f"j_{gid}_0"), InlineKeyboardButton("🎴 2", callback_data=f"j_{gid}_1"), InlineKeyboardButton("🎴 3", callback_data=f"j_{gid}_2"))
    if g["step"] > 0: kb.row(InlineKeyboardButton("💰 ЗАБРАТЬ", callback_data=f"jcash_{gid}"))
    if msg_id: bot.edit_message_text(text, cid, msg_id, reply_markup=kb, parse_mode="Markdown")
    else: bot.send_message(cid, text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("j_"))
def joker_play(call):
    _, gid, choice = call.data.split("_"); uid = str(call.from_user.id); g = data["active_games"].get(gid)
    if not g or g["user"] != uid or not g["active"]: return bot.answer_callback_query(call.id, "❌ Ошибка.")
    if g["step"] >= 12: return bot.answer_callback_query(call.id, "❌ Максимум!")
    
    lose_idx = random.randint(0, 2)
    if int(choice) == lose_idx:
        g["active"] = False
        bot.edit_message_text(f"🃏 **ПРОИГРЫШ!**\nВы попали в проигрышную ячейку.\nПотеряно: {g['bet']} 🪙", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="Markdown")
        del data["active_games"][gid]; save_data(); return
    
    g["step"] += 1
    mult_table = [1.2, 1.4, 1.6, 1.9, 2.2, 2.5, 2.9, 3.3, 3.8, 4.2, 4.6, 5.0]
    g["mult"] = mult_table[g["step"] - 1]
    save_data()
    
    if g["step"] >= 12:
        rw = math.floor(g["bet"] * g["mult"])
        add_balance(uid, rw); g["active"] = False
        bot.edit_message_text(f"🏆 **ДЖОКЕР ПРОЙДЕН!**\nМаксимальный множитель x{g['mult']:.2f}!\nВыигрыш: {rw} 🪙", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="Markdown")
        del data["active_games"][gid]; save_data(); return
    
    bot.answer_callback_query(call.id, f"✅ Шаг {g['step']}! Множитель x{g['mult']:.2f}")
    show_joker_menu(call.message.chat.id, gid, uid, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("jcash_"))
def joker_cash(call):
    gid = call.data.split("_")[1]; uid = str(call.from_user.id); g = data["active_games"].get(gid)
    if not g or g["user"] != uid or not g["active"]: return
    rw = math.floor(g["bet"] * g["mult"])
    add_balance(uid, rw); g["active"] = False
    bot.edit_message_text(f"🏆 **Забрано!** +{rw} 🪙", call.message.chat.id, call.message.message_id, reply_markup=None, parse_mode="Markdown")
    del data["active_games"][gid]; save_data()

@bot.message_handler(func=lambda m: m.text and len(m.text.split()) >= 2 and m.text.split()[0].isdigit())
def roulette_bet(message):
    uid = str(message.from_user.id); parts = message.text.split()
    try: bet = int(parts[0])
    except: return
    targets = parts[1:]
    valid_targets = []
    for t in targets:
        t_clean = t.lower().strip()
        if t_clean in ["red", "к", "кр", "красное", "black", "ч", "черное", "green", "з", "зеленое", "even", "чет", "odd", "нечет"]:
            valid_targets.append(t_clean)
        elif t_clean.isdigit():
            num = int(t_clean)
            if 0 <= num <= 36: valid_targets.append(str(num))
            else: return
        elif "-" in t_clean:
            try:
                s, e = map(int, t_clean.split("-"))
                if 0 <= s <= 36 and 0 <= e <= 36 and s <= e: valid_targets.append(t_clean)
                else: return
            except: return
        else: return
    if not valid_targets: return
    if bet <= 0 or get_balance(uid) < bet * len(valid_targets): return bot.reply_to(message, f"❌ Недостаточно средств (нужно {bet * len(valid_targets)} 🪙).")
    if "bets" not in data["roulette_bets"]: data["roulette_bets"] = {}
    if str(message.chat.id) not in data["roulette_bets"]: data["roulette_bets"][str(message.chat.id)] = []
    data["roulette_bets"][str(message.chat.id)].append({"user": uid, "bet": bet, "targets": valid_targets})
    add_balance(uid, -bet * len(valid_targets)); save_data()
    bot.reply_to(message, f"✅ Ставка {bet} на {', '.join(valid_targets)} принята!\nНапишите `го` для запуска.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "го")
def roulette_go(message):
    chat_id = str(message.chat.id)
    if chat_id not in data["roulette_bets"] or not data["roulette_bets"][chat_id]: return bot.reply_to(message, "❌ Нет ставок.")
    bets = data["roulette_bets"][chat_id]; data["roulette_bets"][chat_id] = []; save_data()
    win_num = random.randint(0, 36)
    wc = "green" if win_num == 0 else ("red" if 1 <= win_num <= 18 else "black")
    sym = {"green": "🟢", "red": "🔴", "black": "⚫️"}[wc]
    total_win = 0; results = []
    for b in bets:
        u_win = 0
        for x in b["targets"]:
            win, mlt = False, 0
            if "-" in x:
                try: s, e = map(int, x.split("-")); mlt = max(1, math.floor(37 / (e - s + 1))) if s <= win_num <= e else 0
                except: pass
            elif x.isdigit():
                if int(x) == win_num: win, mlt = True, 36
            elif x in ["red", "к", "кр", "красное"]:
                if wc == "red": win, mlt = True, 2
            elif x in ["black", "ч", "черное"]:
                if wc == "black": win, mlt = True, 2
            elif x in ["green", "з", "зеленое"]:
                if wc == "green": win, mlt = True, 36
            elif x in ["even", "чет"]:
                if win_num != 0 and win_num % 2 == 0: win, mlt = True, 2
            elif x in ["odd", "нечет"]:
                if win_num % 2 != 0: win, mlt = True, 2
            if win: u_win += b["bet"] * mlt
        if u_win > 0: 
            u_win = int(u_win * get_win_multiplier(b["user"])); add_balance(b["user"], u_win); results.append(f"✅ {get_nick(b['user'])} +{u_win}")
        else: results.append(f"❌ {get_nick(b['user'])} -{b['bet'] * len(b['targets'])}")
        total_win += u_win
    res_text = "\n".join(results)
    bot.reply_to(message, f"🎰 Выпало: {sym} **{win_num}**\n\n{res_text}\n\n💰 Общий выигрыш: {total_win} 🪙", parse_mode="Markdown")

# ==================== БИЗНЕС, БАНК, КВЕСТЫ ====================

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "мой бизнес")
def my_business(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {}); t = p.get("biz_territory", 0)
    if t == 0:
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🏗 Построить бизнес", callback_data="biz_build"))
        return bot.reply_to(message, f"🏢 У вас нет бизнеса.\nНажмите кнопку ниже, чтобы купить первый уровень за {BIZ_TERRITORY_COST[0]} 🪙.", reply_markup=kb)
    show_biz_menu(message.chat.id, uid, None)

def show_biz_menu(chat_id, uid, msg_id):
    p = data["players"].get(uid, {}); t = p.get("biz_territory", 0); b = p.get("biz_building", 1); tax = p.get("biz_tax", 0)
    inc = (t * 100) * (1 + (b - 1) * 0.5)
    earned = int((min(int(time.time()) - p.get("biz_last_collect", int(time.time())), 86400) / 3600) * inc)
    text = f"🏢 **Бизнес**\n🧱 Территория: {t} м² (⬆️ {BIZ_TERRITORY_COST.get(t, 'MAX')} 🪙)\n🏢 Здание: ур. {b} (⬆️ {BIZ_BUILDING_COST.get(b, 'MAX')} 🪙)\n📈 Доход: {int(inc)} 🪙/час\n💸 Налоги: {tax} 🪙\n💰 Накоплено: **{earned} 🪙**"
    kb = InlineKeyboardMarkup(row_width=2).add(InlineKeyboardButton("💰 Собрать", callback_data="biz_collect"), InlineKeyboardButton("🧱 Терр.", callback_data="biz_terr"), InlineKeyboardButton("🏢 Здание", callback_data="biz_build_up"), InlineKeyboardButton("💸 Налоги", callback_data="biz_tax"))
    if msg_id: bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb, parse_mode="Markdown")
    else: bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("biz_"))
def biz_callback(call):
    uid = str(call.from_user.id); act = call.data.split("_")[1]
    if act == "build":
        if get_balance(uid) < BIZ_TERRITORY_COST[0]: return bot.answer_callback_query(call.id, "❌ Мало денег!", show_alert=True)
        add_balance(uid, -BIZ_TERRITORY_COST[0]); data["players"][uid]["biz_territory"] = 1; save_data()
        bot.answer_callback_query(call.id, "✅ Построено!"); show_biz_menu(call.message.chat.id, uid, call.message.message_id)
    elif act == "collect":
        p = data["players"].get(uid, {}); t = p.get("biz_territory", 0); b = p.get("biz_building", 1); tax = p.get("biz_tax", 0)
        if tax > 0: return bot.answer_callback_query(call.id, f"❌ Оплатите налоги ({tax} 🪙)!", show_alert=True)
        inc = (t * 100) * (1 + (b - 1) * 0.5)
        earned = int((min(int(time.time()) - p.get("biz_last_collect", int(time.time())), 86400) / 3600) * inc)
        if earned <= 0: return bot.answer_callback_query(call.id, "⏳ Пусто.", show_alert=True)
        tax_o = int(inc * 0.1)
        data["players"][uid]["biz_tax"] = tax_o; add_balance(uid, earned); data["players"][uid]["biz_last_collect"] = int(time.time()); save_data()
        bot.answer_callback_query(call.id, f"💰 +{earned} 🪙! Налог: {tax_o} 🪙", show_alert=True); show_biz_menu(call.message.chat.id, uid, call.message.message_id)
    elif act == "terr":
        p = data["players"].get(uid, {}); t = p.get("biz_territory", 0)
        if t >= 5: return bot.answer_callback_query(call.id, "🧱 MAX!", show_alert=True)
        c = BIZ_TERRITORY_COST.get(t, 0)
        if get_balance(uid) < c: return bot.answer_callback_query(call.id, f"❌ {c} 🪙!", show_alert=True)
        add_balance(uid, -c); data["players"][uid]["biz_territory"] += 1; save_data()
        bot.answer_callback_query(call.id, "✅ Территория +!"); show_biz_menu(call.message.chat.id, uid, call.message.message_id)
    elif act == "build_up":
        p = data["players"].get(uid, {}); b = p.get("biz_building", 1)
        if b >= 5: return bot.answer_callback_query(call.id, "🏢 MAX!", show_alert=True)
        c = BIZ_BUILDING_COST.get(b, 0)
        if get_balance(uid) < c: return bot.answer_callback_query(call.id, f"❌ {c} 🪙!", show_alert=True)
        add_balance(uid, -c); data["players"][uid]["biz_building"] += 1; save_data()
        bot.answer_callback_query(call.id, "✅ Здание +!"); show_biz_menu(call.message.chat.id, uid, call.message.message_id)
    elif act == "tax":
        p = data["players"].get(uid, {}); tax = p.get("biz_tax", 0)
        if tax <= 0: return bot.answer_callback_query(call.id, "✅ Долгов нет.", show_alert=True)
        if get_balance(uid) < tax: return bot.answer_callback_query(call.id, f"❌ {tax} 🪙!", show_alert=True)
        add_balance(uid, -tax); data["players"][uid]["biz_tax"] = 0; save_data()
        bot.answer_callback_query(call.id, "✅ Оплачено!"); show_biz_menu(call.message.chat.id, uid, call.message.message_id)

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "банк")
def bank_info(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {}); b = p.get("bank", 0); d = p.get("deposit", 0); ds = p.get("deposit_start", 0); now = int(time.time()); cd = 3 * 86400
    st = "Нет" if d == 0 else (f"✅ {d} 🪙 (Можно снять)" if now - ds >= cd else f"⏳ {d} 🪙 ({(cd - (now - ds))//3600} ч.)")
    msg = f"🏦 **БАНК**\n💰 Счет: {b} 🪙\n💎 Депозит: {st}\n📈 10% (раз в 3 дня)\n\n`банк положить СУММА` / `банк снять СУММА`\n`депозит положить СУММА` / `депозит снять`"
    bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("банк положить"))
def bank_dep(message):
    uid = str(message.from_user.id)
    try:
        a = int(message.text.split()[2])
        if a <= 0 or get_balance(uid) < a: return bot.reply_to(message, "❌ Мало.")
        add_balance(uid, -a); data["players"][uid]["bank"] += a; save_data(); bot.reply_to(message, f"✅ +{a} 🪙 в банк.")
    except: bot.reply_to(message, "❌ `банк положить 1000`")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("банк снять"))
def bank_wd(message):
    uid = str(message.from_user.id)
    try:
        a = int(message.text.split()[2])
        if a <= 0 or data["players"][uid].get("bank", 0) < a: return bot.reply_to(message, "❌ Мало в банке.")
        data["players"][uid]["bank"] -= a; add_balance(uid, a); save_data(); bot.reply_to(message, f"✅ -{a} 🪙 из банка.")
    except: bot.reply_to(message, "❌ `банк снять 1000`")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith("депозит положить"))
def dep_make(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {})
    try:
        a = int(message.text.split()[2])
        if a <= 0 or get_balance(uid) < a: return bot.reply_to(message, "❌ Мало.")
        if p.get("deposit", 0) > 0 and (int(time.time()) - p.get("deposit_start", 0) < 3 * 86400): return bot.reply_to(message, "❌ Уже есть депозит.")
        add_balance(uid, -a); data["players"][uid]["deposit"] = a; data["players"][uid]["deposit_start"] = int(time.time()); save_data()
        bot.reply_to(message, f"✅ Депозит {a} 🪙 открыт."); complete_quest(uid, "Открыть депозит")
    except: bot.reply_to(message, "❌ `депозит положить 1000`")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "депозит снять")
def dep_wd(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {}); d = p.get("deposit", 0); ds = p.get("deposit_start", 0)
    if d <= 0: return bot.reply_to(message, "❌ Нет депозита.")
    if int(time.time()) - ds < 3 * 86400: return bot.reply_to(message, f"⏳ Ждать {(3 * 86400 - (int(time.time()) - ds))//3600} ч.")
    i = int(d * 0.1); add_balance(uid, d + i); data["players"][uid]["deposit"] = 0; data["players"][uid]["deposit_start"] = 0; save_data()
    bot.reply_to(message, f"🏦 **Депозит закрыт!**\nВклад: {d} 🪙\nПроценты: +{i} 🪙\nИтого: **{d + i} 🪙**", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and (m.text.lower().startswith("перевод") or m.text.lower().startswith("передать")))
def transfer(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    parts = message.text.split()
    if message.reply_to_message:
        try: a = int(parts[1])
        except: return bot.reply_to(message, "❌ `передать 1000` — в ответ на сообщение игрока", parse_mode="Markdown")
        t_uid = str(message.reply_to_message.from_user.id)
        if message.reply_to_message.from_user.is_bot: return bot.reply_to(message, "❌ Боту переводить нельзя.")
    else:
        try: a = int(parts[1]); t_raw = parts[2]
        except: return transfer_hint(message)
        if t_raw.isdigit(): t_uid = get_user_by_short_id(t_raw)
        else: t_uid = get_user_by_username(t_raw)
        if not t_uid: return bot.reply_to(message, "❌ Игрок не найден.")
    if a <= 0 or get_balance(uid) < a: return bot.reply_to(message, "❌ Мало.")
    if a > 10000: return bot.reply_to(message, "❌ Лимит перевода — 10000 🪙.")
    if t_uid == uid: return bot.reply_to(message, "❌ Себе нельзя.")
    if t_uid not in data["players"]: register_user_by_id(t_uid)
    add_balance(uid, -a); add_balance(t_uid, a)
    bot.reply_to(message, f"✅ **ПЕРЕДАЧА**\n━━━━━━━━━━━━━━━━━━━━\n{a} 🪙 → **{get_nick(t_uid)}**", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "квесты")
def quests_menu(message):
    uid = str(message.from_user.id); p = data["players"].get(uid, {}); now = int(time.time())
    if "quest_date" not in p or now - p.get("quest_date", 0) >= 86400:
        data["players"][uid]["quests"] = {
            "Сделать ставку в слот": {"done": False, "reward": 500, "claimed": False}, 
            "Сыграть в краш": {"done": False, "reward": 500, "claimed": False}, 
            "Открыть депозит": {"done": False, "reward": 500, "claimed": False}
        }
        data["players"][uid]["quest_date"] = now; save_data()
    q = p.get("quests", {}); text = "📜 **Ежедневные квесты**\n\n"
    for name, info in q.items(): 
        status = "✅" if info.get("done") else "❌"
        text += f"{status} {name} (+{info['reward']} 🪙)\n"
    text += "\nНапишите `забрать квест` после выполнения."
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "забрать квест")
def claim_quests(message):
    uid = str(message.from_user.id); q = data["players"].get(uid, {}).get("quests", {}); total = 0
    for name, info in q.items():
        if info.get("done") and not info.get("claimed", False): total += info["reward"]; info["claimed"] = True
    if total > 0: 
        add_balance(uid, total); save_data()
        bot.reply_to(message, f"🎉 Вы получили {total} 🪙 за квесты!", parse_mode="Markdown")
    else: bot.reply_to(message, "❌ Нет выполненных или незабранных квестов.")

def complete_quest(uid, quest_name):
    uid = str(uid); p = data["players"].get(uid, {})
    if "quests" in p and quest_name in p["quests"]:
        if not p["quests"][quest_name]["done"]: p["quests"][quest_name]["done"] = True; save_data()


# ==================== ГЛАВНОЕ МЕНЮ (нижние кнопки) ====================
from types import SimpleNamespace

MENU_BTNS = ["🎮 Игры", "🎟 Промо", "🛡 Клан", "👤 Профиль", "🏆 Топ", "📖 Помощь", "🎁 Бонус", "💼 Бизнес", "🏦 Банк", "💸 Передать", "⚙️ Админ"]
STAKES = [100, 1000, 5000, 10000]
GAME_NAMES = {"slots": "🎰 **СЛОТЫ**", "mines": "💣 **МИНЫ**", "crash": "🚀 **КРАШ**", "joker": "🃏 **ДЖОКЕР**"}
GAME_HINTS = {"mines": "\n💣 По кнопке играется с 5 минами. Текстом можно задать число мин: `1500 3` (3, 5 или 9)"}

def main_menu_kb(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="Выберите раздел 👇")
    kb.row(KeyboardButton("🎮 Игры"), KeyboardButton("🎟 Промо"))
    kb.row(KeyboardButton("🛡 Клан"), KeyboardButton("👤 Профиль"))
    kb.row(KeyboardButton("🏆 Топ"), KeyboardButton("📖 Помощь"))
    kb.row(KeyboardButton("🎁 Бонус"), KeyboardButton("💼 Бизнес"))
    kb.row(KeyboardButton("🏦 Банк"), KeyboardButton("💸 Передать"))
    if WEBAPP_URL:
        kb.row(KeyboardButton("⚓ Корабли (игра)", web_app=WebAppInfo(url=WEBAPP_URL)))
    if is_admin: kb.row(KeyboardButton("⚙️ Админ"))
    return kb

def shim_text(message, text):
    return SimpleNamespace(text=text, from_user=message.from_user, chat=message.chat, message_id=message.message_id, reply_to_message=None)

def shim_call(call, text):
    return SimpleNamespace(text=text, from_user=call.from_user, chat=call.message.chat, message_id=call.message.message_id, reply_to_message=None)

def route_or_cancel(message):
    """Во время ввода суммы: кнопка меню — обработать её, команда — отменить ввод."""
    t = (message.text or "").strip()
    if t in MENU_BTNS:
        menu_router(message); return True
    if t == "меню":
        menu_panel(message); return True
    if t.startswith("/"): return True
    return False

@bot.message_handler(commands=["menu"])
def menu_cmd(message):
    if message.chat.type == "private":
        bot.send_message(message.chat.id, "📋 Меню открыто — кнопки внизу экрана 👇", reply_markup=main_menu_kb(message.from_user.id in ADMIN_IDS))
    else:
        bot.send_message(message.chat.id, "📋 **МЕНЮ SAILIX CASINO**\n━━━━━━━━━━━━━━━━━━━━\nНажмите кнопку:", reply_markup=group_menu_kb(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.strip() in MENU_BTNS)
def menu_router(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    t = message.text.strip()
    if t == "🎮 Игры": send_games_menu(message.chat.id)
    elif t == "🎟 Промо":
        if message.chat.type == "private":
            bot.clear_step_handler_by_chat_id(message.chat.id)
            bot.send_message(message.chat.id, "🎟 **ПРОМОКОД**\n━━━━━━━━━━━━━━━━━━━━\nВведите код:\n\n(«отмена» — выйти из ввода)", parse_mode="Markdown")
            bot.register_next_step_handler_by_chat_id(message.chat.id, promo_code_step)
        else:
            bot.reply_to(message, "🎟 Напишите в чате: `промо КОД`", parse_mode="Markdown")
    elif t == "👤 Профиль": profile_command(message)
    elif t == "🏆 Топ": top_cmd(message)
    elif t == "📖 Помощь": help_command(message)
    elif t == "🛡 Клан": send_clan_menu(message.chat.id)
    elif t == "🎁 Бонус": daily_bonus(message)
    elif t == "💼 Бизнес": my_business(message)
    elif t == "🏦 Банк": bank_info(message)
    elif t == "💸 Передать": transfer_hint(message)
    elif t == "⚙️ Админ":
        if message.from_user.id in ADMIN_IDS: admin_panel(message)

# ---------- Игровой зал: выбор игры и ставки ----------

def games_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🎰 Слоты", callback_data="gm_slots"), InlineKeyboardButton("💣 Мины", callback_data="gm_mines"))
    kb.add(InlineKeyboardButton("🚀 Краш", callback_data="gm_crash"), InlineKeyboardButton("🃏 Джокер", callback_data="gm_joker"))
    kb.add(InlineKeyboardButton("🎯 Рулетка", callback_data="gm_roulette"), InlineKeyboardButton("⚔️ Дуэль", callback_data="gm_duel"))
    if WEBAPP_URL:
        kb.add(InlineKeyboardButton("⚓ Корабли (мини-приложение)", web_app=WebAppInfo(url=WEBAPP_URL)))
    return kb

GAMES_MENU_TEXT = ("🎮 **ИГРОВОЙ ЗАЛ**\n━━━━━━━━━━━━━━━━━━━━\n"
                   "Выберите игру, затем ставку кнопкой\n— или напишите свою сумму в чат ✏️")

def send_games_menu(chat_id):
    bot.send_message(chat_id, GAMES_MENU_TEXT, reply_markup=games_menu_kb(), parse_mode="Markdown")

def stake_kb(game):
    kb = InlineKeyboardMarkup(row_width=2)
    for s in STAKES: kb.add(InlineKeyboardButton(f"{s} 🪙", callback_data=f"gs_{game}_{s}"))
    kb.add(InlineKeyboardButton("⬅️ К играм", callback_data="g_back"))
    return kb

@bot.callback_query_handler(func=lambda call: call.data.startswith("gm_"))
def games_menu_cb(call):
    game = call.data[3:]; chat_id = call.message.chat.id
    if game == "duel":
        bot.answer_callback_query(call.id)
        return bot.send_message(chat_id,
            "⚔️ **ДУЭЛЬ**\n━━━━━━━━━━━━━━━━━━━━\n"
            "Вызовите соперника:\n• ответьте на его сообщение: `дуэль 1000`\n• или укажите ID: `дуэль 15 1000`\n\n"
            "Победитель забирает обе ставки 🏆", parse_mode="Markdown")
    if game == "roulette":
        bot.answer_callback_query(call.id)
        return bot.send_message(chat_id,
            "🎯 **РУЛЕТКА**\n━━━━━━━━━━━━━━━━━━━━\n"
            "Ставка текстом: `1000 красное`, `1000 черное`, `1000 зеленое`\n"
            "Числа и диапазоны: `500 12`, `500 5-20`, `500 чет нечет`\n"
            "Запуск раунда: `го`", parse_mode="Markdown")
    hint = GAME_HINTS.get(game, "")
    bot.send_message(chat_id,
        f"{GAME_NAMES[game]}\nВыберите ставку кнопкой или напишите свою сумму в чат ✏️\n(минимум 100 🪙){hint}",
        reply_markup=stake_kb(game), parse_mode="Markdown")
    bot.answer_callback_query(call.id)
    bot.clear_step_handler_by_chat_id(chat_id)
    bot.register_next_step_handler_by_chat_id(chat_id, custom_bet_step, game, str(call.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("gs_"))
def stake_pick(call):
    _, game, s = call.data.split("_")
    chat_id = call.message.chat.id
    bot.clear_step_handler_by_chat_id(chat_id)
    bot.answer_callback_query(call.id, "🎮 Запускаем!")
    place_bet(SimpleNamespace(from_user=call.from_user, chat=call.message.chat, message_id=call.message.message_id, reply_to_message=None), game, int(s), 5)

@bot.callback_query_handler(func=lambda call: call.data == "g_back")
def stake_back(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(GAMES_MENU_TEXT, call.message.chat.id, call.message.message_id, reply_markup=games_menu_kb(), parse_mode="Markdown")
    except Exception:
        send_games_menu(call.message.chat.id)

def custom_bet_step(message, game, owner_uid):
    if str(message.from_user.id) != owner_uid:
        bot.register_next_step_handler_by_chat_id(message.chat.id, custom_bet_step, game, owner_uid)
        return
    if route_or_cancel(message): return
    parts = (message.text or "").strip().split()
    try: amount = int(parts[0])
    except (ValueError, IndexError):
        return bot.reply_to(message, "✏️ Введите целое число, например `2500`.", parse_mode="Markdown")
    bombs = 5
    if game == "mines" and len(parts) > 1:
        try:
            if int(parts[1]) in MULT: bombs = int(parts[1])
        except ValueError: pass
    place_bet(message, game, amount, bombs)

def place_bet(base, game, amount, bombs=5):
    uid = str(base.from_user.id)
    if uid not in data["players"]: register_user_by_id(uid)
    if amount < 100: return bot.send_message(base.chat.id, "❌ Минимальная ставка — 100 🪙.")
    if amount > get_balance(uid):
        return bot.send_message(base.chat.id, f"❌ Недостаточно средств. Баланс: {get_balance(uid)} 🪙")
    cmds = {"slots": f"слот {amount}", "mines": f"мины {amount} {bombs}", "crash": f"краш {amount}", "joker": f"джокер {amount}"}
    handlers = {"slots": slot_machine, "mines": start_mines, "crash": start_crash, "joker": start_joker}
    handlers[game](SimpleNamespace(text=cmds[game], from_user=base.from_user, chat=base.chat, message_id=base.message_id, reply_to_message=None))

# ---------- Клановый центр ----------

def clan_menu_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("🛡 Мой клан", callback_data="clm_info"))
    kb.add(InlineKeyboardButton("➕ Создать клан (10 000 🪙)", callback_data="clm_create"))
    kb.add(InlineKeyboardButton("🔑 Вступить в клан по ID", callback_data="clm_join"))
    kb.add(InlineKeyboardButton("💰 Казна: положить/снять", callback_data="clm_treasury"))
    kb.add(InlineKeyboardButton("🚪 Выйти из клана", callback_data="clm_leave"))
    kb.add(InlineKeyboardButton("🏆 Топ кланов", callback_data="clm_top"))
    return kb

def send_clan_menu(chat_id):
    bot.send_message(chat_id, "🛡 **КЛАНОВЫЙ ЦЕНТР**\n━━━━━━━━━━━━━━━━━━━━\nВыберите действие:", reply_markup=clan_menu_kb(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("clm_"))
def clan_menu_cb(call):
    act = call.data[4:]; chat_id = call.message.chat.id; uid = str(call.from_user.id)
    if uid not in data["players"]: register_user_by_id(uid)
    if act == "info":
        bot.answer_callback_query(call.id); my_clan_info(shim_call(call, "мой клан"))
    elif act == "leave":
        bot.answer_callback_query(call.id); leave_clan(shim_call(call, "выйти из клана"))
    elif act == "top":
        bot.answer_callback_query(call.id); top_clans(shim_call(call, "топ кланов"))
    elif act == "create":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "✏️ Введите название клана (3–20 символов):")
        bot.clear_step_handler_by_chat_id(chat_id)
        bot.register_next_step_handler_by_chat_id(chat_id, clan_create_step)
    elif act == "join":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "✏️ Введите ID клана:")
        bot.clear_step_handler_by_chat_id(chat_id)
        bot.register_next_step_handler_by_chat_id(chat_id, clan_join_step)
    elif act == "treasury":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "✏️ Введите действие и сумму, например:\n`положить 1000` или `снять 1000`", parse_mode="Markdown")
        bot.clear_step_handler_by_chat_id(chat_id)
        bot.register_next_step_handler_by_chat_id(chat_id, clan_treasury_step)

# ---------- Тихий обработчик кнопок проигранного поля мин ----------

@bot.callback_query_handler(func=lambda call: call.data == "ignore")
def ignore_cb(call):
    bot.answer_callback_query(call.id)


# ==================== МЕНЮ ДЛЯ ГРУППОВОГО ЧАТА ====================

def transfer_hint(message):
    bot.reply_to(message,
        "💸 **ПЕРЕДАЧА МОНЕТ**\n━━━━━━━━━━━━━━━━━━━━\n"
        "В чате: ответьте на сообщение игрока и напишите `передать 1000`\n"
        "Или: `перевод 1000 ник` / `передать 1000 ID`\n"
        "Лимит: 10 000 🪙 за перевод", parse_mode="Markdown")

def group_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("💰 Баланс", callback_data="gmenu_balance"), InlineKeyboardButton("👤 Профиль", callback_data="gmenu_profile"))
    kb.add(InlineKeyboardButton("🎁 Бонус", callback_data="gmenu_bonus"), InlineKeyboardButton("🏆 Топ", callback_data="gmenu_top"))
    kb.add(InlineKeyboardButton("🎮 Игры", callback_data="gmenu_games"), InlineKeyboardButton("🛡 Клан", callback_data="gmenu_clan"))
    kb.add(InlineKeyboardButton("💼 Бизнес", callback_data="gmenu_biz"), InlineKeyboardButton("📖 Помощь", callback_data="gmenu_help"))
    kb.add(InlineKeyboardButton("🏦 Банк", callback_data="gmenu_bank"), InlineKeyboardButton("💸 Передать", callback_data="gmenu_transfer"))
    return kb

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "меню")
def menu_panel(message):
    bot.send_message(message.chat.id,
        "📋 **МЕНЮ SAILIX CASINO**\n━━━━━━━━━━━━━━━━━━━━\nНажмите кнопку:",
        reply_markup=group_menu_kb(), parse_mode="Markdown")

GMENU_HANDLERS = {
    "balance": ("б", balance_command),
    "profile": ("профиль", profile_command),
    "bonus": ("бонус", daily_bonus),
    "top": ("топ", top_cmd),
    "help": ("помощь", help_command),
    "biz": ("мой бизнес", my_business),
    "bank": ("банк", bank_info),
}

@bot.callback_query_handler(func=lambda call: call.data.startswith("gmenu_"))
def group_menu_cb(call):
    act = call.data[6:]; chat_id = call.message.chat.id
    if act in GMENU_HANDLERS:
        bot.answer_callback_query(call.id)
        text, fn = GMENU_HANDLERS[act]
        fn(shim_call(call, text))
    elif act == "games":
        bot.answer_callback_query(call.id); send_games_menu(chat_id)
    elif act == "clan":
        bot.answer_callback_query(call.id); send_clan_menu(chat_id)
    elif act == "transfer":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "💸 **ПЕРЕДАЧА МОНЕТ**\n━━━━━━━━━━━━━━━━━━━━\nОтветьте на сообщение игрока и напишите:\n`передать 1000`\n\nИли текстом: `перевод 1000 ник`", parse_mode="Markdown")

# ==================== ПРОМО-ВВОД С КНОПКИ ====================

def promo_code_step(message):
    if route_or_cancel(message): return
    t = (message.text or "").strip()
    if t.lower() in ("отмена", "cancel"):
        return bot.send_message(message.chat.id, "❌ Ввод промокода отменён.")
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    bot.send_message(message.chat.id, activate_promo_code(uid, t), parse_mode="Markdown")

# ==================== РЕФЕРАЛКА ====================

_bot_uname = {"v": None}
def bot_username():
    if not _bot_uname["v"]:
        try: _bot_uname["v"] = bot.get_me().username
        except Exception: _bot_uname["v"] = ""
    return _bot_uname["v"]

def ref_count(uid):
    return sum(1 for p in data["players"].values() if p.get("referrer") == uid)

def ref_list(uid):
    return [p.get("username", "?") for p in data["players"].values() if p.get("referrer") == uid][:10]

def send_ref_panel(chat_id, uid):
    cnt = ref_count(uid)
    link = f"https://t.me/{bot_username()}?start=ref_{uid}"
    names = ref_list(uid)
    lst = "\n".join([f"• {n}" for n in names]) if names else "Пока никого 😔"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔗 Отправить ссылку другу", url=link))
    text = (f"👥 **РЕФЕРАЛЬНАЯ ПРОГРАММА**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁 Пригласи друга: тебе **+{REF_BONUS_INVITER} 🪙**, другу **+{REF_BONUS_NEW} 🪙**\n"
            f"Бонус начисляется, когда друг запустит бота по твоей ссылке\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 Твоя ссылка (вечная):\n{link}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Приглашено: **{cnt}**\n{lst}")
    bot.send_message(chat_id, text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data == "ref_panel")
def ref_panel_cb(call):
    bot.answer_callback_query(call.id)
    send_ref_panel(call.message.chat.id, str(call.from_user.id))

@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == "рефералка")
def ref_cmd(message):
    uid = str(message.from_user.id)
    if uid not in data["players"]: register_user(message.from_user)
    send_ref_panel(message.chat.id, uid)

# ==================== ШАГИ ВВОДА КЛАНОВ ====================

def clan_create_step(message):
    if route_or_cancel(message): return
    name = (message.text or "").strip()
    if len(name) < 3 or len(name) > 20:
        return bot.reply_to(message, "❌ Название от 3 до 20 символов. Введите ещё раз:")
    create_clan(shim_text(message, f"создать клан {name}"))

def clan_join_step(message):
    if route_or_cancel(message): return
    sid = (message.text or "").strip().split()[0]
    join_clan(shim_text(message, f"вступить клан {sid}"))

def clan_treasury_step(message):
    if route_or_cancel(message): return
    t = (message.text or "").strip()
    clan_treasury(shim_text(message, f"клан казна {t}"))



# ==================== МОРСКОЙ БОЙ (Mini App PvP) ====================

SB_FLEET = [4, 3, 3, 2, 2, 2, 1, 1, 1, 1]  # 1 линкор, 2 крейсера, 3 эсминца, 4 катера
SB_SIZE = 10

if "sb_chats" not in data: data["sb_chats"] = {}
if "sb_challenges" not in data: data["sb_challenges"] = {}
if "sb_games" not in data: data["sb_games"] = {}

def sb_place_ship(ships_list, grid, cells):
    """cells: список (r,c). Возвращает строку ошибки или None."""
    for r, c in cells:
        if not (0 <= r < SB_SIZE and 0 <= c < SB_SIZE): return "Корабль выходит за границы поля."
    for r, c in cells:
        if (r, c) in grid: return "Здесь уже стоит другой корабль."
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (r + dr, c + dc) in grid: return "Корабли не могут соприкасаться даже углами — отодвиньте на клетку."
    idx = len(ships_list)
    ships_list.append({"cells": [[r, c] for r, c in cells], "alive": True})
    for r, c in cells: grid[(r, c)] = idx
    return None

def sb_random_fleet():
    ships, grid = [], {}
    for size in SB_FLEET:
        for _ in range(300):
            horiz = random.random() < 0.5
            r = random.randint(0, SB_SIZE - (1 if horiz else size))
            c = random.randint(0, SB_SIZE - (size if horiz else 1))
            cells = [(r, c + i) if horiz else (r + i, c) for i in range(size)]
            if sb_place_ship(ships, grid, cells) is None: break
    return {"ships": ships, "grid": grid}

def sb_fleet_alive(fleet):
    return any(s["alive"] for s in fleet)

def sb_fire(game, shooter, r, c):
    """Выстрел shooter по полю соперника. Возвращает (result, state_changed)."""
    enemy_uid = game["players"][0] if game["players"][1] == shooter else game["players"][1]
    enemy_fleet = game["fleets"][enemy_uid]
    grid = enemy_fleet["grid"]
    key = f"{r},{c}"
    if not (0 <= r < SB_SIZE and 0 <= c < SB_SIZE): return "wrong", None
    if key in game["shots"][shooter]: return "repeat", None
    game["shots"][shooter].add(key)
    si = grid.get((r, c))
    if si is None: return "miss", None
    ship = enemy_fleet["ships"][si]
    ship["hits"] = ship.get("hits", 0) + 1
    result = "hit"
    if ship["hits"] >= len(ship["cells"]):
        ship["alive"] = False
        result = "sunk"
        if not sb_fleet_alive(enemy_fleet):
            game["status"] = "done"
            game["winner"] = shooter
    return result, si

def sb_state_for(game, uid):
    """Состояние партии с точки зрения игрока uid."""
    enemy_uid = game["players"][0] if game["players"][1] == uid else game["players"][1]
    me, en = game["fleets"][uid], game["fleets"][enemy_uid]
    def board(fleet, shots):
        b = [[0]*SB_SIZE for _ in range(SB_SIZE)]  # 0 пусто, 1 промах, 2 попадание, 3 убит, 4 корабль
        for si, ship in enumerate(fleet["ships"]):
            if ship.get("hits", 0) >= len(ship["cells"]):
                for r, c in ship["cells"]: b[r][c] = 3
            elif ship["alive"]:
                for r, c in ship["cells"]:
                    if f"{r},{c}" in shots: b[r][c] = 2
                # нераненые клетки корабля не показываем на чужом поле
        for key in shots:
            r, c = map(int, key.split(","))
            if b[r][c] == 0: b[r][c] = 1
        return b
    own = board(me, game["shots"][enemy_uid])
    enemy = board(en, game["shots"][uid])
    # на своём поле показываем целые корабли
    for si, ship in enumerate(me["ships"]):
        if ship["alive"] and ship.get("hits", 0) < len(ship["cells"]):
            for r, c in ship["cells"]:
                if own[r][c] == 0: own[r][c] = 4
    # на чужом поле показываем живые клетки убитых кораблей + попадания
    for si, ship in enumerate(en["ships"]):
        for r, c in ship["cells"]:
            if f"{r},{c}" in game["shots"][uid] and not (ship.get("hits",0) >= len(ship["cells"])):
                enemy[r][c] = 2
    return {"own": own, "enemy": enemy, "turn": game["turn"], "status": game["status"], "winner": game.get("winner")}

def sb_free_game_ids():
    for gid in list(data["sb_games"].keys()):
        g = data["sb_games"][gid]
        if g.get("status") == "done" and time.time() - g.get("finished", 0) > 600:
            del data["sb_games"][gid]

def validate_initdata(raw):
    """Проверяет подпись Telegram initData. Возвращает user_id или None."""
    try:
        if not raw: return None
        parsed = dict(pair.split("=", 1) for pair in raw.split("&"))
        h = parsed.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(secret, check.encode(), hashlib.sha256).hexdigest() != h: return None
        user = json.loads(parsed.get("user", "{}"))
        return user.get("id")
    except Exception:
        return None

def sb_pvp_reward(winner):
    add_balance(winner, 300)
    save_data()

# ==================== CALLBACK: ПРИНЯТЬ ВЫЗОВ ====================

@bot.callback_query_handler(func=lambda call: call.data.startswith("sba_"))
def sb_accept_call(call):
    cid = call.data[4:]
    ch = data["sb_challenges"].get(cid)
    if not ch or ch["status"] != "open":
        bot.answer_callback_query(call.id, "⌛ Вызов уже неактуален или принят.", show_alert=True)
        return
    uid = str(call.from_user.id)
    if uid == ch["from"]:
        bot.answer_callback_query(call.id, "Нельзя принять свой же вызов 🙂", show_alert=True)
        return
    for gid, g in data["sb_games"].items():
        if g["status"] == "active" and uid in g["players"]:
            bot.answer_callback_query(call.id, "У вас уже идёт бой. Завершите его.", show_alert=True)
            return
    register_user_by_id(uid)
    # создаём партию
    gid = f"{cid}"
    g = {
        "players": [ch["from"], uid],
        "fleets": {ch["from"]: sb_random_fleet(), uid: sb_random_fleet()},
        "shots": {ch["from"]: set(), uid: set()},
        "turn": random.choice([ch["from"], uid]),
        "status": "active",
        "winner": None,
        "created": time.time(),
        "chat": ch["chat"],
    }
    data["sb_games"][gid] = g
    del data["sb_challenges"][cid]
    save_data()
    name_a = get_nick(ch["from"]); name_b = get_nick(uid)
    try:
        bot.edit_message_text(
            f"⚔️ **Вызов принят!**\n━━━━━━━━━━━━━━━━━━━━\n{name_a} vs {name_b}\n🚢 Морской бой начался!",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception: pass
    kb = InlineKeyboardMarkup(row_width=1)
    if WEBAPP_URL:
        kb.add(InlineKeyboardButton("⚓ Открыть игру", web_app=WebAppInfo(url=WEBAPP_URL)))
    for p in [ch["from"], uid]:
        try:
            bot.send_message(int(p), f"⚓ **МОРСКОЙ БОЙ**\n━━━━━━━━━━━━━━━━━━━━\n🆚 Противник: {name_a if p == uid else name_b}\n🎯 Первый ход: {name_a if g['turn'] == ch['from'] else name_b}\n\nОткройте игру кнопкой ниже — поле уже расставлено автоматически.\nПобедитель получает **300 🪙**!", parse_mode="Markdown", reply_markup=kb)
        except Exception:
            try: bot.send_message(ch["chat"], f"⚠️ {name_a if p == uid else name_b}, напишите боту /start в личку, чтобы получить поле боя!", parse_mode="Markdown")
            except Exception: pass
    bot.answer_callback_query(call.id, "⚔️ Бой начался! Проверьте личные сообщения.")

# ==================== ЗАПОМИНАНИЕ ЧАТА + КОМАНДА ДУЭЛИ ====================

@bot.message_handler(commands=["duel"], chat_types=["group", "supergroup"])
def sb_duel_group(message):
    data["sb_chats"][str(message.from_user.id)] = message.chat.id
    save_data()
    bot.reply_to(message,
        "⚔️ **Вызовы на морской бой**\n━━━━━━━━━━━━━━━━━━━━\nОткройте мини-приложение (кнопка ⚓ Корабли у поля ввода), выберите «С друзьями» и нажмите «📢 Кинуть вызов» — вызов придёт в этот чат с зелёной кнопкой.",
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.chat.type in ("group", "supergroup") and m.text and m.text.lower().strip() == "вызов")
def sb_challenge_word(message):
    data["sb_chats"][str(message.from_user.id)] = message.chat.id
    save_data()
    bot.reply_to(message,
        "⚔️ Чат запомнен! Откройте мини-приложение (⚓ Корабли) → «С друзьями» → «📢 Кинуть вызов».",
        parse_mode="Markdown")

# ==================== API-СЕРВЕР ДЛЯ MINI APP ====================

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

class SBAPIHandler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self._json({"ok": True})

    def do_GET(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        if p.path == "/api/ping":
            return self._json({"ok": True, "pong": True})
        uid = validate_initdata((q.get("initData") or [""])[0])
        if not uid:
            return self._json({"ok": False, "err": "auth"}, 401)
        uid = str(uid)
        if p.path == "/api/status":
            # активный вызов этого игрока или его партия
            for cid, ch in data["sb_challenges"].items():
                if ch["from"] == uid and ch["status"] == "open":
                    return self._json({"ok": True, "challenge": cid})
            for gid, g in data["sb_games"].items():
                if g["status"] == "active" and uid in g["players"]:
                    return self._json({"ok": True, "game": gid})
            return self._json({"ok": True})
        if p.path == "/api/state":
            gid = (q.get("gid") or [""])[0]
            g = data["sb_games"].get(gid)
            if not g or uid not in g["players"]:
                return self._json({"ok": False, "err": "no_game"}, 404)
            st = sb_state_for(g, uid)
            enemy = [pl for pl in g["players"] if pl != uid][0]
            st["enemy_name"] = get_nick(enemy)
            st["gid"] = gid
            if g["status"] == "done" and not g.get("rewarded"):
                g["rewarded"] = True
                sb_pvp_reward(g["winner"])
                save_data()
                try:
                    bot.send_message(int(g["chat"]), f"🏁 **МОРСКОЙ БОЙ ЗАВЕРШЁН**\n━━━━━━━━━━━━━━━━━━━━\n🏆 Победитель: **{get_nick(g['winner'])}**\n🎁 Приз: **300 🪙**", parse_mode="Markdown")
                except Exception: pass
            return self._json({"ok": True, "state": st})
        return self._json({"ok": False, "err": "unknown"}, 404)

    def do_POST(self):
        p = urlparse(self.path)
        ln = int(self.headers.get("Content-Length", 0) or 0)
        try: body = json.loads(self.rfile.read(ln) or b"{}")
        except Exception: body = {}
        uid = validate_initdata(body.get("initData", ""))
        if not uid:
            return self._json({"ok": False, "err": "auth"}, 401)
        uid = str(uid)
        if p.path == "/api/challenge":
            if uid not in data["players"]: register_user_by_id(uid)
            chat_id = data["sb_chats"].get(uid)
            if not chat_id:
                return self._json({"ok": False, "err": "no_chat"})
            # не больше одного открытого вызова
            now_ts = time.time()
            for key in list(data["sb_challenges"].keys()):
                ch2 = data["sb_challenges"][key]
                if ch2["status"] == "open" and now_ts - ch2.get("created", 0) > 600:
                    del data["sb_challenges"][key]
                    continue
                if ch2["from"] == uid and ch2["status"] == "open":
                    return self._json({"ok": True, "cid": ch2["cid"], "already": True})
            cid = f"{int(time.time())}{random.randint(100,999)}"
            data["sb_challenges"][cid] = {"cid": cid, "from": uid, "chat": chat_id, "status": "open", "created": time.time()}
            save_data()
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("🟢 Принять вызов", callback_data=f"sba_{cid}"))
            try:
                bot.send_message(chat_id, f"⚓ **ВЫЗОВ НА МОРСКОЙ БОЙ**\n━━━━━━━━━━━━━━━━━━━━\n👤 От: **{get_nick(uid)}**\n\nНажмите зелёную кнопку, чтобы принять бой!\n🎯 Победитель получает 300 🪙", parse_mode="Markdown", reply_markup=kb)
                return self._json({"ok": True, "cid": cid})
            except Exception as e:
                del data["sb_challenges"][cid]; save_data()
                return self._json({"ok": False, "err": "send_failed"})
        if p.path == "/api/pvp_move":
            gid = body.get("gid", "")
            g = data["sb_games"].get(gid)
            if not g or uid not in g["players"] or g["status"] != "active":
                return self._json({"ok": False, "err": "no_game"}, 404)
            if g["turn"] != uid:
                return self._json({"ok": False, "err": "not_turn"}, 409)
            try: r, c = int(body["r"]), int(body["c"])
            except Exception:
                return self._json({"ok": False, "err": "bad_move"}, 400)
            result, si = sb_fire(g, uid, r, c)
            if result in ("repeat", "wrong"):
                return self._json({"ok": False, "err": result}, 409)
            if g["status"] == "active":
                enemy_uid = [pl for pl in g["players"] if pl != uid][0]
                g["turn"] = enemy_uid
            else:
                g["finished"] = time.time()
                save_data()
            save_data()
            st = sb_state_for(g, uid)
            st["last"] = result
            return self._json({"ok": True, "state": st})
        if p.path == "/api/win":
            # победа над ботом в мини-приложении
            add_balance(uid, 300)
            save_data()
            return self._json({"ok": True, "reward": 300})
        return self._json({"ok": False, "err": "unknown"}, 404)

def sb_start_api():
    port = int(os.getenv("PORT", 8080))
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), SBAPIHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"SB API started on port {port}")
    except Exception as e:
        print("⚠️ SB API not started:", e)

sb_start_api()

print("BOT STARTED")
bot.infinity_polling(skip_pending=True)
