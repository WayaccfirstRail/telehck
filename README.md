# Telegram Proxy Tool

A stealthy Telegram bot for message relaying and user info demos. For educational/research use only.

## Setup
1. Create bot via @BotFather, snag token.
2. Get your user ID via @userinfobot.
3. Copy .env.example to .env, fill in.
4. pip install -r requirements.txt
5. python main.py

## Usage
- /start: Dashboard buttons (Message, Replies, Info).
- Message: Target → Text → Send raw.
- Replies: Hub for active threads, view logs.
- Info: Dump public intel on target.

Threads persist in JSON, photos download to cwd. Respect ToS/privacy—don't be a dick.

Test on burners. Logs to console.
