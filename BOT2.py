import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import mysql.connector
from num2fawords import words
import re
import os
from dotenv import load_dotenv

load_dotenv()

# Lock
message_lock = asyncio.Lock()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

ORDERS_DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("ORDERS_DB_NAME", "orders_db")
}
TOKEN = os.getenv("BOT2_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DISCUSSION_GROUP_ID = os.getenv("DISCUSSION_GROUP_ID")
SAVED_MESSAGES_ID = os.getenv("SAVED_MESSAGES_ID")
BOT_ID = 0
COMMISSION_RATE = 0.0025

user_data = {}

def format_number(number):
    try:
        return f"{int(number):,}"
    except ValueError:
        return number
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
        logger.error(f"Error saving user: {e}")
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
        logger.error(f"Error fetching user: {e}")
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
def save_proposal(pending_order_id, price, proposer_telegram_id, order_message_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO proposals (pending_order_id, price, proposer_telegram_id, order_message_id) VALUES (%s, %s, %s, %s)",
            (pending_order_id, price, proposer_telegram_id, order_message_id)
        )
        proposal_id = cursor.lastrowid
        db.commit()
        db.close()
        logger.info(f"Proposal saved: id={proposal_id}, order_id={pending_order_id}, proposer_telegram_id={proposer_telegram_id}")
        return proposal_id
    except mysql.connector.Error as e:
        logger.error(f"Error saving proposal: {e}")
        return None
    
def update_proposal_comment_id(proposal_id, comment_message_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("UPDATE proposals SET comment_message_id = %s WHERE id = %s", (comment_message_id, proposal_id))
        db.commit()
        db.close()
        logger.info(f"Updated comment_message_id={comment_message_id} for proposal_id={proposal_id}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error updating proposal comment id: {e}")
        return False
    
def get_proposal(proposal_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT * FROM proposals WHERE id = %s", (proposal_id,))
        result = cursor.fetchone()
        db.close()
        if result:
            columns = ['id', 'pending_order_id', 'price', 'proposer_telegram_id', 'comment_message_id', 'order_message_id', 'created_at']
            return dict(zip(columns, result))
        return None
    except mysql.connector.Error as e:
        logger.error(f"Error fetching proposal: {e}")
        return None
def get_proposals_for_order(pending_order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT comment_message_id FROM proposals WHERE pending_order_id = %s", (pending_order_id,))
        results = cursor.fetchall()
        db.close()
        return [r[0] for r in results if r[0]]
    except mysql.connector.Error as e:
        logger.error(f"Error fetching proposals for order: {e}")
        return []
    
def get_pending_order(order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("SELECT * FROM pending_orders WHERE id = %s", (order_id,))
        result = cursor.fetchone()
        db.close()
        if result:
            columns = [
                'id', 'first_name', 'last_name', 'phone', 'transaction_type', 
                'asset', 'quantity', 'unit', 'price', 'expiry_time', 
                'order_type', 'created_at', 'telegram_id', 'channel_message_id', 
                'saved_message_id', 'saved_button_message_id', 'group_message_id', 
                'asset_type', 'description', 'photo_ids', 'has_invoice', 
                'user_message_id', 'user_button_message_id'
            ]
            order = dict(zip(columns, result))
            logger.info(f"Fetched pending order {order_id}: group_message_id={order['group_message_id']}, saved_button_message_id={order['saved_button_message_id']}, user_message_id={order['user_message_id']}, user_button_message_id={order['user_button_message_id']}")
            if order["photo_ids"]:
                order["photo_ids"] = order["photo_ids"].split(',')
            return order
        logger.warning(f"No pending order found for order_id={order_id}")
        return None
    except mysql.connector.Error as e:
        logger.error(f"Error fetching pending order: {e}")
        return None
        
def delete_pending_order(order_id):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("DELETE FROM proposals WHERE pending_order_id = %s", (order_id,))
        cursor.execute("DELETE FROM pending_orders WHERE id = %s", (order_id,))
        db.commit()
        db.close()
        logger.info(f"Deleted pending order {order_id} and its proposals")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error deleting pending order: {e}")
        return False
    
def save_order_to_db(pending_order, price):
    try:
        db = mysql.connector.connect(**ORDERS_DB_CONFIG)
        cursor = db.cursor()
        photo_ids = ','.join(pending_order["photo_ids"]) if pending_order["photo_ids"] else None
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
                price,
                pending_order["expiry_time"],
                pending_order["order_type"],
                pending_order["telegram_id"],
                pending_order.get("asset_type", 'standard'),
                pending_order.get("description"),
                photo_ids,
                pending_order.get("has_invoice")
            )
        )
        db.commit()
        db.close()
        logger.info(f"Saved order to database for pending_order_id={pending_order['id']}, price={price}")
        return True
    except mysql.connector.Error as e:
        logger.error(f"Error saving order: {e}")
        return False
        
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    logger.info(f"Start command received from user {user_id}, args: {args}")
    if args:
        param = args[0]
        if param.startswith("buy_") or param.startswith("sell_"):
            action, order_id_str = param.split("_")
            try:
                order_id = int(order_id_str)
            except ValueError:
                await update.message.reply_text("🚫 شناسه سفارش نامعتبر است.")
                return
            pending_order = get_pending_order(order_id)
            if not pending_order:
                await update.message.reply_text("🚫 سفارش یافت نشد یا منقضی شده است.")
                return
            if (action == "buy" and pending_order["transaction_type"] != "فروش") or (action == "sell" and pending_order["transaction_type"] != "خرید"):
                await update.message.reply_text("🚫 نوع معامله سازگار نیست.")
                return
            user_data[user_id] = {"step": "check_user", "order_id": order_id, "action": action, "is_proposal": True, "is_second_hand": pending_order["asset_type"] == "second_hand"}
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
                # حذف پیام خوش‌آمدگویی
                user_data[user_id]["step"] = "name"
                user_data[user_id]["is_new_user"] = True
                await update.message.reply_text(
                    "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                    )
            return
    else:
        user_info = get_user_from_db(user_id)
        if user_info:
            # پیام جدید برای کاربران موجود
            await update.message.reply_text(
                f"خانم/آقای {user_info['first_name']} {user_info['last_name']}\n"
                f"برای استفاده از این ربات باید از کانال روی دکمه (خریدارم 🟢) یا (فروشنده ام 🔴) کلیک کنید.\n"
                f"پر سود باشید🌹"
            )
        else:
            # حذف پیام خوش‌آمدگویی برای کاربران جدید
            user_data[user_id] = {"step": "name", "is_new_user": True, "is_direct_start": True}
            await update.message.reply_text(
                "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
            )
            
async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Failed to answer callback query: {e}")
    data = query.data
    logger.info(f"Button clicked by user {user_id}: {data}")
    
    if data == "cancel_order" or data == "cancel_request":
        await query.message.reply_text("درخواست شما با موفقیت لغو شد ✅")
        if user_id in user_data:
            del user_data[user_id]
        return
        
    elif data.startswith("accept_"):
        proposal_id = int(data.split("_")[1])
        proposal = get_proposal(proposal_id)
        if not proposal:
            await context.bot.send_message(user_id, "🚫 پیشنهاد یافت نشد.")
            return
        pending_order = get_pending_order(proposal["pending_order_id"])
        if not pending_order:
            await context.bot.send_message(user_id, "🚫 سفارش یافت نشد.")
            return
        if int(user_id) != pending_order["telegram_id"]:
            logger.info(f"User ID mismatch: user_id={user_id}, pending_order_telegram_id={pending_order['telegram_id']}")
            await context.bot.send_message(user_id, "🚫 شما سفارش‌گذار نیستید.")
            return
        
        group_thread_id = pending_order["group_message_id"]
        if group_thread_id:
            try:
                await context.bot.send_message(
                    chat_id=DISCUSSION_GROUP_ID,
                    message_thread_id=group_thread_id,
                    reply_to_message_id=group_thread_id,
                    text="💯 معامله انجام شد"
                )
                logger.info(f"Sent 'Transaction completed' message to group for order {pending_order['id']}")
            except Exception as e:
                logger.error(f"Error sending transaction message to group: {e}")
        
        try:
            if pending_order["channel_message_id"]:
                if pending_order["asset_type"] == "second_hand" and pending_order["photo_ids"]:
                    for i in range(len(pending_order["photo_ids"])):
                        try:
                            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=pending_order["channel_message_id"] + i)
                            logger.info(f"Deleted channel message {pending_order['channel_message_id'] + i} for order {pending_order['id']}")
                        except Exception as e:
                            logger.warning(f"Failed to delete channel message {pending_order['channel_message_id'] + i}: {e}")
                    try:
                        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=pending_order["channel_message_id"] + len(pending_order["photo_ids"]))
                        logger.info(f"Deleted channel button message {pending_order['channel_message_id'] + len(pending_order['photo_ids'])} for order {pending_order['id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete channel button message {pending_order['channel_message_id'] + len(pending_order['photo_ids'])}: {e}")
                else:
                    try:
                        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=pending_order["channel_message_id"])
                        logger.info(f"Deleted channel message {pending_order['channel_message_id']} for order {pending_order['id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete channel message {pending_order['channel_message_id']}: {e}")
                    try:
                        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=pending_order["channel_message_id"] + 1)
                        logger.info(f"Deleted channel button message {pending_order['channel_message_id'] + 1} for order {pending_order['id']}")
                    except Exception as e:
                        logger.warning(f"Failed to delete channel button message {pending_order['channel_message_id'] + 1}: {e}")
            if pending_order["saved_message_id"]:
                try:
                    await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=pending_order["saved_message_id"])
                    logger.info(f"Deleted saved message {pending_order['saved_message_id']} for order {pending_order['id']}")
                except Exception as e:
                    logger.warning(f"Failed to delete saved message {pending_order['saved_message_id']}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during message deletion for order {pending_order['id']}: {e}")
        
        try:
            await context.bot.send_message(
                chat_id=pending_order["telegram_id"],
                text=f"سفارش شما در قیمت {format_number(proposal['price'])} نهایی شد، لطفا منتظر تماس ادمین باشید🌹\nتوجه شود این قیمت بدون هزینه کارمزد ({COMMISSION_RATE*100:.2f}%) لحاظ شده است."
            )
            await context.bot.send_message(
                chat_id=proposal["proposer_telegram_id"],
                text=f"سفارش‌گذار پیشنهاد قیمتی شما را تایید کرد، لطفا منتظر تماس ادمین باشید🌹\nتوجه شود این قیمت بدون هزینه کارمزد ({COMMISSION_RATE*100:.2f}%) لحاظ شده است."
            )
        except Exception as e:
            logger.error(f"Error sending notifications for order {pending_order['id']}: {e}")
            await context.bot.send_message(user_id, f"🚫 خطا در ارسال اعلان‌ها: {e}")
            return
        
        owner_user = get_user_from_db(pending_order["telegram_id"])
        proposer_user = get_user_from_db(proposal["proposer_telegram_id"])
        if not owner_user or not proposer_user:
            logger.error(f"User data missing for order {pending_order['id']}: owner={owner_user}, proposer={proposer_user}")
            await context.bot.send_message(user_id, "🚫 خطا در بازیابی اطلاعات کاربران.")
            return
        
        if pending_order["transaction_type"] == "خرید":
            buyer_name = f"{owner_user['first_name']} {owner_user['last_name']}"
            buyer_phone = owner_user["phone"]
            seller_name = f"{proposer_user['first_name']} {proposer_user['last_name']}"
            seller_phone = proposer_user["phone"]
        else:
            seller_name = f"{owner_user['first_name']} {owner_user['last_name']}"
            seller_phone = owner_user["phone"]
            buyer_name = f"{proposer_user['first_name']} {proposer_user['last_name']}"
            buyer_phone = proposer_user["phone"]
        
        unit = "تعداد" if pending_order["asset"] in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "دلار", "یورو", "دلار تتر"] else "گرم"
        saved_text = (
            f"💯 معامله انجام شد\n"
            f"💰 نام دارایی: {pending_order['asset']}\n"
            f"🔢 {unit}: {pending_order['quantity']}\n"
            f"💵 قیمت توافقی: {format_number(proposal['price'])} تومان\n"
            f"👤 نام خریدار: {buyer_name}\n"
            f"👤 نام فروشنده: {seller_name}\n"
            f"📞 شماره تماس خریدار: {buyer_phone}\n"
            f"📞 شماره تماس فروشنده: {seller_phone}"
        )
        try:
            await context.bot.send_message(chat_id=SAVED_MESSAGES_ID, text=saved_text)
            logger.info(f"Sent transaction details to SAVED_MESSAGES_ID for order {pending_order['id']}")
        except Exception as e:
            logger.error(f"Error sending transaction details to SAVED_MESSAGES_ID: {e}")
            await context.bot.send_message(user_id, f"🚫 خطا در ارسال اطلاعات معامله: {e}")
            return
        
        order_saved = False
        try:
            if save_order_to_db(pending_order, proposal["price"]):
                order_saved = True
                logger.info(f"Saved order to database for order {pending_order['id']}")
            else:
                logger.error(f"Failed to save order to database for order {pending_order['id']}")
                await context.bot.send_message(user_id, "🚫 خطا در ذخیره معامله.")
        except Exception as e:
            logger.error(f"Error saving order to database for order {pending_order['id']}: {e}")
            await context.bot.send_message(user_id, f"🚫 خطا در ذخیره معامله: {e}")
        
        if order_saved:
            try:
                if not delete_pending_order(pending_order["id"]):
                    logger.error(f"Failed to delete pending order {pending_order['id']}")
                    await context.bot.send_message(user_id, "🚫 خطا در حذف سفارش موقت.")
                else:
                    logger.info(f"Deleted pending order {pending_order['id']} and its proposals")
            except Exception as e:
                logger.error(f"Error deleting pending order {pending_order['id']}: {e}")
                await context.bot.send_message(user_id, f"🚫 خطا در حذف سفارش موقت: {e}")
    
    elif data.startswith("complete_order_"):
        order_id = int(data.split("_")[1])
        pending_order = get_pending_order(order_id)
        if not pending_order:
            await context.bot.send_message(user_id, "🚫 سفارش یافت نشد.")
            return
        if str(user_id) != SAVED_MESSAGES_ID:
            await context.bot.send_message(user_id, "🚫 دسترسی غیرمجاز.")
            return
        try:
            if pending_order["channel_message_id"]:
                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=pending_order["channel_message_id"])
                logger.info(f"Deleted channel message {pending_order['channel_message_id']} for order {order_id}")
            if pending_order["saved_message_id"]:
                await context.bot.send_message(chat_id=SAVED_MESSAGES_ID, text=f"✅ سفارش {order_id} تکمیل و حذف شد!")
                await context.bot.delete_message(chat_id=SAVED_MESSAGES_ID, message_id=pending_order["saved_message_id"])
                logger.info(f"Deleted saved message {pending_order['saved_message_id']} for order {order_id}")
            if pending_order["group_message_id"]:
                await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=pending_order["group_message_id"])
                logger.info(f"Deleted group message {pending_order['group_message_id']} for order {order_id}")
            comment_ids = get_proposals_for_order(order_id)
            for cid in comment_ids:
                await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=cid)
                logger.info(f"Deleted proposal comment {cid} for order {order_id}")
        except Exception as e:
            logger.error(f"Error deleting messages for order {order_id}: {e}")
            await context.bot.send_message(user_id, f"🚫 خطا در حذف پیام‌ها: {e}")
        delete_pending_order(order_id)
        return
    
    if user_id not in user_data:
        return
    step = user_data[user_id].get("step")
    if data == "confirm_user_info_yes":
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
        pending_order = get_pending_order(user_data[user_id]["order_id"])
        transaction = "خرید" if user_data[user_id]["action"] == "buy" else "فروش"
        unit = "تعداد" if pending_order["asset"] in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "دلار", "یورو", "دلار تتر"] else "گرم"
        full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
        confirm_text = (
            f"خانم/آقای {full_name}\n"
            f"آیا از {transaction} {pending_order['asset']} \n"
            f"وزن {pending_order['quantity']} {unit}\n"
            f"قیمت هر واحد {format_number(pending_order['price'])} تومان\n"
            f"مطمئن هستید؟"
        )
        respond_button_text = "بله، در همین قیمت خریدارم 👍" if user_data[user_id]["action"] == "buy" else "بله، در همین قیمت فروشنده ام 👍"
        user_data[user_id]["step"] = "confirm_proposal"
        await query.message.reply_text(
            confirm_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(respond_button_text, callback_data="proposal_same_price"),
                 InlineKeyboardButton("خیر، پیشنهاد قیمت دیگری دارم 👎", callback_data="proposal_other_price")],
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif data == "confirm_user_info_no":
        delete_user_data(user_id)
        # حذف پیام خوش‌آمدگویی
        user_data[user_id]["step"] = "name"
        user_data[user_id]["is_update"] = True
        user_data[user_id]["is_new_user"] = False
        await query.message.reply_text(
            "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
        )
    elif data == "proposal_same_price":
        await handle_proposal(update, context, use_order_price=True)
    elif data == "proposal_other_price":
        user_data[user_id]["step"] = "enter_proposal_price"
        await query.message.reply_text(
            "لطفا قیمت درخواستی خود را وارد کنید (لطفاً عدد را به تومان وارد کنید):\n مثلا برای نوشتن صد و بیست سه هزار و پانصد فقط بنویسید:  123500",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif data == "confirm_proposal_price_yes":
        await handle_proposal(update, context, use_order_price=False)
    elif data == "confirm_proposal_price_no":
        user_data[user_id]["step"] = "enter_proposal_price"
        await query.message.reply_text(
            "لطفا قیمت درخواستی خود را وارد کنید (لطفاً عدد را به تومان وارد کنید):\n مثلا برای نوشتن صد و بیست سه هزار و پانصد فقط بنویسید:  123500",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                 InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
            ])
        )
    elif data == "previous_question":
        current_step = user_data.get(user_id, {}).get("step")
        if current_step == "name":
            user_data[user_id]["step"] = "check_user"
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
                user_data[user_id]["step"] = "name"
                user_data[user_id]["is_new_user"] = True
                await query.message.reply_text(
                    "لطفاً نام خود را وارد کنید (لطفاً به زبان فارسی بنویسید):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]])
                )
        elif current_step == "confirm_proposal":
            user_data[user_id]["step"] = "confirm_user_info"
            full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
            await query.message.reply_text(
                f"👤 خانم/آقای {full_name} با شماره تماس {user_data[user_id]['phone']}، آیا تأیید می‌کنید؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("بله 👍", callback_data="confirm_user_info_yes"),
                     InlineKeyboardButton("خیر 👎", callback_data="confirm_user_info_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "enter_proposal_price":
            user_data[user_id]["step"] = "confirm_proposal"
            pending_order = get_pending_order(user_data[user_id]["order_id"])
            transaction = "خرید" if user_data[user_id]["action"] == "buy" else "فروش"
            unit = "تعداد" if pending_order["asset"] in ["ربع سکه", "نیم سکه", "سکه امامی", "سکه بهار آزادی", "دلار", "یورو", "دلار تتر"] else "گرم"
            full_name = f"{user_data[user_id]['name']} {user_data[user_id]['last_name']}"
            confirm_text = (
                f"خانم/آقای {full_name}\n"
                f"آیا از {transaction} {pending_order['asset']} \n"
                f"وزن {pending_order['quantity']} {unit}\n"
                f"قیمت هر واحد {format_number(pending_order['price'])} تومان\n"
                f"مطمئن هستید؟"
            )
            respond_button_text = "بله، در همین قیمت خریدارم 👍" if user_data[user_id]["action"] == "buy" else "بله، در همین قیمت فروشنده ام 👍"
            await query.message.reply_text(
                confirm_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(respond_button_text, callback_data="proposal_same_price"),
                     InlineKeyboardButton("خیر، پیشنهاد قیمت دیگری دارم 👎", callback_data="proposal_other_price")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        elif current_step == "confirm_proposal_price":
            user_data[user_id]["step"] = "enter_proposal_price"
            await query.message.reply_text(
                "لطفا قیمت درخواستی خود را وارد کنید (لطفاً عدد را به تومان وارد کنید):\n مثلا برای نوشتن صد و بیست سه هزار و پانصد فقط بنویسید:  123500",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        logger.warning(f"Received update with no effective user: {update}")
        return
    user_id = update.effective_user.id
    if user_id not in user_data:
        return
    step = user_data[user_id]["step"]
    logger.info(f"Message received from user {user_id} in step {step}")
    if step == "name":
        text = update.message.text
        if not re.match(r"^[\u0600-\u06FF\s]+$", text):
            await update.message.reply_text("🚫 لطفاً نام را به زبان فارسی وارد کنید:")
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
            await update.message.reply_text("🚫 لطفاً نام خانوادگی را به زبان فارسی وارد کنید:")
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
            await update.message.reply_text("🚫 شماره تماس معتبر نیست. لطفاً دوباره وارد کنید:")
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
        if user_data[user_id].get("is_direct_start"):
            if save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                # پیام جدید برای کاربران جدید پس از ثبت اطلاعات
                await update.message.reply_text(
                    f"خانم/آقای {user_data[user_id]['name']} {user_data[user_id]['last_name']}\n"
                    f"برای استفاده از این ربات باید از کانال روی دکمه (خریدارم 🟢) یا (فروشنده ام 🔴) کلیک کنید.\n"
                    f"پر سود باشید🌹"
                )
            del user_data[user_id]
    elif step == "enter_proposal_price":
        text = update.message.text
        try:
            price = int(text.replace(",", ""))
            if price <= 0:
                raise ValueError
            user_data[user_id]["proposal_price"] = price
            formatted_price = format_number(price)
            user_data[user_id]["step"] = "confirm_proposal_price"
            await update.message.reply_text(
                f"پیشنهاد قیمتی واردشده: {formatted_price} تومان\nدرست است؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👍 بله", callback_data="confirm_proposal_price_yes"),
                     InlineKeyboardButton("👎 خیر", callback_data="confirm_proposal_price_no")],
                    [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order"),
                     InlineKeyboardButton("⬅️ سوال قبلی", callback_data="previous_question")]
                ])
            )
        except ValueError:
            await update.message.reply_text("🚫 لطفاً یک عدد معتبر وارد کنید.")

