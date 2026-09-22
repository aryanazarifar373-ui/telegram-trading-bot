import logging
import random
import string
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram import InputMediaPhoto
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from telegram.error import TimedOut, BadRequest
import io
import mysql.connector
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import time
import os
import re
from num2fawords import words
import jdatetime
import arabic_reshaper
from bidi.algorithm import get_display
from mysql.connector import IntegrityError
from dotenv import load_dotenv

load_dotenv()


# Lock
message_lock = asyncio.Lock()
# Log setting
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# تنظیمات
PRICE_DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("PRICE_DB_NAME", "price_db")
}
ORDERS_DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("ORDERS_DB_NAME", "orders_db")
}
UPDATE_INTERVAL = 300
FONT_PATH = os.getenv("FONT_PATH", "assets/Vazirmatn-RD-Regular.ttf")
FIXED_IMAGE_PATH = os.getenv("FIXED_IMAGE_PATH", "assets/fixed_image.jpg")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DISCUSSION_GROUP_ID = os.getenv("DISCUSSION_GROUP_ID")
SAVED_MESSAGES_ID = os.getenv("SAVED_MESSAGES_ID")
TOKEN = os.getenv("BOT1_TOKEN")
SECOND_BOT_USERNAME = os.getenv("SECOND_BOT_USERNAME", "kadoos_Limit_managment_bot")
ASSETS = ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "طلای 18 عیار", "دلار", "یورو", "دلار تتر"]
REGULAR_ASSETS = ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "طلای 18 عیار"]
VIP_ASSETS = ASSETS
COMMISSION_RATE = 0.0025

# ذخیره اطلاعات کاربر و سفارشات
user_data = {}
order_data = {}

def words_float(num):
    integer_part = int(num)
    decimal_part = int(round((num - integer_part) * 100))
    integer_words = words(integer_part)
    if decimal_part:
        decimal_words = words(decimal_part)
        return f"{integer_words} ممیز {decimal_words}"
    return integer_words

def compress_image(image_path):
    try:
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=70)  # کاهش کیفیت برای کم کردن حجم
        output.seek(0)
        return output
    except Exception as e:
        logger.error(f"Error compressing image {image_path}: {e}")
        raise

def save_user_to_db(first_name, last_name, phone, telegram_id=None, is_vip='no'):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO users (first_name, last_name, phone, telegram_id, is_vip) VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE first_name=%s, last_name=%s, phone=%s, is_vip=%s",
            (first_name, last_name, phone, telegram_id, is_vip, first_name, last_name, phone, is_vip)
        )
        db.commit()
        db.close()
        logger.info(f"User saved/updated to database with telegram_id: {telegram_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error saving user to database: {e}")
        raise
    
def update_user_identity(telegram_id, first_name, last_name, phone):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("UPDATE orders SET first_name=%s, last_name=%s, phone=%s WHERE telegram_id=%s", (first_name, last_name, phone, telegram_id))
        cursor.execute("UPDATE pending_orders SET first_name=%s, last_name=%s, phone=%s WHERE telegram_id=%s", (first_name, last_name, phone, telegram_id))
        db.commit()
        db.close()
        logger.info(f"Updated user identity in orders and pending_orders for telegram_id: {telegram_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating user identity: {e}")
        raise

def get_user_from_db(telegram_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT first_name, last_name, phone, is_vip FROM users WHERE telegram_id = %s", (telegram_id,))
        result = cursor.fetchone()
        db.close()
        if result:
            return {"first_name": result[0], "last_name": result[1], "phone": result[2], "is_vip": result[3]}
        return None
    except mysql.connector.Error as e:
        logger.error(f"Error fetching user from database: {e}")
        raise

def delete_user_data(telegram_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id = %s", (telegram_id,))
        db.commit()
        db.close()
        logger.info(f"Deleted user from users table for telegram_id: {telegram_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error deleting user data: {e}")
        raise

def update_user_is_vip(telegram_id, is_vip='yes'):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("UPDATE users SET is_vip = %s WHERE telegram_id = %s", (is_vip, telegram_id))
        db.commit()
        db.close()
        logger.info(f"Updated is_vip for telegram_id: {telegram_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating is_vip: {e}")
        raise
def get_pending_order(order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, telegram_id, first_name, last_name, phone, transaction_type, asset, quantity, unit, price, expiry_time, 
                   order_type, asset_type, description, photo_ids, has_invoice, channel_message_id, saved_message_id, group_message_id 
            FROM pending_orders WHERE id = %s
            """,
            (order_id,)
        )
        order = cursor.fetchone()
        db.close()
        if not order:
            logger.warning(f"No pending order found for order_id {order_id}")
            return None
        columns = [
            'id', 'telegram_id', 'first_name', 'last_name', 'phone', 'transaction_type', 'asset', 'quantity', 'unit', 
            'price', 'expiry_time', 'order_type', 'asset_type', 'description', 'photo_ids', 'has_invoice', 
            'channel_message_id', 'saved_message_id', 'group_message_id'
        ]
        pending_order = dict(zip(columns, order))
        logger.info(f"Retrieved pending order {order_id} for user {pending_order['telegram_id']}")
        return pending_order
    except mysql.connector.Error as e:
        logger.error(f"Error retrieving pending order {order_id}: {e}")
        return None
    
def save_pending_order(user_id, order_type='limit'):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        photo_ids_str = ','.join(user_data[user_id].get("photo_ids", [])) if user_data[user_id].get("photo_ids") else None
        cursor.execute(
            """
            INSERT INTO pending_orders (first_name, last_name, phone, transaction_type, asset, quantity, unit, price, expiry_time, order_type, telegram_id, asset_type, description, photo_ids, has_invoice)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_data[user_id]["name"],
                user_data[user_id]["last_name"],
                user_data[user_id]["phone"],
                user_data[user_id].get("transaction_type"),
                user_data[user_id].get("asset"),
                user_data[user_id].get("quantity"),
                user_data[user_id].get("unit"),
                user_data[user_id].get("price", 0),
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(user_data[user_id].get("expiry_time", int(time.time())))),
                order_type,
                user_id,
                user_data[user_id].get("asset_type", 'standard'),
                user_data[user_id].get("description"),
                photo_ids_str,
                user_data[user_id].get("has_invoice")
                )
        )
        order_id = cursor.lastrowid
        db.commit()
        db.close()
        logger.info(f"Pending order saved to database for user {user_id}, id: {order_id}")
        return order_id
    except mysql.connector.Error as e:
        logger.error(f"Error saving pending order to database: {e}")
        raise
def update_pending_order_message_ids(order_id, channel_message_id=None, saved_message_id=None, group_message_id=None, user_message_id=None, user_button_message_id=None, saved_button_message_id=None, photo_ids=None):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        query = """
            UPDATE pending_orders
            SET
                channel_message_id = COALESCE(%s, channel_message_id),
                saved_message_id = COALESCE(%s, saved_message_id),
                group_message_id = COALESCE(%s, group_message_id),
                user_message_id = COALESCE(%s, user_message_id),
                user_button_message_id = COALESCE(%s, user_button_message_id),
                saved_button_message_id = COALESCE(%s, saved_button_message_id),
                photo_ids = COALESCE(%s, photo_ids)
            WHERE id = %s
        """
        cursor.execute(query, (
            channel_message_id,
            saved_message_id,
            group_message_id,
            user_message_id,
            user_button_message_id,
            saved_button_message_id,
            photo_ids,
            order_id
        ))
        db.commit()
        db.close()
        logger.info(f"Updated message IDs for order {order_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating message IDs for order {order_id}: {e}")
        return False
                
