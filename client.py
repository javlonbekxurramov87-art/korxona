import telebot
import sqlite3
import os
import time
import threading
from telebot import types

# 1. BOT SOZLAMALARI
TOKEN = '6325207843:AAHJ8DeIEoxSIIc6iQJXbthIqfcm1tssxg0' 
bot = telebot.TeleBot(TOKEN)
ADMIN_IDS = [8384720661] 

# 2. YO'LLAR
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'db.sqlite3')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# 3. STATUSLAR
STATUS_MAP = {
    'KIRITILDI': "Navbatda ⏳",
    'TASDIQLANDI': "Tasdiqlandi ✅",
    'USTA_QABUL_QILDI': "Usta qabul qildi 👨‍🔧",
    'ISHDA': "Ishlab chiqarishda 🛠",
    'USTA_TUGATDI': "Tayyorlandi, admin tasdiqlashi kutilmoqda ⏳", 
    'TAYYOR': "Tayyor, buyurtmani olib ketishingiz mumkin! ✅",      
    'BAJARILDI': "Buyurtma topshirildi 🚚"
}

last_checked_order_id = {'max_id': 0}

# --- MONITORING: YANGI BUYURTMALAR ---
def init_last_order_id():
    global last_checked_order_id
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(id) FROM orders_order")
        max_id = cursor.fetchone()[0]
        last_checked_order_id['max_id'] = max_id if max_id else 0
        conn.close()
        print(f"📊 Monitor boshlandi. Oxirgi ID: {last_checked_order_id['max_id']}")
    except Exception as e:
        print(f"Init error: {e}")