async def handle_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE, use_order_price: bool):
    user_id = update.effective_user.id
    logger.info(f"Proposal received from user {user_id}, use_order_price={use_order_price}")
    async with message_lock:
        if user_id not in user_data or "order_id" not in user_data[user_id]:
            logger.debug(f"Ignoring proposal from user {user_id}: user_data or order_id missing")
            await context.bot.send_message(user_id, "🚫 خطا: اطلاعات سفارش یافت نشد. لطفاً دوباره شروع کنید.")
            return
        pending_order = get_pending_order(user_data[user_id]["order_id"])
        if not pending_order:
            logger.warning(f"No pending order found for order_id={user_data[user_id]['order_id']}")
            await context.bot.send_message(user_id, "🚫 سفارش یافت نشد.")
            return

        logger.info(f"Processing proposal for order_id={pending_order['id']}, group_message_id={pending_order['group_message_id']}, saved_button_message_id={pending_order['saved_button_message_id']}, channel_message_id={pending_order['channel_message_id']}")

        if use_order_price:
            price = pending_order["price"]
        else:
            if not user_data[user_id].get("proposal_price"):
                logger.error(f"No proposal_price provided for user {user_id}")
                await context.bot.send_message(
                    user_id,
                    "🚫 لطفاً یک عدد معتبر برای قیمت پیشنهاد وارد کنید (فقط عدد به تومان):",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("لغو سفارش 🚫", callback_data="cancel_order")]
                    ])
                )
                return
            price = user_data[user_id]["proposal_price"]

        if user_data[user_id].get("is_new_user", False):
            if not save_user_to_db(user_data[user_id]["name"], user_data[user_id]["last_name"], user_data[user_id]["phone"], user_id):
                logger.error(f"Failed to save user data for user {user_id}")
                await context.bot.send_message(user_id, "🚫 خطا در ذخیره اطلاعات.")
                return

        proposal_id = save_proposal(
            pending_order["id"],
            price,
            user_id,
            pending_order["group_message_id"]
        )
        if not proposal_id:
            logger.error(f"Failed to save proposal for order_id={pending_order['id']}, user_id={user_id}")
            await context.bot.send_message(user_id, "🚫 خطا در ثبت پیشنهاد.")
            return

        group_message_id = pending_order["group_message_id"]
        if not group_message_id:
            logger.error(f"No group_message_id found for order_id={pending_order['id']}, channel_message_id={pending_order['channel_message_id']}")
            await context.bot.send_message(
                user_id,
                f"🚫 خطا در یافتن پیام گروه برای سفارش {pending_order['id']}. لطفاً مطمئن شوید که پیام کانال به گروه فوروارد شده است."
            )
            await context.bot.send_message(
                chat_id=SAVED_MESSAGES_ID,
                text=f"⚠️ خطا: group_message_id برای سفارش {pending_order['id']} وجود ندارد. channel_message_id={pending_order['channel_message_id']}"
            )
            return
        # اعتبارسنجی دسترسی به پیام گروه
        try:
            await context.bot.get_chat(chat_id=DISCUSSION_GROUP_ID)
            # ارسال پیام آزمایشی برای بررسی دسترسی به group_message_id
            test_message = await context.bot.send_message(
                chat_id=DISCUSSION_GROUP_ID,
                reply_to_message_id=group_message_id,
                text="Test message to verify reply",
                disable_notification=True
            )
            await context.bot.delete_message(
                chat_id=DISCUSSION_GROUP_ID,
                message_id=test_message.message_id
            )
            logger.info(f"Validated group_message_id={group_message_id} for order_id={pending_order['id']}")
        except Exception as e:
            logger.error(f"Cannot access group_message_id={group_message_id} for order_id={pending_order['id']}: {e}")
            await context.bot.send_message(
                user_id,
                f"🚫 خطا در دسترسی به پیام گروه برای سفارش {pending_order['id']}. لطفاً با ادمین تماس بگیرید."
            )
            await context.bot.send_message(
                chat_id=SAVED_MESSAGES_ID,
                text=f"⚠️ خطا در دسترسی به group_message_id={group_message_id} برای سفارش {pending_order['id']}: {e}"
            )
            return
        # ارسال پیشنهاد قیمتی به‌صورت ریپلای
        comment_text = f"پیشنهاد قیمت: {format_number(price)} تومان"
        try:
            comment_message = await context.bot.send_message(
                chat_id=DISCUSSION_GROUP_ID,
                reply_to_message_id=group_message_id,
                text=comment_text
            )
            update_proposal_comment_id(proposal_id, comment_message.message_id)
            logger.info(f"Proposal comment sent for order_id={pending_order['id']}, proposal_id={proposal_id}, group_message_id={group_message_id}, comment_message_id={comment_message.message_id}")
        except Exception as e:
            logger.error(f"Error sending comment to group for order_id={pending_order['id']}: {e}")
            await context.bot.send_message(
                user_id,
                f"🚫 خطا در ارسال پیشنهاد به گروه: {e}"
            )
            await context.bot.send_message(
                chat_id=SAVED_MESSAGES_ID,
                text=f"⚠️ خطا در ارسال پیشنهاد به group_message_id={group_message_id} برای سفارش {pending_order['id']}: {e}"
            )
            return

        proposer_type = "فروشنده" if pending_order["transaction_type"] == "خرید" else "خریدار"
        private_message_text = (
            f"💰 نام دارایی: {pending_order['asset']}\n"
            f"💵 قیمت سفارش شما: {format_number(pending_order['price'])} تومان\n"
            f"💸 پیشنهاد قیمتی {proposer_type}: {format_number(price)} تومان"
        )
        try:
            await context.bot.send_message(
                chat_id=pending_order["telegram_id"],
                text=private_message_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("معامله می‌کنم 🤝", callback_data=f"accept_{proposal_id}")]])
            )
            logger.info(f"Sent proposal notification to order owner {pending_order['telegram_id']} for proposal {proposal_id}")
        except Exception as e:
            logger.error(f"Error sending private message to order owner {pending_order['telegram_id']}: {e}")
            await context.bot.send_message(user_id, f"🚫 خطا در ارسال پیام به سفارش‌گذار: {e}")
            return
        await context.bot.send_message(
            user_id,
            "✅ پیشنهاد قیمتی شما با موفقیت در گروه تلگرامی قرار گرفت، لطفاً منتظر قبول سفارش‌گذار باشید ☺️"
        )
        if user_id in user_data:
            del user_data[user_id]
            
async def delete_non_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.from_user and message.from_user.id != BOT_ID and message.from_user.id != 777000:
        try:
            await context.bot.delete_message(chat_id=DISCUSSION_GROUP_ID, message_id=message.message_id)
            logger.info(f"Deleted non-bot message {message.message_id} in group")
        except Exception as e:
            logger.error(f"Error deleting non-bot message {message.message_id}: {e}")
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update.effective_user:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="🚫 خطایی رخ داد. لطفاً دوباره تلاش کنید یا با ادمین تماس بگیرید."
        )

def main():
    try:
        logger.info("Bot is starting...")
        app = Application.builder().token(TOKEN).build()
        bot = app.bot
        loop = asyncio.get_event_loop()
        me = loop.run_until_complete(bot.get_me())
        global BOT_ID
        BOT_ID = me.id
        logger.info(f"Bot ID set to: {BOT_ID}")
        app.add_handler(CommandHandler("start", handle_start))
        app.add_handler(CallbackQueryHandler(handle_button))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_error_handler(error_handler)
        logger.info("Bot is launched successfully. Start polling...")
        app.run_polling(timeout=60)
    except Exception as e:
        logger.error(f"Error in launching bot: {e}", exc_info=True)

if __name__ == "__main__":
    import sys
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()