async def delete_pending_order_and_proposals(order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            "SELECT channel_message_id, saved_message_id, group_message_id, asset_type, telegram_id, user_message_id, user_button_message_id, saved_button_message_id, order_type, photo_ids FROM pending_orders WHERE id = %s",
            (order_id,)
        )
        order = cursor.fetchone()
        if not order:
            logger.error(f"No order found for order_id {order_id}")
            db.close()
            return None
        
        channel_message_id, saved_message_id, group_message_id, asset_type, telegram_id, user_message_id, user_button_message_id, saved_button_message_id, order_type, photo_ids = order
        
        # لاگ‌گیری برای بررسی مقادیر
        logger.info(f"Order {order_id} details: channel_message_id={channel_message_id}, user_message_id={user_message_id}, user_button_message_id={user_button_message_id}, telegram_id={telegram_id}, photo_ids={photo_ids}")

        # جمع‌آوری تمام comment_message_id از proposals برای حذف ریپلای‌ها (در گروه)
        cursor.execute("SELECT comment_message_id FROM proposals WHERE pending_order_id = %s", (order_id,))
        proposals = cursor.fetchall()
        comment_message_ids = [row[0] for row in proposals if row[0] is not None]
        
        # حذف از دیتابیس
        cursor.execute("DELETE FROM proposals WHERE pending_order_id = %s", (order_id,))
        cursor.execute("DELETE FROM pending_orders WHERE id = %s", (order_id,))
        db.commit()
        db.close()
        
        # تبدیل photo_ids به لیست (اگر وجود داشته باشد)
        user_message_ids = []
        if photo_ids and asset_type == "second_hand":
            user_message_ids = [int(pid) for pid in photo_ids.split(",") if pid] if photo_ids else []
        
        # تابع داخلی برای حذف پیام‌ها
        async def delete_messages(context):
            try:
                # حذف از کانال
                if channel_message_id:
                    try:
                        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_message_id)
                        logger.info(f"Deleted channel message {channel_message_id} for order {order_id}")
                        if asset_type == "standard":
                            try:
                                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_message_id + 1)  # حذف پیام دکمه‌دار
                                logger.info(f"Deleted channel button message {channel_message_id + 1} for order {order_id}")
                            except Exception as e:
                                logger.error(f"Failed to delete channel button message {channel_message_id + 1}: {e}")
                        elif asset_type == "second_hand":
                            for msg_id in range(channel_message_id, channel_message_id + 3):
                                try:
                                    await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                                    logger.info(f"Deleted channel message {msg_id} for order {order_id}")
                                except Exception as e:
                                    logger.error(f"Failed to delete channel message {msg_id}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to delete channel message {channel_message_id}: {e}")
                
                # حذف از Saved Messages
                if saved_message_id:
                    try:
                        await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=saved_message_id)
                        logger.info(f"Deleted saved message {saved_message_id} for order {order_id}")
                        if saved_button_message_id:
                            await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=saved_button_message_id)
                            logger.info(f"Deleted saved button message {saved_button_message_id} for order {order_id}")
                        if asset_type == "standard" and order_type != 'spot':
                            try:
                                await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=saved_message_id + 1)
                                logger.info(f"Deleted saved +1 message {saved_message_id + 1} for order {order_id}")
                            except Exception as e:
                                logger.error(f"Failed to delete saved +1 message: {e}")
                        elif asset_type == "second_hand":
                            for msg_id in range(saved_message_id, saved_message_id + 3):
                                try:
                                    await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=msg_id)
                                    logger.info(f"Deleted saved message {msg_id} for order {order_id}")
                                except Exception as e:
                                    logger.error(f"Failed to delete saved message {msg_id}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to delete saved messages for order {order_id}: {e}")
                
                # حذف از گروه (شامل ریپلای‌ها)
                if group_message_id:
                    try:
                        if asset_type == "second_hand":
                            for msg_id in range(group_message_id - 2, group_message_id + 1):
                                try:
                                    await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=msg_id)
                                    logger.info(f"Deleted group message {msg_id} for order {order_id}")
                                except Exception as e:
                                    logger.error(f"Failed to delete group message {msg_id}: {e}")
                        else:
                            await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=group_message_id)
                            logger.info(f"Deleted group message {group_message_id} for order {order_id}")
                            await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=group_message_id + 1)
                            logger.info(f"Deleted group button message {group_message_id + 1} for order {order_id}")
                        
                        # حذف تمام ریپلای‌ها (comment_message_id) از گروه
                        for comment_id in comment_message_ids:
                            try:
                                await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=comment_id)
                                logger.info(f"Deleted comment message {comment_id} for order {order_id}")
                            except Exception as e:
                                logger.error(f"Failed to delete comment message {comment_id}: {e}")
                    except Exception as e:
                        logger.error(f"Failed to delete group messages for order {order_id}: {e}")
                
                # حذف از چت خصوصی کاربر
                if telegram_id and user_message_id:
                    try:
                        await context.bot.delete_message(chat_id=telegram_id, message_id=user_message_id)
                        logger.info(f"Deleted user message {user_message_id} in chat {telegram_id} for order {order_id}")
                    except Exception as e:
                        logger.error(f"Failed to delete user message {user_message_id} in chat {telegram_id}: {e}")
                if telegram_id and user_button_message_id:
                    try:
                        await context.bot.delete_message(chat_id=telegram_id, message_id=user_button_message_id)
                        logger.info(f"Deleted user button message {user_button_message_id} in chat {telegram_id} for order {order_id}")
                    except Exception as e:
                        logger.error(f"Failed to delete user button message {user_button_message_id} in chat {telegram_id}: {e}")
                
                # حذف تمام پیام‌های اضافی (عکس‌ها) در چت خصوصی برای طلای دسته دوم
                if asset_type == "second_hand" and user_message_ids:
                    for msg_id in user_message_ids:
                        try:
                            await context.bot.delete_message(chat_id=telegram_id, message_id=msg_id)
                            logger.info(f"Deleted additional user message {msg_id} in chat {telegram_id} for order {order_id}")
                        except Exception as e:
                            logger.error(f"Failed to delete additional user message {msg_id} in chat {telegram_id}: {e}")
                
                # لاگ‌گیری نهایی
                logger.info(f"All messages for order {order_id} processed for deletion")
            
            except Exception as e:
                logger.error(f"Error deleting messages for order_id {order_id}: {e}")
        
        logger.info(f"Deleted pending order {order_id} and related proposals from database")
        return delete_messages
    
    except mysql.connector.Error as e:
        logger.error(f"Error accessing database for order_id {order_id}: {e}")
        return None
                                
