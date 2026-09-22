import requests
import pandas as pd
import mysql.connector
import time
import schedule
import logging
import os
from dotenv import load_dotenv

load_dotenv()

# log setting for debug
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# تنظیمات
BRSAPI_KEY = os.getenv("BRSAPI_KEY")
API_URL = f"https://Api.BrsApi.ir/Market/Gold_Currency.php?key={BRSAPI_KEY}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/106.0.0.0",
    "Accept": "application/json, text/plain, */*"
}
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("PRICE_DB_NAME", "price_db")
}
LIMIT_BUY_PERCENT = 1.0025  # 0.25% بالاتر برای خرید در limit_prices
LIMIT_SELL_PERCENT = 0.9975  # 0.25% پایین‌تر برای فروش در limit_prices
SPOT_BUY_PERCENT = 1.05    # 5% بالاتر برای خرید در spot_prices
SPOT_SELL_PERCENT = 0.95   # 5% پایین‌تر برای فروش در spot_prices

def get_prices():
    try:
        response = requests.get(API_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info("The data is achieved successfully from API")
        return {
            data["gold"][0]["name"]: float(data["gold"][0]["price"]),
            data["gold"][7]["name"]: float(data["gold"][7]["price"]),
            data["gold"][6]["name"]: float(data["gold"][6]["price"]),
            data["gold"][5]["name"]: float(data["gold"][5]["price"]),
            data["gold"][8]["name"]: float(data["gold"][8]["price"]),
            data["currency"][0]["name"]: float(data["currency"][0]["price"]),
            data["currency"][1]["name"]: float(data["currency"][1]["price"]),
            data["currency"][2]["name"]: float(data["currency"][2]["price"])
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error in fetching data from API: {e}")
        return None

def calculate_buy_sell(prices):
    if prices is None:
        logger.warning("No data available!")
        return None, None
    limit_data = [
        {
            "asset": asset,
            "buy_price": price * LIMIT_BUY_PERCENT,
            "sell_price": price * LIMIT_SELL_PERCENT,
            "updated_at": pd.Timestamp.now()
        }
        for asset, price in prices.items()
    ]
    spot_data = [
        {
            "asset": asset,
            "buy_price": price * SPOT_BUY_PERCENT,
            "sell_price": price * SPOT_SELL_PERCENT,
            "updated_at": pd.Timestamp.now()
        }
        for asset, price in prices.items()
    ]
    limit_df = pd.DataFrame(limit_data)
    spot_df = pd.DataFrame(spot_data)
    logger.info("Calculated limit_prices DataFrame:\n%s", limit_df)
    logger.info("Calculated spot_prices DataFrame:\n%s", spot_df)
    return limit_df, spot_df

def update_database(limit_df, spot_df):
    if (limit_df is None or limit_df.empty) and (spot_df is None or spot_df.empty):
        logger.warning("No DataFrame existence!")
        return
    try:
        db = mysql.connector.connect(**DB_CONFIG)
        cursor = db.cursor()
        
        # حذف داده‌های قدیمی از هر دو جدول
        cursor.execute("DELETE FROM limit_prices")
        cursor.execute("DELETE FROM spot_prices")
        
        # درج داده‌های جدید در limit_prices
        for _, row in limit_df.iterrows():
            cursor.execute(
                """
                INSERT INTO limit_prices (asset, buy_price, sell_price, updated_at)
                VALUES (%s, %s, %s, %s)
                """,
                (row["asset"], row["buy_price"], row["sell_price"], row["updated_at"])
            )
        
        # درج داده‌های جدید در spot_prices
        for _, row in spot_df.iterrows():
            cursor.execute(
                """
                INSERT INTO spot_prices (asset, buy_price, sell_price, updated_at)
                VALUES (%s, %s, %s, %s)
                """,
                (row["asset"], row["buy_price"], row["sell_price"], row["updated_at"])
            )
        
        db.commit()
        logger.info("Database updated successfully: limit_prices and spot_prices")
    except mysql.connector.Error as e:
        logger.error(f"Error in updating database: {e}")
    finally:
        if db.is_connected():
            cursor.close()
            db.close()

def create_tables():
    try:
        db = mysql.connector.connect(**DB_CONFIG)
        cursor = db.cursor()
        # ایجاد جدول limit_prices
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS limit_prices (
                id INT AUTO_INCREMENT PRIMARY KEY,
                asset VARCHAR(50),
                buy_price FLOAT,
                sell_price FLOAT,
                updated_at DATETIME
            )
        """)
        # ایجاد جدول spot_prices
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spot_prices (
                id INT AUTO_INCREMENT PRIMARY KEY,
                asset VARCHAR(50),
                buy_price FLOAT,
                sell_price FLOAT,
                updated_at DATETIME
            )
        """)
        db.commit()
        logger.info("Tables limit_prices and spot_prices created successfully")
    except mysql.connector.Error as e:
        logger.error(f"Error creating tables: {e}")
    finally:
        if db.is_connected():
            cursor.close()
            db.close()

def job():
    prices = get_prices()
    limit_df, spot_df = calculate_buy_sell(prices)
    update_database(limit_df, spot_df)

# ایجاد جداول در دیتابیس
create_tables()

# اجرای اولیه
job()

# اجرای هر ۵ دقیقه
schedule.every(5).minutes.do(job)
while True:
    schedule.run_pending()
    time.sleep(1)