# Telegram Trading & Order Management Bot

An automated system for managing buy/sell orders (gold, coins, and foreign currency) through Telegram, with a live price feed and a MySQL backend.

## Overview

This project is composed of three services that work together:

- **`update_price.py`** — Fetches live gold, coin, and currency prices from an external market-data API every 5 minutes, calculates buy/sell spreads for both spot and limit order types, and stores the results in MySQL.
- **`BOT1.py`** — The main customer-facing Telegram bot. Handles user registration, price inquiries, order creation (limit orders, spot orders, and second-hand asset listings), photo/document collection for order verification, VIP user management, commission calculation, order expiry, and posting order summaries to a Telegram channel and discussion group.
- **`BOT2.py`** — A companion bot used for limit-order management on the same order database.

## Features

- Real-time price synchronization from an external market API into MySQL
- Multi-step conversational order flow with inline keyboards
- Automatic commission calculation on transactions
- Photo compression and image handling for order listings
- Persian number formatting and RTL text rendering for generated content
- VIP user tiers with admin-only promotion commands
- Automatic expiry and cleanup of pending orders
- Structured logging throughout all services

## Tech Stack

- Python 3
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- MySQL (via `mysql-connector-python`)
- pandas
- Pillow (image processing)
- python-dotenv (configuration management)

## Project Structure

```
.
├── BOT1.py               # Main Telegram bot (orders, users, VIP management)
├── BOT2.py                # Secondary bot (limit-order management)
├── update_price.py        # Scheduled price-fetching service
├── requirements.txt        # Python dependencies
├── .env.example            # Template for required environment variables
└── assets/                 # Fonts / images used for generated content (not tracked)
```

## Configuration

All credentials and environment-specific values (database password, bot tokens, channel/group IDs, API keys) are loaded from environment variables — nothing is hardcoded in the source.

1. Copy `.env.example` to `.env`
2. Fill in your own values (database credentials, Telegram bot tokens from [@BotFather](https://t.me/BotFather), channel/group IDs, and market-data API key)

## Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd <repository-name>

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# edit .env with your own values

# 5. Run the services
python update_price.py
python BOT1.py
python BOT2.py
```

## Database

Both bots and the price service connect to MySQL. `update_price.py` automatically creates its own tables (`limit_prices`, `spot_prices`) on first run. The order and user tables (`orders`, `pending_orders`, `users`) are expected to already exist in the `orders_db` database.

## License

This project is provided for portfolio purposes.