def check_new_orders():
    while True:
        try:
            time.sleep(10)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, order_number, customer_name, status, created_at 
                FROM orders_order WHERE id > ? AND status = 'KIRITILDI'
            """, (last_checked_order_id['max_id'],))
            
            new_orders = cursor.fetchall()
            for order in new_orders:
                oid, onum, cname, status, date = order
                for admin_id in ADMIN_IDS:
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔍 Ko'rish", callback_data=f"view_admin_{oid}"))
                    msg = (f"🆕 **YANGI BUYURTMA!**\n\n🔢 **№:** `{onum}`\n👤 **Mijoz:** {cname}\n"
                           f"📊 **Holat:** {STATUS_MAP.get(status, status)}")
                    bot.send_message(admin_id, msg, parse_mode="Markdown", reply_markup=markup)
                
                last_checked_order_id['max_id'] = max(last_checked_order_id['max_id'], oid)
            conn.close()
        except Exception as e:
            print(f"New order check error: {e}")

# --- MONITORING: STATUS O'ZGARISHI ---
def check_order_status_updates():
    last_status_cache = {}
    while True:
        try:
            time.sleep(5)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, customer_chat_id, order_number, status FROM orders_order WHERE customer_chat_id IS NOT NULL")
            orders = cursor.fetchall()
            
            for oid, chat_id, onum, current_status in orders:
                if oid in last_status_cache and last_status_cache[oid] != current_status:
                    txt = (f"🔄 **Holat yangilandi!**\n\n🔢 **Buyurtma №:** `{onum}`\n"
                           f"✅ **Yangi holat:** {STATUS_MAP.get(current_status, current_status)}")
                    if current_status == 'TAYYOR':
                        txt += "\n\n🎉 Buyurtmangiz tayyor!"
                    bot.send_message(chat_id, txt, parse_mode="Markdown")
                
                last_status_cache[oid] = current_status
            conn.close()
        except Exception as e:
            print(f"Status monitor error: {e}")

# --- KLAVIATURA ---
def main_keyboard(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📦 Mening buyurtmalarim"))
    if chat_id in ADMIN_IDS:
        markup.add(types.KeyboardButton("🔔 Tasdiqlash kutilmoqda"))
    return markup

# --- KOMANDALAR ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "👋 Xush kelibsiz! ID raqamingizni yuboring.", 
                     reply_markup=main_keyboard(message.chat.id))

# --- ASOSIY CALLBACK MANAGER (BIRLASHTIRILGAN) ---
@bot.callback_query_handler(func=lambda call: True)
def callback_manager(call):
    data = call.data
    
    # 1. ADMIN TASDIQLASHI
    if data.startswith('approve_'):
        oid = data.split('_')[1]
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE orders_order SET status = 'TAYYOR' WHERE id = ?", (oid,))
            cursor.execute("SELECT customer_chat_id, order_number, pdf_file FROM orders_order WHERE id = ?", (oid,))
            res = cursor.fetchone()
            conn.commit()
            conn.close()

            if res and res[0]:
                chat_id, num, pdf = res
                caption = f"✅ №{num} buyurtmangiz tasdiqlandi!"
                if pdf:
                    path = os.path.join(MEDIA_ROOT, pdf)
                    if os.path.exists(path):
                        with open(path, 'rb') as f:
                            bot.send_document(chat_id, f, caption=caption)
                    else:
                        bot.send_message(chat_id, caption)
                else:
                    bot.send_message(chat_id, caption)
            bot.edit_message_text(f"✅ №{res[1]} tasdiqlandi.", call.message.chat.id, call.message.message_id)
        except Exception as e:
            print(f"Approve error: {e}")

    # 2. ADMIN KO'RISHI
    elif data.startswith('view_admin_'):
        oid = data.split('_')[2]
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT order_number, customer_name, product_name, quantity, status FROM orders_order WHERE id = ?", (oid,))
            order = cursor.fetchone()
            conn.close()
            if order:
                info = (f"📋 **MA'LUMOT**\n\n🔢 **№:** `{order[0]}`\n👤 **Mijoz:** {order[1]}\n"
                        f"📦 **Mahsulot:** {order[2]}\n🔢 **Soni:** {order[3]}\n📊 **Holat:** {STATUS_MAP.get(order[4], order[4])}")
                bot.send_message(call.message.chat.id, info, parse_mode="Markdown")
        except Exception as e:
            print(f"Admin view error: {e}")

    # 3. FOYDALANUVCHI KO'RISHI
    elif data.startswith('view_'):
        oid = data.split('_')[1]
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT order_number, status, product_name FROM orders_order WHERE id = ?", (oid,))
            order = cursor.fetchone()
            conn.close()
            if order:
                bot.send_message(call.message.chat.id, f"📦 **Buyurtma №{order[0]}**\n📊 **Holat:** {STATUS_MAP.get(order[1], order[1])}\n🍎 **Mahsulot:** {order[2]}")
        except Exception as e:
            print(f"User view error: {e}")

# --- TEXT HANDLING ---
@bot.message_handler(func=lambda message: True)
def handle_all(message):
    text = message.text
    chat_id = message.chat.id

    if text == "📦 Mening buyurtmalarim":
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, order_number, status FROM orders_order WHERE customer_chat_id = ?", (chat_id,))
            orders = cursor.fetchall()
            conn.close()
            if orders:
                markup = types.InlineKeyboardMarkup()
                for o in orders:
                    markup.add(types.InlineKeyboardButton(f"№ {o[1]}", callback_data=f"view_{o[0]}"))
                bot.send_message(chat_id, "📑 Buyurtmalaringiz:", reply_markup=markup)
            else:
                bot.send_message(chat_id, "❌ Buyurtmalar topilmadi. ID-ingizni kiriting.")
        except Exception as e:
            print(f"List error: {e}")

    elif text == "🔔 Tasdiqlash kutilmoqda" and chat_id in ADMIN_IDS:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, order_number, customer_name FROM orders_order WHERE status = 'USTA_TUGATDI'")
            pending = cursor.fetchall()
            conn.close()
            if pending:
                for p in pending:
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("✅ TASDIQLASH", callback_data=f"approve_{p[0]}"))
                    bot.send_message(chat_id, f"📦 №: `{p[1]}` | {p[2]}", parse_mode="Markdown", reply_markup=markup)
            else:
                bot.send_message(chat_id, "✅ Kutilayotgan buyurtmalar yo'q.")
        except Exception as e:
            print(f"Pending error: {e}")

    elif text.isdigit():
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT customer_name FROM orders_order WHERE customer_unique_id = ? LIMIT 1", (text,))
            customer = cursor.fetchone()
            if customer:
                cursor.execute("UPDATE orders_order SET customer_chat_id = ? WHERE customer_unique_id = ?", (chat_id, text))
                conn.commit()
                bot.send_message(chat_id, f"✅ Salam, {customer[0]}! Ulandi.")
            else:
                bot.send_message(chat_id, "❌ ID topilmadi.")
            conn.close()
        except Exception as e:
            print(f"Bind error: {e}")

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    init_last_order_id()
    threading.Thread(target=check_new_orders, daemon=True).start()
    threading.Thread(target=check_order_status_updates, daemon=True).start()
    bot.infinity_polling()