# Delta Exchange India — EMA Cross Alert Bot

A 24/7 Python bot that scans **Delta Exchange India** perpetual/dated futures
and sends Telegram alerts whenever price crosses **EMA 8** or **EMA 20** on
5m, 15m, and 1h timeframes (all configurable).

Each alert includes:
- A dark-themed candlestick chart (last 50 candles) with EMA lines and a signal arrow
- Volume for that candle vs. its recent average, for context
- A market summary (top 15 gainers / losers / volume) sent on a schedule

**Two ways to run this — pick one:**
- **`main.py`** — a traditional always-on process for a VPS/server (Oracle Cloud, a spare PC, etc.) that never sleeps.
- **`run_once.py`** — a single-pass script triggered on a schedule by **GitHub Actions**, free forever, no credit card. See **`README_GITHUB_ACTIONS.md`** — start there if you don't have a card or a server.

**This is a technical-analysis alerting tool, not a trading advisor.** It does
not place orders and does not guarantee profitable signals. EMA crossovers
lag price and produce false signals in choppy/ranging markets — treat alerts
as one input into your own decision-making, not as instructions to trade.

---

## How it works (design note)

Rather than a raw WebSocket tick-by-tick candle builder, this bot **polls the
REST API right after each candle closes** (e.g. 5 seconds after every 5-minute
boundary). This is the more robust choice for something meant to run
unattended 24/7 — a dropped connection just means the next poll retries;
there's no fragile reconnect/resync state to get wrong. Public market-data
endpoints on Delta India (`/v2/products`, `/v2/tickers`, `/v2/history/candles`)
require no API key.

---

## Setup

### 1. Create a Telegram bot
1. Message **@BotFather** on Telegram → `/newbot` → follow the prompts → copy the **bot token**.
2. Start a chat with your new bot (send it any message).
3. Message **@userinfobot** to get your numeric **chat id** (or use `https://api.telegram.org/bot<TOKEN>/getUpdates` after messaging your bot to find `chat.id`).

### 2. Install dependencies
```bash
cd delta_ema_bot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# then edit .env and fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

All other settings in `.env` are optional — sensible defaults match the original spec (EMA 8/20, 5m/15m/1h, 1M min volume, 3-candle anti-spam, hourly summary).

### 4. Run
```bash
python3 main.py
```
You should immediately get a Telegram "bot online" message, then alerts as
signals fire and an hourly market summary.

---

## Running it 24/7

Pick one:

### Option A — tmux/screen (simplest, good for a VPS)
```bash
tmux new -s emabot
cd delta_ema_bot && source venv/bin/activate && python3 main.py
# Ctrl+B then D to detach; `tmux attach -t emabot` to come back
```

### Option B — systemd service (auto-restarts on crash/reboot)
Create `/etc/systemd/system/ema-bot.service`:
```ini
[Unit]
Description=Delta EMA Cross Alert Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/delta_ema_bot
ExecStart=/path/to/delta_ema_bot/venv/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ema-bot
sudo journalctl -u ema-bot -f   # view logs
```

### Option C — Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python3", "main.py"]
```
```bash
docker build -t ema-bot .
docker run -d --restart=always --env-file .env --name ema-bot ema-bot
```

A cheap always-on VPS (e.g. a $5/mo box) or a Docker host is recommended —
this is not meant to run on your laptop that sleeps or a machine that drops
off Wi-Fi.

---

## File overview

| File | Purpose |
|---|---|
| `main.py` | Entry point; spins up one worker thread per timeframe + a summary thread |
| `delta_client.py` | REST wrapper for products/tickers/candles |
| `indicators.py` | EMA, RSI, MACD, and cross-detection logic |
| `chart.py` | Dark-themed candlestick chart rendering (mplfinance) |
| `notifier.py` | Telegram sendMessage/sendPhoto |
| `state.py` | Anti-spam tracking (blocks repeat signals for N candles) |
| `config.py` | Loads and validates all settings from `.env` |

## Tuning

- `MIN_24H_VOLUME` — raise this to scan only the most liquid contracts (fewer, higher-quality alerts).
- `EMA_FAST` / `EMA_SLOW` — change the periods used for the cross signal.
- `ANTI_SPAM_CANDLES` — how many candles must pass before the same signal (symbol+timeframe+direction) can fire again.
- `TIMEFRAMES` — any of `1m,3m,5m,15m,30m,1h,2h,4h,6h,1d,1w` supported by Delta's candle API.

## Logs

Written to `logs/bot.log` (and stdout). Check here first if alerts stop coming.