def delete_pending_order(order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("DELETE FROM pending_orders WHERE id = %s", (order_id,))
        db.commit()
        db.close()
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error deleting pending order: {e}")
        return False
    
def save_order_to_db_from_pending(pending_order, agreed_price):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        photo_ids_str = ','.join(pending_order["photo_ids"]) if pending_order["photo_ids"] else None
        cursor.execute(
            """
            INSERT INTO orders (first_name, last_name, phone, transaction_type, asset, quantity, unit, price, expiry_time, order_type, telegram_id, asset_type, description, photo_ids, has_invoice)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pending_order["first_name"],
                pending_order["last_name"],
                pending_order["phone"],
                pending_order["transaction_type"],
                pending_order["asset"],
                pending_order["quantity"],
                pending_order["unit"],
                agreed_price,
                pending_order["expiry_time"],
                pending_order["order_type"],
                pending_order["telegram_id"],
                pending_order.get("asset_type", 'standard'),
                pending_order.get("description"),
                photo_ids_str,
                pending_order.get("has_invoice")
            )
        )
        db.commit()
        db.close()
        logger.info(f"Order saved to database from pending_order {pending_order['id']}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error saving order from pending: {e}")
        return False
        
def save_spot_order_to_db(user_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO orders (first_name, last_name, phone, transaction_type, asset, quantity, unit, price, expiry_time, order_type, telegram_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_data[user_id]["name"],
                user_data[user_id]["last_name"],
                user_data[user_id]["phone"],
                user_data[user_id]["transaction_type"],
                user_data[user_id]["asset"],
                user_data[user_id]["quantity"],
                user_data[user_id]["unit"],
                user_data[user_id].get("price", 0),
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(time.time()))),
                'spot',
                user_id
            )
        )
        db.commit()
        db.close()
        logger.info(f"Spot order saved to database for user {user_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error saving spot order to database: {e}")
        return False
    
def get_prices_from_db(table_name="limit_prices"):
    try:
        db = mysql.connector.connect(**PRICE_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(f"SELECT asset, buy_price, sell_price, updated_at FROM {table_name}")
        data = cursor.fetchall()
        db.close()
        if not data:
            logger.warning(f"No data found in '{table_name}' table!")
            return None
        df = pd.DataFrame(data, columns=["asset", "buy_price", "sell_price", "updated_at"])
        logger.info(f"Data retrieved from {table_name}:\n%s", df)
        return df
    except mysql.connector.Error as e:
        logger.error(f"Error in fetching data from {table_name}: {e}")
        return None
    
def create_price_table(df, title="📊 جدول قیمت تابلویی (تعدیل شده با کارمزد 0.25%)"):
    if df is None or df.empty:
        logger.warning("DataFrame خالی است یا وجود ندارد")
        return False
    try:
        if not os.path.exists(FONT_PATH):
            logger.error(f"فونت در مسیر {FONT_PATH} یافت نشد")
            return False
        image_width = 900
        image_height = 80 + 40 * len(df) + 100
        image = Image.new("RGB", (image_width, image_height), "#F5F5F5")
        draw = ImageDraw.Draw(image)
        header_font = ImageFont.truetype(FONT_PATH, 24)
        body_font = ImageFont.truetype(FONT_PATH, 24)
        draw.rectangle((10, 10, image_width-10, image_height-10), outline="#1E3A8A", width=2)
        header_text = arabic_reshaper.reshape(title)
        header_text = get_display(header_text)
        text_bbox = draw.textbbox((0, 0), header_text, font=header_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_x = (image_width - text_width) // 2
        draw.text((text_x, 20), header_text, font=header_font, fill="#1E3A8A")
        draw.line((20, 60, image_width-20, 60), fill="#1E3A8A", width=2)
        draw.text((20, 70), get_display(arabic_reshaper.reshape("دارایی")), font=body_font, fill="black")
        draw.text((300, 70), get_display(arabic_reshaper.reshape("قیمت خرید")), font=body_font, fill="black")
        draw.text((600, 70), get_display(arabic_reshaper.reshape("قیمت فروش")), font=body_font, fill="black")
        draw.line((20, 100, image_width-20, 100), fill="#1E3A8A", width=2)
        y = 110
        for _, row in df.iterrows():
            asset_text = get_display(arabic_reshaper.reshape(str(row["asset"])))
            buy_price_text = f"{row['buy_price']:,.0f}"
            sell_price_text = f"{row['sell_price']:,.0f}"
            draw.text((20, y), asset_text, font=body_font, fill="black")
            draw.text((300, y), buy_price_text, font=body_font, fill="black")
            draw.text((600, y), sell_price_text, font=body_font, fill="black")
            y += 40
        last_update = df["updated_at"].iloc[0]
        next_update = int(time.mktime(last_update.timetuple()) + UPDATE_INTERVAL)
        seconds_left = next_update - int(time.time())
        minutes, seconds = divmod(seconds_left, 60)
        timer_text = f"⏳ آپدیت بعدی قیمت: {minutes} دقیقه {seconds} ثانیه"
        timer_text = get_display(arabic_reshaper.reshape(timer_text))
        draw.text((20, y), timer_text, font=body_font, fill="#B91C1C")
        output_path = f"{'spot' if 'سفارشات فوری' in title else 'limit'}_prices_table.png"
        image.save(output_path)
        logger.info(f"تصویر در مسیر {output_path} ذخیره شد")
        return True
    except Exception as e:
        logger.error(f"خطا در ساخت تصویر: {e}", exc_info=True)
        return False

def parse_duration(text):
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    patterns = [
        (r"(\d+)\s*(دقیقه|دقيقه)", 60),
        (r"(\d+)\s*(ساعت)", 3600),
        (r"(\d+)\s*(روز)", 86400),
        (r"(\d+)\s*(هفته)", 604800),
        (r"(یک|يک)\s*(دقیقه|دقيقه)", 60),
        (r"(یک|يک)\s*(ساعت)", 3600),
        (r"(یک|يک)\s*(روز)", 86400),
        (r"(یک|يک)\s*(هفته)", 604800)
    ]
    for pattern, seconds in patterns:
        match = re.match(pattern, text)
        if match:
            value = 1 if match.group(1) in ["یک", "يک"] else int(match.group(1))
            return value * seconds, None
    return None, "🚫 لطفاً زمان را به‌صورت معتبر وارد کنید (مثل '2 ساعت' یا '1 روز')."

def format_number(number):
    try:
        return f"{int(number):,}"
    except ValueError:
        return number
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /start ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    logger.info(f"Command /start ordered by {user_id} in private chat")
    welcome_message = (
            "خوش آمدید!!!☺️🌹\n"
            "برای استفاده از امکانات ربات لطفا از بین کشوی Menu یکی از گزینه ها را انتخاب کنید🙏"  
    )          
    await update.message.reply_text(welcome_message)

async def prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /prices ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    logger.info(f"دکمه (دریافت قیمت لحظه‌ای) توسط کاربر {user_id} انتخاب شد")
    df = get_prices_from_db("limit_prices")
    if create_price_table(df):
        await context.bot.send_photo(
            chat_id=user_id,
            photo=open("limit_prices_table.png", "rb")
        )
        logger.info("تصویر جدول با موفقیت برای کاربر ارسال شد")
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text="🚫 خطایی در ساخت جدول رخ داد. لطفاً بعداً دوباره امتحان کنید."
        )
        logger.warning("ارسال تصویر ناموفق بود!")
async def spot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /spot ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    logger.info(f"دکمه (سفارش فوری) توسط کاربر {user_id} انتخاب شد")
    user_data[user_id] = {"step": "check_user", "order_type": "spot", "asset_type": "standard"}
    df = get_prices_from_db("spot_prices")
    if create_price_table(df, title="‼️ جدول قیمت سفارشات فوری"):
        await context.bot.send_photo(
            chat_id=user_id,
            photo=open("spot_prices_table.png", "rb"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید و ادامه", callback_data="confirm_spot_order"),
                 InlineKeyboardButton("🚫 لغو سفارش", callback_data="cancel_order")]
            ])
        )
        logger.info("تصویر جدول سفارشات فوری با موفقیت برای کاربر ارسال شد")
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text="🚫 خطایی در ساخت جدول رخ داد. لطفاً بعداً دوباره امتحان کنید."
        )
        logger.warning("ارسال تصویر سفارشات فوری ناموفق بود!")

async def limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /limit ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    logger.info(f"دکمه (سفارش‌گذاری) توسط کاربر {user_id} انتخاب شد")
    user_data[user_id] = {"step": "check_user", "order_type": "limit", "asset_type": "standard"}
    user_info = get_user_from_db(user_id)
    if user_info:
        user_data[user_id]["name"] = user_info["first_name"]
        user_data[user_id]["last_name"] = user_info["last_name"]
        user_data[user_id]["phone"] = user_info["phone"]
        user_data[user_id]["is_vip"] = user_info["is_vip"]
        await update.message.reply_text(
            f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                 InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    else:
        user_data[user_id]["step"] = "name"
        user_data[user_id]["is_new_user"] = True
        await update.message.reply_text(
            "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
        )
async def secondhand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /secondhand ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    logger.info(f"دکمه (فروش طلای دسته دوم) توسط کاربر {user_id} انتخاب شد")
    user_data[user_id] = {"step": "check_user", "order_type": "second_hand", "transaction_type": "فروش", "asset_type": "second_hand", "photo_ids": []}
    user_info = get_user_from_db(user_id)
    if user_info:
        user_data[user_id]["name"] = user_info["first_name"]
        user_data[user_id]["last_name"] = user_info["last_name"]
        user_data[user_id]["phone"] = user_info["phone"]
        user_data[user_id]["is_vip"] = user_info["is_vip"]
        await update.message.reply_text(
            f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                 InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    else:
        user_data[user_id]["step"] = "name"
        user_data[user_id]["is_new_user"] = True
        await update.message.reply_text(
            "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
        )

async def addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private":
        logger.info(f"Command /addvip ordered by {user_id} in non-private chat {update.effective_chat.id}")
        return
    if str(user_id) != SAVED_MESSAGES_ID:
        await update.message.reply_text("🚫 دسترسی غیرمجاز. این دستور فقط برای ادمین مجاز است.")
        logger.info(f"Unauthorized access to /addvip by user {user_id}")
        return
    logger.info(f"دکمه (افزودن مشتری VIP) توسط کاربر {user_id} انتخاب شد")
    user_data[user_id] = {"step": "enter_vip_telegram_id", "is_vip_registration": True}
    await update.message.reply_text(
        "لطفا telegram_id کاربر از جدول users را وارد کنید 🙏",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="cancel_vip_registration")]])
    )

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15), retry=retry_if_exception_type(TimedOut))
async def send_media_with_cleanup(chat_id, photo_ids, text, is_saved=False, context=None, asset_type=None, complete_keyboard=None):
    try:
        logger.info(f"Sending media to chat {chat_id}, asset_type: {asset_type}")
        if photo_ids and asset_type == "second_hand":
            media = [InputMediaPhoto(media=photo_id, caption=text if i == 0 else "") for i, photo_id in enumerate(photo_ids)]
            media_group = await context.bot.send_media_group(chat_id=chat_id, media=media)
            message_ids = [msg.message_id for msg in media_group]
            logger.info(f"Sent media group to chat {chat_id}, message_ids: {message_ids}")
            return message_ids[0], message_ids
        elif asset_type == "standard":
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=compress_image(FIXED_IMAGE_PATH),
                caption=text,
                reply_markup=complete_keyboard if is_saved and chat_id != SAVED_MESSAGES_ID else None
            )
            logger.info(f"Sent photo to chat {chat_id}, message_id: {msg.message_id}")
            return msg.message_id, [msg.message_id]
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=complete_keyboard if is_saved and chat_id != SAVED_MESSAGES_ID else None
            )
            logger.info(f"Sent text message to chat {chat_id}, message_id: {msg.message_id}")
            return msg.message_id, [msg.message_id]
    except TimedOut as e:
        logger.error(f"Timeout sending message to chat {chat_id}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to send message to chat {chat_id}: {e}")
        raise

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_type = query.message.chat.type
    await query.answer()
    logger.info(f"Callback received from user {user_id}: {query.data}")
    
    if chat_type != "private" and not query.data.startswith("cancel_") and query.data != "complete_order":
        logger.info(f"Button is touched by {user_id} in non-private chat {query.message.chat_id}")
        return
        
    if query.data == "cancel_order":
        await query.message.reply_text("درخواست شما با موفقیت لغو شد ✅")
        logger.info(f"Order cancelled by user {user_id}")
        if user_id in user_data:
            del user_data[user_id]
        return
    elif query.data.startswith("delete_order_"):
        try:
            order_id = int(query.data.split("_")[2])
        except ValueError:
            logger.warning(f"Invalid delete callback: {query.data}")
            return
        pending_order = get_pending_order(order_id)
        if not pending_order:
            await query.message.reply_text("🚫 سفارش تکمیل یا حذف شده است.")
            return
        if user_id != pending_order["telegram_id"]:
            await context.bot.send_message(user_id, "🚫 این سفارش متعلق به شما نیست.")
            return
        # اجرای کوروتین برای حذف
        delete_coroutine = await delete_pending_order_and_proposals(order_id)
        if delete_coroutine:
            await delete_coroutine(context)
            # انتخاب پیام بر اساس نوع سفارش
            if pending_order["order_type"] == "spot":
                await context.bot.send_message(user_id, "✅سفارش شما با موفقیت حذف شد✅")
            else:
                if pending_order["asset"] in ["دلار", "یورو", "دلار تتر"]:
                    await context.bot.send_message(user_id, "✅سفارش شما با موفقیت حذف شد✅")
                else:
                    await context.bot.send_message(user_id, "✅سفارش شما با موفقیت از کانال حذف شد✅")
            logger.info(f"Order {order_id} deleted by user {user_id}")
        else:
            await context.bot.send_message(user_id, "🚫 خطا در حذف سفارش.")
            logger.error(f"Failed to delete order {order_id} for user {user_id}")                                       
    elif query.data.startswith("complete_order"):
        if str(user_id) != SAVED_MESSAGES_ID:
            await query.message.reply_text("🚫 دسترسی غیرمجاز. فقط ادمین می‌تواند سفارش را تکمیل کند.")
            return
        try:
            order_id = int(query.data.split("_")[2]) if query.data.startswith("complete_order_") else None
            if not order_id:
                db = mysql.connector.connect(**ORDERS_DB_CONFIG)
                cursor = db.cursor()
                cursor.execute(
                    "SELECT id, channel_message_id, saved_message_id, group_message_id, asset_type, transaction_type, price, telegram_id, first_name, last_name, phone, asset, quantity, unit, expiry_time, order_type, description, photo_ids, has_invoice FROM pending_orders WHERE saved_message_id = %s",
                    (query.message.message_id,)
                )
                order = cursor.fetchone()
                db.close()
                if not order:
                    await query.message.reply_text("🚫 داده‌های سفارش یافت نشد.")
                    return
                columns = ['id', 'channel_message_id', 'saved_message_id', 'group_message_id', 'asset_type', 'transaction_type', 'price', 'telegram_id', 'first_name', 'last_name', 'phone', 'asset', 'quantity', 'unit', 'expiry_time', 'order_type', 'description', 'photo_ids', 'has_invoice']
                pending_order = dict(zip(columns, order))
            else:
                pending_order = get_pending_order(order_id)
                if not pending_order:
                    await query.message.reply_text("🚫 داده‌های سفارش یافت نشد.")
                    return
            
            if pending_order["photo_ids"]:
                pending_order["photo_ids"] = pending_order["photo_ids"].split(',')
            
            agreed_price = pending_order["price"]
            if not save_order_to_db_from_pending(pending_order, agreed_price):
                await query.message.reply_text("🚫 خطا در ذخیره سفارش در جدول نهایی.")
                return
            
            # حذف پیام‌ها با استفاده از تابع delete_pending_order_and_proposals
            delete_func = await delete_pending_order_and_proposals(pending_order["id"])
            if delete_func:
                await delete_func(context)
                await query.message.reply_text("✅ سفارش تکمیل و حذف شد! ✅")
                logger.info(f"Order {pending_order['id']} completed and deleted by user {user_id}")
            else:
                await query.message.reply_text("🚫 خطا در حذف سفارش از دیتابیس.")
                return
        except Exception as e:
            logger.error(f"Error completing order for user {user_id}: {e}")
            await query.message.reply_text(f"🚫 خطایی در تکمیل سفارش رخ داد: {e}")            
    elif query.data == "show_prices":
        logger.info(f"دکمه (دریافت قیمت لحظه‌ای) توسط کاربر {user_id} انتخاب شد")
        df = get_prices_from_db("limit_prices")
        if create_price_table(df):
            await context.bot.send_photo(
                chat_id=user_id,
                photo=open("limit_prices_table.png", "rb")
            )
            logger.info("تصویر جدول با موفقیت برای کاربر ارسال شد")
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 خطایی در ساخت جدول رخ داد. لطفاً بعداً دوباره امتحان کنید."
                )
            logger.warning("ارسال تصویر ناموفق بود!")
    elif query.data == "limit_order":
        logger.info(f"دکمه (سفارش‌گذاری) توسط کاربر {user_id} انتخاب شد")
        user_data[user_id] = {"step": "check_user", "order_type": "limit", "asset_type": "standard"}
        user_info = get_user_from_db(user_id)
        if user_info:
            user_data[user_id]["name"] = user_info["first_name"]
            user_data[user_id]["last_name"] = user_info["last_name"]
            user_data[user_id]["phone"] = user_info["phone"]
            user_data[user_id]["is_vip"] = user_info["is_vip"]
            await query.message.reply_text(
                f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            user_data[user_id]["step"] = "name"
            user_data[user_id]["is_new_user"] = True
            await query.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
            )
    elif query.data == "spot_order":
        logger.info(f"دکمه (سفارش فوری) توسط کاربر {user_id} انتخاب شد")
        user_data[user_id] = {"step": "check_user", "order_type": "spot", "asset_type": "standard"}
        df = get_prices_from_db("spot_prices")
        if create_price_table(df, title="‼️ جدول قیمت سفارشات فوری"):
            await context.bot.send_photo(
                chat_id=user_id,
                photo=open("spot_prices_table.png", "rb"),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تأیید و ادامه", callback_data="confirm_spot_order"),
                     InlineKeyboardButton("🚫 لغو سفارش", callback_data="cancel_order")]
                ])
            )
            logger.info("تصویر جدول سفارشات فوری با موفقیت برای کاربر ارسال شد")
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 خطایی در ساخت جدول رخ داد. لطفاً بعداً دوباره امتحان کنید."
            )
            logger.warning("ارسال تصویر سفارشات فوری ناموفق بود!")
    elif query.data == "confirm_spot_order":
        user_data[user_id]["step"] = "check_user"
        user_info = get_user_from_db(user_id)
        if user_info:
            user_data[user_id]["name"] = user_info["first_name"]
            user_data[user_id]["last_name"] = user_info["last_name"]
            user_data[user_id]["phone"] = user_info["phone"]
            user_data[user_id]["is_vip"] = user_info["is_vip"]
            await query.message.reply_text(
                f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            user_data[user_id]["step"] = "name"
            user_data[user_id]["is_new_user"] = True
            await query.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                )
    elif query.data == "second_hand_order":
        logger.info(f"دکمه (فروش طلای دسته دوم) توسط کاربر {user_id} انتخاب شد")
        user_data[user_id] = {"step": "check_user", "order_type": "second_hand", "transaction_type": "فروش", "asset_type": "second_hand", "photo_ids": []}
        user_info = get_user_from_db(user_id)
        if user_info:
            user_data[user_id]["name"] = user_info["first_name"]
            user_data[user_id]["last_name"] = user_info["last_name"]
            user_data[user_id]["phone"] = user_info["phone"]
            user_data[user_id]["is_vip"] = user_info["is_vip"]
            await query.message.reply_text(
                f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            user_data[user_id]["step"] = "name"
            user_data[user_id]["is_new_user"] = True
            await query.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
            )
    elif query.data == "add_vip_customer" and str(user_id) == SAVED_MESSAGES_ID:
        logger.info(f"دکمه (افزودن مشتری VIP) توسط کاربر {user_id} انتخاب شد")
        user_data[user_id] = {"step": "enter_vip_telegram_id", "is_vip_registration": True}
        await query.message.reply_text(
            "لطفا telegram_id کاربر از جدول users را وارد کنید 🙏",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="cancel_vip_registration")]])
        )
    elif query.data == "confirm_user_info_yes":
        if user_data[user_id].get("is_update", False):
            if not save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                await query.message.reply_text("🚫 خطا در ذخیره اطلاعات جدید کاربر.")
                return
            if not update_user_identity(user_id, user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"]):
                await query.message.reply_text("🚫 خطا در بروزرسانی اطلاعات کاربر.")
                return
            del user_data[user_id]["is_update"]
        elif user_data[user_id].get("is_new_user", False):
            if not save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                await query.message.reply_text("🚫 خطا در ذخیره اطلاعات کاربر. لطفاً دوباره امتحان کنید.")
                return 
        if user_data[user_id]["order_type"] in ["spot", "limit"]:
            user_data[user_id]["step"] = "transaction_type"
            keyboard = [[InlineKeyboardButton("🟢 خرید", callback_data="buy"), InlineKeyboardButton("🔴 فروش", callback_data="sell")]]
            await query.message.reply_text(
                "نوع معامله را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard + [
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif user_data[user_id]["order_type"] == "second_hand":
            user_data[user_id]["step"] = "has_invoice"
            await query.message.reply_text(
                "آیا فاکتور خرید دارید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_invoice_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_invoice_no")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
    elif query.data == "has_invoice_yes":
        user_data[user_id]["has_invoice"] = "yes"
        user_data[user_id]["step"] = "item_name"
        await query.message.reply_text(
            "قصد فروش چه دارایی دارید؟ (لطفا کوتاه بنویسید مثلا گوشواره طلا، دستبند طلا و ...)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                 InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    elif query.data == "has_invoice_no":
        user_data[user_id]["has_invoice"] = "no"
        await query.message.reply_text("لطفا منتظر تماس ادمین باشید ☺️")
        saved_text = (
            f"🛍 درخواست فروش طلای دست دوم\n"
            f"👤 نام: {user_data[user_id]['name']}\n"
            f"👤 نام خانوادگی: {user_data[user_id]['last_name']}\n"
            f"📞 شماره تماس: {user_data[user_id]['phone']}"
        )
        await context.bot.send_message(
            chat_id=SAVED_MESSAGES_ID,
            text=saved_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("تماس گرفته شد ✅", callback_data="contacted")]])
        )
        if user_id in user_data:
            del user_data[user_id]
    elif query.data == "contacted":
        await query.message.delete()
    elif query.data == "confirm_user_info_no":
        delete_user_data(user_id)
        user_data[user_id]["step"] = "name"
        user_data[user_id]["is_update"] = True
        user_data[user_id]["is_new_user"] = False
        await query.message.reply_text(
            "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
        )
    elif query.data == "cancel_vip_registration" and str(user_id) == SAVED_MESSAGES_ID:
        await query.message.reply_text("🚫 ثبت مشتری VIP لغو شد.")
        logger.info(f"VIP registration cancelled by user {user_id}")
        if user_id in user_data:
            del user_data[user_id]
    elif query.data == "previous_question":
        current_step = user_data.get(user_id, {}).get("step")
        logger.info(f"Previous question requested by {user_id} in step {current_step}")
        if not current_step:
            logger.warning(f"No step found for user {user_id}")
            await query.message.reply_text("🚫 خطایی رخ داد. لطفاً یکی از دستورات منو را انتخاب کنید.")
            return
        if current_step == "check_user":
            await query.message.reply_text("🚫 این اولین مرحله است. لطفاً ادامه دهید یا دستور دیگری از منو انتخاب کنید.")
            return
        elif current_step == "name":
            user_data[user_id]["step"] = "check_user"
            await query.message.reply_text("👋 خوش آمدید!")
            await query.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
            )
        elif current_step == "last_name":
            user_data[user_id]["step"] = "name"
            await query.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )            
        elif current_step == "phone":
            user_data[user_id]["step"] = "last_name"
            await query.message.reply_text(
                "لطفاً نام خانوادگی خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "confirm_user_info":
            user_data[user_id]["step"] = "phone"
            await query.message.reply_text(
                "لطفاً شماره تماس خود را وارد کنید (لطفاً اعداد را فقط به انگلیسی وارد کنید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "transaction_type":
            user_data[user_id]["step"] = "check_user"
            user_info = get_user_from_db(user_id)
            if user_info:
                await query.message.reply_text(
                    f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                         InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                         [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                        ])
                )
            else:
                await query.message.reply_text("👋 خوش آمدید!")
                user_data[user_id]["step"] = "name"
                user_data[user_id]["is_new_user"] = True
                await query.message.reply_text(
                    "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                )
        elif current_step == "asset":
            user_data[user_id]["step"] = "transaction_type"
            keyboard = [[InlineKeyboardButton("🟢 خرید", callback_data="buy"), InlineKeyboardButton("🔴 فروش", callback_data="sell")]]
            await query.message.reply_text(
                "نوع معامله را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(keyboard + [
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "has_invoice":
            user_data[user_id]["step"] = "check_user"
            user_info = get_user_from_db(user_id)
            if user_info:
                await query.message.reply_text(
                    f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                         InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                        [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
            else:
                await query.message.reply_text("👋 خوش آمدید!")
                user_data[user_id]["step"] = "name"
                user_data[user_id]["is_new_user"] = True
                await query.message.reply_text(
                    "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                )
        elif current_step == "item_name":
            logger.info(f"Returning to has_invoice for user {user_id}")
            user_data[user_id]["step"] = "has_invoice"
            await query.message.reply_text(
                "آیا فاکتور خرید دارید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_invoice_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_invoice_no")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        elif current_step == "quantity":
            if user_data[user_id]["order_type"] == "second_hand":
                user_data[user_id]["step"] = "item_name"
                await query.message.reply_text(
                    "قصد فروش چه دارایی دارید؟ (لطفا کوتاه بنویسید مثلا گوشواره طلا، دستبند طلا و ...)",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                         InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
            else:
                user_data[user_id]["step"] = "asset"
                asset_list = VIP_ASSETS if user_data[user_id].get("is_vip") == "yes" else REGULAR_ASSETS
                keyboard = [
                    [InlineKeyboardButton(f"🪙 {asset}" if asset in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی"] else f"🟡 {asset}" if asset == "طلای 18 عیار" else f"💵 {asset}" if asset == "دلار" else f"💶 {asset}" if asset == "یورو" else f"🪙 {asset}", callback_data=asset)]
                    for asset in asset_list
                ]
                await query.message.reply_text(
                    "نوع دارایی را انتخاب کنید:",
                    reply_markup=InlineKeyboardMarkup(keyboard + [
                        [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                         InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                    ])
                )
        elif current_step == "confirm_quantity":
            user_data[user_id]["step"] = "quantity"
            unit = user_data[user_id]["unit"]
            await query.message.reply_text(
                f"لطفاً {unit} خود را وارد کنید (فقط عدد مورد نظر را وارد کنید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "upload_first_photo":
            user_data[user_id]["step"] = "confirm_quantity"
            unit = user_data[user_id]["unit"]
            quantity_words = words_float(user_data[user_id]["quantity"])
            await query.message.reply_text(
                f"مقدار: {user_data[user_id]['quantity']} ({quantity_words} {unit})\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_quantity"),
                     InlineKeyboardButton("خیر 👎", callback_data="retry_quantity")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
                )
        elif current_step == "upload_second_photo":
            user_data[user_id]["step"] = "upload_first_photo"
            user_data[user_id]["photo_ids"] = []  # پاک کردن photo_ids برای بازگشت به عکس اول
            await query.message.reply_text(
                "لطفا اولین عکس از دارایی خود را ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        elif current_step == "confirm_first_photo":
            user_data[user_id]["step"] = "upload_first_photo"
            user_data[user_id]["photo_ids"] = []  # پاک کردن photo_ids
            await query.message.reply_text(
                "لطفا اولین عکس از دارایی خود را ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        elif current_step == "confirm_second_photo":
            user_data[user_id]["step"] = "upload_second_photo"
            user_data[user_id]["photo_ids"] = user_data[user_id]["photo_ids"][:1]  # نگه‌داشتن فقط عکس اول
            await query.message.reply_text(
                "لطفا عکس دوم از دارایی خود را از زاویه دیگر ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                    InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        elif current_step == "price":
            if user_data[user_id]["order_type"] == "second_hand":
                user_data[user_id]["step"] = "confirm_second_photo"
                await query.message.reply_text(
                    "آیا از عکس ارسالی دوم خود مطمئن هستید؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("بله 👍", callback_data="confirm_photo_yes"),
                         InlineKeyboardButton("خیر 👎", callback_data="confirm_photo_no")],
                        [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
            else:
                user_data[user_id]["step"] = "confirm_quantity"
                unit = user_data[user_id]["unit"]
                await query.message.reply_text(
                    f"مقدار: {user_data[user_id]['quantity']} ({words_float(user_data[user_id]['quantity'])} {unit})\nدرست است؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("👍 بله", callback_data="confirm_quantity"),
                         InlineKeyboardButton("👎 خیر", callback_data="retry_quantity")],
                        [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
        elif current_step == "confirm_price":
            user_data[user_id]["step"] = "price"
            transaction_type = user_data[user_id]["transaction_type"]
            await query.message.reply_text(
                f"لطفاً قیمت {transaction_type} هر واحد را دوباره وارد کنید (لطفاً قیمت را بر اساس تومان وارد کنید):",
                reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
                )
        elif current_step == "expiry":
            user_data[user_id]["step"] = "confirm_price"
            formatted_price = format_number(user_data[user_id]["price"])
            await query.message.reply_text(
                f"قیمت واردشده: {formatted_price} تومان\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👍 بله", callback_data="confirm_price"),
                     InlineKeyboardButton("👎 خیر", callback_data="retry_price")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "has_description":
            user_data[user_id]["step"] = "price"
            await query.message.reply_text(
                f"لطفاً قیمت فروش هر گرم را تعیین کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                    InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )                      
        elif current_step == "description":
            user_data[user_id]["step"] = "has_description"
            await query.message.reply_text(
                "آیا توضیحات اضافی مایل هستید در اختیار اعضای گروه قرار دهید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_description_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_description_no")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                     ])
            )
        elif current_step == "confirm_order" and user_data[user_id]["order_type"] == "limit":
            user_data[user_id]["step"] = "expiry"
            await query.message.reply_text(
                "لطفاً زمان انقضای سفارش را وارد کنید (مثل '2 ساعت' یا '1 روز'):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "confirm_order" and user_data[user_id]["order_type"] == "second_hand":
            user_data[user_id]["step"] = "has_description"
            await query.message.reply_text(
                "آیا توضیحات اضافی مایل هستید در اختیار اعضای گروه قرار دهید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_description_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_description_no")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        elif current_step == "confirm_order_spot":
            user_data[user_id]["step"] = "confirm_quantity"
            unit = user_data[user_id]["unit"]
            quantity_words = words_float(user_data[user_id]["quantity"])
            await query.message.reply_text(
                f"مقدار: {user_data[user_id]['quantity']} ({quantity_words} {unit})\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👍 بله", callback_data="confirm_quantity"),
                     InlineKeyboardButton("👎 خیر", callback_data="retry_quantity")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
            )
        elif current_step == "has_invoice":
            user_data[user_id]["step"] = "check_user"
            user_info = get_user_from_db(user_id)
            if user_info:
                await query.message.reply_text(
                    f"👤 خانم/آقای {user_info['first_name']} {user_info['last_name']} با شماره تماس {user_info['phone']}، آیا تأیید می‌کنید؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                         InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                         [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
            else:
                await query.message.reply_text("👋 خوش آمدید!")
                user_data[user_id]["step"] = "name"
                user_data[user_id]["is_new_user"] = True
                await query.message.reply_text(
                    "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                )
    elif query.data in ["confirm_photo_yes", "confirm_photo_no"]:
        current_step = user_data[user_id]["step"]
        logger.info(f"Processing confirm_photo for user {user_id}, step: {current_step}, callback: {query.data}")
        try:
            if query.data == "confirm_photo_yes":
                if current_step == "confirm_first_photo":
                    logger.info(f"User {user_id} confirmed first photo, setting step to 'upload_second_photo'")
                    await query.message.reply_text(
                        "عکس اول شما تایید شد ✅\nلطفا عکس دوم از دارایی خود را از زاویه دیگر ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                            InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                        ])
                    )
                    user_data[user_id]["step"] = "upload_second_photo"
                elif current_step == "confirm_second_photo":
                    logger.info(f"User {user_id} confirmed second photo, setting step to 'price'")
                    await query.message.reply_text("عکس دوم شما تایید شد ✅")  # بدون دکمه
                    await asyncio.sleep(0.5)
                    user_data[user_id]["step"] = "price"
                    await query.message.reply_text(
                        f"لطفاً قیمت فروش هر گرم را تعیین کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                            InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                        ])
                    )
                    logger.info(f"Sent price request for user {user_id}")
            elif query.data == "confirm_photo_no":
                if current_step == "confirm_first_photo":
                    user_data[user_id]["step"] = "upload_first_photo"
                    user_data[user_id]["photo_ids"] = []
                    await query.message.reply_text(
                        "لطفا اولین عکس از دارایی خود را ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                            InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                        ])
                    )
                elif current_step == "confirm_second_photo":
                    user_data[user_id]["step"] = "upload_second_photo"
                    user_data[user_id]["photo_ids"] = user_data[user_id]["photo_ids"][:1]
                    await query.message.reply_text(
                        "لطفا عکس دوم از دارایی خود را از زاویه دیگر ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                            InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                        ])
                    )
        except Exception as e:
            logger.error(f"Error processing confirm_photo for user {user_id}: {e}")
            await query.message.reply_text("🚫 خطایی رخ داد. لطفاً دوباره امتحان کنید.")                       
    elif query.data == "has_description_yes":
        user_data[user_id]["step"] = "description"
        await query.message.reply_text(
            "لطفا توضیحات خود بنویسید (حداکثر 200 کاراکتر)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                 InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    elif query.data == "has_description_no":
        user_data[user_id]["step"] = "confirm_order"
        gross_amount = user_data[user_id]["quantity"] * user_data[user_id]["price"]
        commission = gross_amount * COMMISSION_RATE
        net_amount = gross_amount - commission
        full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
        confirmation_text = (
            f"👤 خانم/آقای {full_name}\n"
            f"🛍 دارایی: {user_data[user_id]['asset']}\n"
            f"⚖️ گرم: {user_data[user_id]['quantity']}\n"
            f"💸 مقدار ناخالص دریافتی: {format_number(gross_amount)} تومان\n"
            f"🧾 هزینه کارمزد: {format_number(commission)} تومان\n"
            f"💰 مقدار خالص دریافتی (پس از اعمال کارمزد): {format_number(net_amount)} تومان\n"
            f"❓ آیا از ثبت سفارش مطمئن هستید؟"
        )
        await query.message.reply_text(
            confirmation_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("بله، تایید می کنم 👍", callback_data="confirm_order_final")],
                [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                 InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    elif user_data.get(user_id, {}).get("step") == "transaction_type" and query.data in ["buy", "sell"]:
        user_data[user_id]["transaction_type"] = "خرید" if query.data == "buy" else "فروش"
        user_data[user_id]["step"] = "asset"
        asset_list = VIP_ASSETS if user_data[user_id].get("is_vip") == "yes" else REGULAR_ASSETS
        keyboard = [
            [InlineKeyboardButton(f"🪙 {asset}" if asset in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی"] else f"🟡 {asset}" if asset == "طلای 18 عیار" else f"💵 {asset}" if asset == "دلار" else f"💶 {asset}" if asset == "یورو" else f"🪙 {asset}", callback_data=asset)]
            for asset in asset_list
        ]
        await query.message.reply_text(
            "نوع دارایی را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard + [
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif user_data.get(user_id, {}).get("step") == "asset" and query.data in ASSETS:
        user_data[user_id]["asset"] = query.data
        user_data[user_id]["step"] = "quantity"
        unit = "تعداد" if query.data in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "دلار", "یورو", "دلار تتر"] else "گرم"
        user_data[user_id]["unit"] = unit
        await query.message.reply_text(
            f"لطفاً {unit} خود را وارد کنید (فقط عدد مورد نظر را وارد کنید):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif user_data.get(user_id, {}).get("step") == "confirm_quantity" and query.data == "retry_quantity":
        unit = "تعداد" if user_data[user_id]["asset"] in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "دلار", "یورو", "دلار تتر"] else "گرم"
        user_data[user_id]["step"] = "quantity"
        await query.message.reply_text(
            f"لطفاً {unit} خود را وارد کنید (فقط عدد مورد نظر را وارد کنید):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif user_data.get(user_id, {}).get("step") == "confirm_quantity" and query.data == "confirm_quantity":
        if user_data[user_id]["order_type"] == "limit":
            user_data[user_id]["step"] = "price"
            transaction_type = user_data[user_id]["transaction_type"]
            await query.message.reply_text(
                f"لطفاً قیمت {transaction_type} هر واحد را وارد کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                    InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif user_data[user_id]["order_type"] == "second_hand":
            user_data[user_id]["step"] = "upload_first_photo"
            await query.message.reply_text(
                "لطفا اولین عکس از دارایی خود را ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                    InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )                      
        else:
            user_data[user_id]["step"] = "confirm_order_spot"
            df = get_prices_from_db("spot_prices")
            price = df[df["asset"] == user_data[user_id]["asset"]][
                "buy_price" if user_data[user_id]["transaction_type"] == "خرید" else "sell_price"
            ].iloc[0]
            user_data[user_id]["price"] = price
            amount = user_data[user_id]["quantity"] * price
            payment_label = "پرداختی" if user_data[user_id]["transaction_type"] == "خرید" else "دریافتی"
            unit = user_data[user_id]["unit"]
            full_name = f"{user_data[user_id].get('name', 'نام')} {user_data[user_id].get('last_name', 'نام خانوادگی')}"
            confirmation_text = (
                f"👤 خانم/آقای {full_name}\n"
                f"💰 دارایی: {user_data[user_id]['asset']}\n"
                f"🔢 {unit}: {user_data[user_id]['quantity']}\n"
                f"💵 مقدار {payment_label}: {format_number(amount)} تومان\n"
                f"❓ آیا از ثبت سفارش مطمئن هستید؟"
            )
            await query.message.reply_text(
                confirmation_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👍 بله، تأیید می‌کنم", callback_data="confirm_order_spot_final"),
                     InlineKeyboardButton("🚫 لغو سفارش", callback_data="cancel_order")],
                     [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
    elif user_data.get(user_id, {}).get("step") == "confirm_price" and query.data == "retry_price":
        user_data[user_id]["step"] = "price"
        transaction_type = user_data[user_id]["transaction_type"]
        await query.message.reply_text(
            f"لطفاً قیمت {transaction_type} هر واحد را دوباره وارد کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )               
    elif user_data.get(user_id, {}).get("step") == "confirm_price" and query.data == "confirm_price":
        if user_data[user_id]["order_type"] == "limit":
            user_data[user_id]["step"] = "expiry"
            await query.message.reply_text(
                f"لطفاً زمان انقضای سفارش را وارد کنید (مثل '2 ساعت' یا '1 روز'):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif user_data[user_id]["order_type"] == "second_hand":
            user_data[user_id]["step"] = "has_description"
            await query.message.reply_text(
                "آیا توضیحات اضافی مایل هستید در اختیار اعضای گروه قرار دهید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_description_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_description_no")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
    elif user_data.get(user_id, {}).get("step") == "confirm_order" and query.data == "confirm_order_final":
        if user_data[user_id]["order_type"] == "limit":
            await send_limit_order_messages(query, context, user_id)
        elif user_data[user_id]["order_type"] == "second_hand":
            if "expiry_time" not in user_data[user_id]:
                user_data[user_id]["expiry_time"] = int(time.time()) + 604800
            await send_limit_order_messages(query, context, user_id)
    elif user_data.get(user_id, {}).get("step") == "confirm_order_spot" and query.data == "confirm_order_spot_final":
            if user_data[user_id]["order_type"] == "spot":
                if user_data[user_id].get("is_new_user", False):
                    if not save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                        await query.message.reply_text("🚫 خطا در ذخیره اطلاعات کاربر. لطفاً دوباره امتحان کنید.")
                        return
                # ست expiry_time برای جلوگیری از حذف فوری (یک سال آینده)
                user_data[user_id]["expiry_time"] = int(time.time()) + 31536000  # 1 سال
                order_id = save_pending_order(user_id, order_type='spot')
                if not order_id:
                    await query.message.reply_text("🚫 خطا در ذخیره سفارش. لطفاً دوباره امتحان کنید.")
                    return
                unit = user_data[user_id]["unit"]
                message_text = (
                    f"‼️ سفارش فوری {user_data[user_id]['transaction_type']} ثبت شد\n"
                    f"👤 نام: {user_data[user_id]['name']} {user_data[user_id]['last_name']}\n"
                    f"📞 شماره تماس: {user_data[user_id]['phone']}\n"
                    f"💰 دارایی: {user_data[user_id]['asset']}\n"
                    f"🔢 {unit}: {user_data[user_id]['quantity']} ({words_float(user_data[user_id]['quantity'])})\n"
                    f"💵 قیمت هر واحد: {format_number(user_data[user_id]['price'])} تومان\n"
                )
                try:
                    # ارسال پیام سفارش بدون دکمه به saved
                    saved_message = await context.bot.send_message(
                        chat_id=SAVED_MESSAGES_ID,
                        text=message_text
                    )
                    saved_message_id = saved_message.message_id
                    
                    # ارسال ریپلای با دکمه به saved
                    complete_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تکمیل سفارش", callback_data=f"complete_order_{order_id}")]])
                    saved_button_message = await context.bot.send_message(
                        chat_id=SAVED_MESSAGES_ID,
                        text="برای تکمیل سفارش کلیک کنید 👇",
                        reply_to_message_id=saved_message_id,
                        reply_markup=complete_keyboard
                    )
                    saved_button_message_id = saved_button_message.message_id
                    
                    # به‌روزرسانی دیتابیس
                    update_pending_order_message_ids(order_id, saved_message_id=saved_message_id, saved_button_message_id=saved_button_message_id)
                    
                    # ارسال پیام سفارش بدون دکمه به کاربر
                    user_message = await context.bot.send_message(
                        chat_id=user_id,
                        text=message_text
                    )
                    user_message_id = user_message.message_id
                    
                    # ارسال ریپلای با دکمه حذف به کاربر
                    user_button_message = await context.bot.send_message(
                        chat_id=user_id,
                        text="برای حذف سفارش کلیک کنید 👇",
                        reply_to_message_id=user_message_id,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("حذف سفارش ❌", callback_data=f"delete_order_{order_id}")]])
                    )
                    user_button_message_id = user_button_message.message_id
                    
                    # به‌روزرسانی دیتابیس برای کاربر
                    update_pending_order_message_ids(order_id, user_message_id=user_message_id, user_button_message_id=user_button_message_id)
                    
                    logger.info(f"Saved Messages message sent for spot order user {user_id}, order_id: {order_id}, message_id: {saved_message_id}")
                    await query.message.reply_text("✅ سفارش شما با موفقیت ثبت شد. لطفاً منتظر تماس ادمین باشید! ✅")
                    if user_id in user_data:
                        del user_data[user_id]
                except Exception as e:
                    logger.error(f"Error sending spot order for user {user_id}: {e}")
                    await query.message.reply_text(f"🚫 خطا در ارسال سفارش: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10), retry=retry_if_exception_type(TimedOut))
async def send_limit_order_messages(query: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    async with message_lock:
        logger.info(f"Starting send_limit_order_messages for user {user_id}")
        expiry_time = user_data[user_id]["expiry_time"]
        expiry_jalali = jdatetime.datetime.fromtimestamp(expiry_time).strftime('%Y/%m/%d %H:%M:%S')
        unit = user_data[user_id]["unit"]
        transaction_type = user_data[user_id]["transaction_type"]
        asset_type = user_data[user_id]["asset_type"]
        asset = user_data[user_id]["asset"]
        is_vip = user_data[user_id].get("is_vip") == "yes"
        is_special_asset = asset in ["دلار", "یورو", "دلار تتر"]
        is_private_order = is_vip and is_special_asset

        # آماده‌سازی متن پیام‌ها
        if asset_type == 'standard':
            message_text = (
                f"📥 سفارش {transaction_type} ثبت شد\n"
                f"💰 دارایی: {user_data[user_id]['asset']}\n"
                f"🔢 {unit}: {user_data[user_id]['quantity']} ({words_float(user_data[user_id]['quantity'])})\n"
                f"💵 قیمت هر واحد: {format_number(user_data[user_id]['price'])} تومان\n"
                f"⏳ انقضا: تا {expiry_jalali}"
            )
            saved_message_text = (
                f"📥 سفارش {transaction_type} ثبت شد\n"
                f"👤 نام: {user_data[user_id]['name']} {user_data[user_id]['last_name']}\n"
                f"📞 شماره تماس: {user_data[user_id]['phone']}\n"
                f"💰 دارایی: {user_data[user_id]['asset']}\n"
                f"🔢 {unit}: {user_data[user_id]['quantity']} ({words_float(user_data[user_id]['quantity'])})\n"
                f"💵 قیمت هر واحد: {format_number(user_data[user_id]['price'])} تومان\n"
                f"⏳ انقضا: تا {expiry_jalali}"
            )
            group_message_text = message_text
            respond_button_text = "فروشنده ام 🔴" if transaction_type == "خرید" else "خریدارم 🟢"
            respond_action = "sell" if transaction_type == "خرید" else "buy"
            message_id_increment = 2
        else:
            message_text = (
                f"🛍 سفارش فروش طلای دسته دوم ثبت شد\n"
                f"💰 فروش {user_data[user_id]['asset']}\n"
                f"⚖️ گرم: {user_data[user_id]['quantity']}\n"
                f"💵 قیمت هر واحد: {format_number(user_data[user_id]['price'])} تومان\n"
                f"⏳ انقضا: تا {expiry_jalali}"
            )
            if user_data[user_id].get("description"):
                message_text += f"\n📝 توضیحات سفارش گذار: {user_data[user_id]['description']}"
            saved_message_text = (
                f"🛍 سفارش فروش طلای دسته دوم ثبت شد\n"
                f"👤 نام: {user_data[user_id]['name']}\n"
                f"👤 نام خانوادگی: {user_data[user_id]['last_name']}\n"
                f"📞 شماره تماس: {user_data[user_id]['phone']}\n"
                f"💰 دارایی: {user_data[user_id]['asset']}\n"
                f"⚖️ گرم: {user_data[user_id]['quantity']}\n"
                f"💵 قیمت هر واحد: {format_number(user_data[user_id]['price'])} تومان\n"
                f"⏳ انقضا: تا {expiry_jalali}"
            )
            if user_data[user_id].get("description"):
                saved_message_text += f"\n📝 توضیحات سفارش گذار: {user_data[user_id]['description']}"
            group_message_text = message_text
            respond_button_text = "خریدارم 🟢"
            respond_action = "buy"
            message_id_increment = 3

        try:
            # ذخیره اطلاعات کاربر و سفارش
            if user_data[user_id].get("is_new_user", False):
                if not save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                    await context.bot.send_message(chat_id=user_id, text="🚫 خطا در ذخیره اطلاعات کاربر. لطفاً دوباره امتحان کنید.")
                    logger.error(f"Failed to save user data for user {user_id}")
                    return
            order_id = save_pending_order(user_id)
            if not order_id:
                await context.bot.send_message(chat_id=user_id, text="🚫 خطا در ذخیره سفارش. لطفاً دوباره امتحان کنید.")
                logger.error(f"Failed to save pending order for user {user_id}")
                return

            # تعریف کیبورد برای دکمه‌ها
            respond_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(respond_button_text, url=f"https://t.me/{SECOND_BOT_USERNAME}?start={respond_action}_{order_id}")]])
            complete_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تکمیل سفارش", callback_data=f"complete_order_{order_id}")]])
            # ارسال به Saved Messages
            try:
                saved_message_id, saved_message_ids = await send_media_with_cleanup(
                    chat_id=SAVED_MESSAGES_ID,
                    photo_ids=user_data[user_id].get("photo_ids", []) if asset_type == "second_hand" else None,
                    text=saved_message_text,
                    is_saved=True,
                    context=context,
                    asset_type=asset_type,
                    complete_keyboard=complete_keyboard
                )
                await asyncio.sleep(0.1)  # تأخیر کاهش‌یافته
                saved_button_message = await context.bot.send_message(
                    chat_id=SAVED_MESSAGES_ID,
                    text="برای تکمیل کلیک کنید 👇",
                    reply_to_message_id=saved_message_id,
                    reply_markup=complete_keyboard
                )
                saved_button_message_id = saved_button_message.message_id
            except Exception as e:
                logger.error(f"Failed to send to Saved Messages for user {user_id}: {e}")
                await context.bot.send_message(chat_id=user_id, text="🚫 خطا در ارسال به Saved Messages. لطفاً با پشتیبانی تماس بگیرید.")
                return

            if is_private_order:
                group_message_id = None
                channel_message_id = None
            else:
                # ارسال به گروه
                try:
                    await asyncio.sleep(0.1)  # تأخیر کاهش‌یافته
                    group_message_id, group_message_ids = await send_media_with_cleanup(
                        chat_id=DISCUSSION_GROUP_ID,
                        photo_ids=user_data[user_id].get("photo_ids", []) if asset_type == "second_hand" else None,
                        text=group_message_text,
                        context=context,
                        asset_type=asset_type
                    )
                    group_button_message = await context.bot.send_message(
                        chat_id=DISCUSSION_GROUP_ID,
                        text="برای پاسخ به این سفارش کلیک کنید 👇" if asset_type == "second_hand" else "برای پاسخ به سفارش کلیک کنید:",
                        reply_to_message_id=group_message_id,
                        reply_markup=respond_keyboard
                    )
                    group_button_message_id = group_button_message.message_id
                except Exception as e:
                    logger.error(f"Failed to send to group {DISCUSSION_GROUP_ID}: {e}")
                    await context.bot.send_message(chat_id=user_id, text="🚫 خطا در ارسال به گروه. لطفاً با پشتیبانی تماس بگیرید.")
                    return

                # ارسال به کانال
                try:
                    await asyncio.sleep(0.05)  # تأخیر کاهش‌یافته
                    channel_message_id, channel_message_ids = await send_media_with_cleanup(
                        chat_id=CHANNEL_ID,
                        photo_ids=user_data[user_id].get("photo_ids", []) if asset_type == "second_hand" else None,
                        text=message_text,
                        context=context,
                        asset_type=asset_type
                    )
                    channel_button_message = await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text="برای قیمت‌گذاری کلیک کنید 👇",
                        reply_to_message_id=channel_message_id,
                        reply_markup=respond_keyboard
                    )
                    channel_button_message_id = channel_button_message.message_id
                except Exception as e:
                    logger.error(f"Failed to send to channel {CHANNEL_ID}: {e}")
                    await context.bot.send_message(chat_id=user_id, text="🚫 خطا در ارسال سفارش به کانال. لطفاً دوباره امتحان کنید.")
                    return

                # افزایش آیدی پیام گروه
                adjusted_group_message_id = group_message_id + message_id_increment
                update_pending_order_message_ids(order_id, group_message_id=adjusted_group_message_id)
                # حذف پیام‌های ارسالی توسط ربات از گروه
                try:
                    await asyncio.sleep(0.1)  # تأخیر کاهش‌یافته
                    if asset_type == "second_hand":
                        for msg_id in group_message_ids:
                            await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=msg_id)
                        await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=group_button_message_id)
                    else:
                        await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=group_message_id)
                        await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=group_button_message_id)
                    logger.info(f"Deleted group messages for order_id {order_id}: {group_message_ids}, button_message_id: {group_button_message_id}")
                except Exception as e:
                    logger.error(f"Error deleting group messages for order_id {order_id}: {e}")

            # به‌روزرسانی دیتابیس با آیدی‌های کانال و Saved Messages
            update_pending_order_message_ids(
                order_id,
                channel_message_id=channel_message_id,
                saved_message_id=saved_message_id,
                saved_button_message_id=saved_button_message_id
            )

            # ارسال پیام به کاربر (چت خصوصی)
            try:
                await asyncio.sleep(0.1)  # تأخیر کاهش‌یافته
                user_message_id, user_message_ids = await send_media_with_cleanup(
                    chat_id=user_id,
                    photo_ids=user_data[user_id].get("photo_ids", []) if asset_type == "second_hand" else None,
                    text=message_text,
                    context=context,
                    asset_type=asset_type
                )
                user_button_message = await context.bot.send_message(
                    chat_id=user_id,
                    text="برای حذف سفارش کلیک کنید 👇",
                    reply_to_message_id=user_message_id,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("حذف سفارش ❌", callback_data=f"delete_order_{order_id}")]])
                )
                user_button_message_id = user_button_message.message_id
                # ذخیره user_message_ids به‌عنوان photo_ids برای سفارش‌های طلای دسته دوم
                photo_ids_str = ",".join(str(pid) for pid in user_message_ids) if asset_type == "second_hand" else None
                logger.info(f"Saving message IDs for order {order_id}: user_message_id={user_message_id}, user_button_message_id={user_button_message_id}, photo_ids={photo_ids_str}")
                update_pending_order_message_ids(
                    order_id,
                    user_message_id=user_message_id,
                    user_button_message_id=user_button_message_id,
                    photo_ids=photo_ids_str
                )
                if is_private_order:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="✅ سفارش شما با موفقیت ثبت شد. لطفاً منتظر تماس ادمین باشید! ✅"
                    )
                else:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="✅ سفارش شما با موفقیت ثبت شد و در کانال قرار گرفت! ✅"
                    )
            except Exception as e:
                logger.error(f"Error sending message to user {user_id}: {e}")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ سفارش شما ثبت شد اما خطایی در ارسال پیام تأیید به شما رخ داد. لطفاً با پشتیبانی تماس بگیرید."
                )                
            logger.info(f"Limit order placed by user {user_id}, order_id: {order_id}")
            if user_id in user_data:
                del user_data[user_id]
        except Exception as e:
            logger.error(f"Error sending limit order for user {user_id}: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"🚫 خطا در ارسال سفارش: {e}")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    logger.debug(f"Received message in chat_id={message.chat_id}, from_user={message.from_user.id if message.from_user else None}, forward_from_message_id={message.forward_from_message_id}, message_id={message.message_id}")
    
    # بررسی اینکه پیام در گروه درست باشد
    if message.chat_id != int(DISCUSSION_GROUP_ID):
        logger.debug(f"Ignoring message: chat_id={message.chat_id} does not match DISCUSSION_GROUP_ID={DISCUSSION_GROUP_ID}")
        return
    
    # بررسی اینکه پیام از تلگرام (777000) و فورواردشده باشد
    if not message.from_user or message.from_user.id != 777000 or not message.forward_from_message_id:
        logger.debug(f"Ignoring message: not from Telegram Notify (user={message.from_user.id if message.from_user else None}, forward_from_message_id={message.forward_from_message_id})")
        return
    
    channel_post_id = message.forward_from_message_id
    group_message_id = message.message_id
    logger.info(f"Detected Telegram Notify message: channel_post_id={channel_post_id}, group_message_id={group_message_id}")
    
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT id, asset_type, transaction_type FROM pending_orders WHERE channel_message_id = %s", (channel_post_id,))
        order = cursor.fetchone()
        
        if order:
            order_id, asset_type, transaction_type = order
            cursor.execute(
                "UPDATE pending_orders SET group_message_id = %s WHERE id = %s",
                (group_message_id, order_id)
            )
            db.commit()
            logger.info(f"Updated group_message_id={group_message_id} for order_id={order_id}, channel_message_id={channel_post_id}")
            
            # افزودن دکمه «خریدارم 🟢» برای طلای دست‌دوم یا دکمه مناسب برای سفارشات معمولی
            respond_button_text = "خریدارم 🟢" if asset_type == "second_hand" else ("فروشنده ام 🔴" if transaction_type == "خرید" else "خریدارم 🟢")
            respond_action = "buy" if asset_type == "second_hand" else ("sell" if transaction_type == "خرید" else "buy")
            respond_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(respond_button_text, url=f"https://t.me/{SECOND_BOT_USERNAME}?start={respond_action}_{order_id}")]])
            await context.bot.send_message(
                chat_id=DISCUSSION_GROUP_ID,
                text="برای پاسخ به این سفارش کلیک کنید 👇" if asset_type == "second_hand" else "برای پاسخ به سفارش کلیک کنید:",
                reply_to_message_id=group_message_id,
                reply_markup=respond_keyboard
            )
        else:
            logger.warning(f"No pending order found for channel_message_id={channel_post_id}")
        db.close()
    except mysql.connector.Error as e:
        logger.error(f"Database error updating group_message_id: {e}")
                        
async def delete_expired_orders(context: ContextTypes.DEFAULT_TYPE):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT id, channel_message_id, saved_message_id, group_message_id, asset_type, telegram_id, user_message_id, user_button_message_id, photo_ids FROM pending_orders WHERE expiry_time <= NOW()")
        expired_orders = cursor.fetchall()
        db.close()
        for order in expired_orders:
            order_id, channel_message_id, saved_message_id, group_message_id, asset_type, telegram_id, user_message_id, user_button_message_id, photo_ids = order
            try:
                # حذف از کانال
                if channel_message_id:
                    await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_message_id)
                    if asset_type == "standard":
                        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_message_id + 1)
                    elif asset_type == "second_hand":
                        for msg_id in range(channel_message_id, channel_message_id + 3):
                            try:
                                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                            except Exception as e:
                                logger.error(f"Failed to delete channel message {msg_id}: {e}")

                # حذف از Saved Messages
                if saved_message_id:
                    await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=saved_message_id)
                    if asset_type == "standard":
                        await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=saved_message_id + 1)
                    elif asset_type == "second_hand":
                        for msg_id in range(saved_message_id, saved_message_id + 3):
                            try:
                                await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=msg_id)
                            except Exception as e:
                                logger.error(f"Failed to delete saved message {msg_id}: {e}")

                # حذف از چت خصوصی کاربر
                if telegram_id and user_message_id:
                    await context.bot.delete_message(chat_id=telegram_id, message_id=user_message_id)
                if telegram_id and user_button_message_id:
                    await context.bot.delete_message(chat_id=telegram_id, message_id=user_button_message_id)
                if asset_type == "second_hand" and photo_ids:
                    user_message_ids = [int(pid) for pid in photo_ids.split(",") if pid]
                    for msg_id in user_message_ids:
                        try:
                            await context.bot.delete_message(chat_id=telegram_id, message_id=msg_id)
                        except Exception as e:
                            logger.error(f"Failed to delete additional user message {msg_id}: {e}")

                delete_pending_order(order_id)
                logger.info(f"Deleted expired order {order_id}")
            except Exception as e:
                logger.error(f"Error deleting expired order {order_id}: {e}")
    except mysql.connector.Error as e:
        logger.error(f"Error fetching expired orders: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type
    if chat_type != "private" or user_id not in user_data:
        logger.info(f"Ignoring message from user {user_id} in chat {chat_type}, user_data exists: {user_id in user_data}")
        return
    step = user_data[user_id]["step"]
    logger.info(f"Message received from user {user_id} in step {step}")    
    if step == "name":
        text = update.message.text
        if not re.match(r"^[\u0600-\u06FF\s]+$", text):
            await update.message.reply_text(
                "🚫 لطفاً نام را به زبان فارسی وارد کنید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
            )
            return
        user_data[user_id]["name"] = text
        user_data[user_id]["step"] = "last_name"
        await update.message.reply_text(
            "لطفاً نام خانوادگی خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif step == "last_name":
        text = update.message.text
        if not re.match(r"^[\u0600-\u06FF\s]+$", text):
            await update.message.reply_text(
                "🚫 لطفاً نام خانوادگی را به زبان فارسی وارد کنید:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
            return
        user_data[user_id]["last_name"] = text
        user_data[user_id]["step"] = "phone"
        await update.message.reply_text(
            "لطفاً شماره تماس خود را وارد کنید (لطفاً اعداد را فقط به انگلیسی وارد کنید):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif step == "phone":
        text = update.message.text
        if not re.match(r"^\+?\d{10,12}$", text):
            await update.message.reply_text(
                "🚫 شماره تماس معتبر نیست. لطفاً دوباره وارد کنید (لطفاً اعداد را فقط به انگلیسی وارد کنید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                    InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
            return
        user_data[user_id]["phone"] = text
        user_data[user_id]["step"] = "confirm_user_info"
        await update.message.reply_text(
            f"👤 خانم/آقای {user_data[user_id]['name']} {user_data[user_id]['last_name']} با شماره تماس {text}، آیا تأیید می‌کنید؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                 InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif step == "item_name":
            text = update.message.text
            if not text or not re.match(r"^[\u0600-\u06FF\s\d]+$", text):
                await update.message.reply_text(
                    "🚫 لطفاً نام دارایی را به زبان فارسی وارد کنید (مثلاً گوشواره طلا، دستبند طلا):",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                        InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
])
                )
                return
            user_data[user_id]["asset"] = text
            user_data[user_id]["unit"] = "گرم"
            user_data[user_id]["step"] = "quantity"
            await update.message.reply_text(
                "لطفا مقدار دارایی را وارد کنید (لطفا فقط عدد و بر حسب گرم وارد کنید)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                    InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )        
    elif step == "quantity":
        text = update.message.text
        try:
            quantity = float(text)
            if quantity <= 0:
                raise ValueError
            user_data[user_id]["quantity"] = quantity
            unit = user_data[user_id]["unit"]
            quantity_words = words_float(quantity)
            await update.message.reply_text(
                f"مقدار: {quantity} ({quantity_words} {unit})\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_quantity"),
                     InlineKeyboardButton("خیر 👎", callback_data="retry_quantity")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
            user_data[user_id]["step"] = "confirm_quantity"
        except ValueError:
            await update.message.reply_text(
                f"🚫 لطفاً مقدار دارایی را وارد کنید (فقط عدد وارد کنید):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
    elif step == "upload_first_photo":
        if update.message.photo:
            photos = update.message.photo
            logger.info(f"Received photo for user {user_id} in upload_first_photo, file_id: {photos[-1].file_id}")
            user_data[user_id]["photo_ids"] = [photos[-1].file_id]
            user_data[user_id]["step"] = "confirm_first_photo"
            await update.message.reply_text(
                "آیا از عکس ارسالی خود مطمئن هستید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_photo_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_photo_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            logger.info(f"No photo received for user {user_id} in upload_first_photo")
            await update.message.reply_text(
                "لطفا اولین عکس از دارایی خود را ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
    elif step == "upload_second_photo":
        if update.message.photo:
            photos = update.message.photo
            logger.info(f"Received photo for user {user_id} in upload_second_photo, file_id: {photos[-1].file_id}")
            user_data[user_id]["photo_ids"].append(photos[-1].file_id)
            user_data[user_id]["step"] = "confirm_second_photo"
            await update.message.reply_text(
                "آیا از عکس ارسالی دوم خود مطمئن هستید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_photo_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_photo_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            logger.info(f"No photo received for user {user_id} in upload_second_photo")
            await update.message.reply_text(
                "لطفا عکس دوم از دارایی خود را از زاویه دیگر ارسال کنید (این عکس توسط اعضای گروه قابل مشاهده است.)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"), 
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
    elif step == "price":
        text = update.message.text
        try:
            # بررسی اینکه ورودی فقط شامل اعداد باشد
            if not re.match(r"^\d+$", text):
                raise ValueError
            price = int(text)
            if price <= 0:
                raise ValueError
            user_data[user_id]["price"] = price
            formatted_price = format_number(price)
            await update.message.reply_text(
                f"قیمت واردشده: {formatted_price} تومان\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_price"),
                     InlineKeyboardButton("خیر 👎", callback_data="retry_price")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
            user_data[user_id]["step"] = "confirm_price"
        except ValueError:
            error_message = (
                f"🚫 لطفاً قیمت {user_data[user_id]['transaction_type']} هر واحد را دوباره وارد کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000"
                if user_data[user_id]["order_type"] == "limit"
                else f"🚫 لطفاً قیمت فروش هر گرم را تعیین کنید (لطفاً قیمت را بر اساس تومان و بدون کاما (,) یا اسلش (\\) وارد کنید)\nمثلا برای چهل میلیون فقط بنویسید: 40000000"
            )
            await update.message.reply_text(
                error_message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )                            
    elif step == "expiry":
        text = update.message.text
        duration, error = parse_duration(text)
        if error:
            await update.message.reply_text(
                error,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
            return
        user_data[user_id]["expiry"] = duration
        user_data[user_id]["expiry_time"] = int(time.time()) + duration
        if user_data[user_id]["order_type"] == "second_hand":
            user_data[user_id]["step"] = "has_description"
            await update.message.reply_text(
                "آیا توضیحات اضافی مایل هستید در اختیار اعضای گروه قرار دهید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="has_description_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="has_description_no")],
                     [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
        else:
            user_data[user_id]["step"] = "confirm_order"
            gross_amount = user_data[user_id]["quantity"] * user_data[user_id]["price"]
            commission = gross_amount * COMMISSION_RATE
            payment_label = "پرداختی" if user_data[user_id]["transaction_type"] == "خرید" else "دریافتی"
            net_amount = gross_amount + commission if user_data[user_id]["transaction_type"] == "خرید" else gross_amount - commission
            unit = user_data[user_id]["unit"]
            full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
            confirmation_text = (
                f"👤 خانم/آقای {full_name}\n"
                f"💰 دارایی: {user_data[user_id]['asset']}\n"
                f"🔢 {unit}: {user_data[user_id]['quantity']}\n"
                f"💸 مقدار ناخالص {payment_label}: {format_number(gross_amount)} تومان\n"
                f"🧾 هزینه کارمزد: {format_number(commission)} تومان\n"
                f"💰 مقدار خالص {payment_label} (پس از اعمال کارمزد): {format_number(net_amount)} تومان\n"
                f"❓ آیا از ثبت سفارش مطمئن هستید؟"
            )
            await update.message.reply_text(
                confirmation_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله، تایید می کنم 👍", callback_data="confirm_order_final")],
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
    elif step == "description":
        text = update.message.text
        if len(text) > 200:
            await update.message.reply_text(
                "🚫 توضیحات شما خیلی طولانی است، لطفاً کمی خلاصه‌تر توضیح دهید 🙏 (حداکثر 200 کاراکتر)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                     InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                ])
            )
            return
        user_data[user_id]["description"] = text
        user_data[user_id]["step"] = "confirm_order"
        gross_amount = user_data[user_id]["quantity"] * user_data[user_id]["price"]
        commission = gross_amount * COMMISSION_RATE
        net_amount = gross_amount - commission
        full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
        confirmation_text = (
            f"👤 خانم/آقای {full_name}\n"
            f"🛍 دارایی: {user_data[user_id]['asset']}\n"
            f"⚖️ گرم: {user_data[user_id]['quantity']}\n"
            f"💸 مقدار ناخالص دریافتی: {format_number(gross_amount)} تومان\n"
            f"🧾 هزینه کارمزد: {format_number(commission)} تومان\n"
            f"💰 مقدار خالص دریافتی (پس از اعمال کارمزد): {format_number(net_amount)} تومان\n"
        )
        if user_data[user_id]["description"]:
            confirmation_text += f"📝 توضیحات شما: {user_data[user_id]['description']}\n"
        confirmation_text += "❓ آیا از ثبت سفارش مطمئن هستید؟"
        await update.message.reply_text(
            confirmation_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("بله، تایید می کنم 👍", callback_data="confirm_order_final")],
                [InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question"),
                 InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
            ])
        )
    elif step == "enter_vip_telegram_id":
        text = update.message.text.strip()
        if not re.match(r"^\d+$", text):
            await update.message.reply_text(
                "🚫 لطفاً یک telegram_id معتبر (فقط عدد) وارد کنید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="cancel_vip_registration")]])
            )
            return
        vip_telegram_id = text  # Keep as string, since telegram_id in db might be string
        user_info = get_user_from_db(vip_telegram_id)
        if not user_info:
            await update.message.reply_text(f"🚫 کاربری با telegram_id {vip_telegram_id} در جدول users یافت نشد.")
            return
        if update_user_is_vip(vip_telegram_id, 'yes'):
            await update.message.reply_text(f"✅ کاربر با telegram_id {vip_telegram_id} به VIP ارتقا یافت.")
        else:
            await update.message.reply_text(f"🚫 خطا در ارتقا کاربر با telegram_id {vip_telegram_id}.")
        if user_id in user_data:
            del user_data[user_id]

def main():
    try:
        logger.info("Bot is running...")
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))  # اضافه کردن handler برای /start
        app.add_handler(CommandHandler("prices", prices))
        app.add_handler(CommandHandler("spot", spot))
        app.add_handler(CommandHandler("limit", limit))
        app.add_handler(CommandHandler("secondhand", secondhand))
        app.add_handler(CommandHandler("addvip", addvip))
        app.add_handler(CallbackQueryHandler(handle_button))
        app.job_queue.run_repeating(delete_expired_orders, interval=60, first=10)
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
        logger.info("Bot is launched successfully. Start polling...")
        app.run_polling(timeout=120)
    except Exception as e:
        logger.error(f"Error in launching bot: {e}", exc_info=True)        
if __name__ == "__main__":
    main()