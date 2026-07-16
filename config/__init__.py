import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN       = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID         = os.getenv('TELEGRAM_CHAT_ID', '')
DATABASE_URL             = os.getenv('DATABASE_URL', '')
ANTHROPIC_API_KEY        = os.getenv('ANTHROPIC_API_KEY', '')
NEWS_API_KEY             = os.getenv('NEWS_API_KEY', '')

RESOLUTION_WINDOW_HOURS  = int(os.getenv('RESOLUTION_WINDOW_HOURS', '12'))
SCAN_INTERVAL_MINUTES    = int(os.getenv('SCAN_INTERVAL_MINUTES', '30'))
PRICE_REFRESH_SECONDS    = int(os.getenv('PRICE_REFRESH_SECONDS', '90'))

LLM_WEIGHT               = float(os.getenv('LLM_WEIGHT', '0.6'))
SIGNAL_WEIGHT            = float(os.getenv('SIGNAL_WEIGHT', '0.4'))

EDGE_MIN_PCT             = float(os.getenv('EDGE_MIN_PCT', '0.05'))
EDGE_MAX_PRICE           = float(os.getenv('EDGE_MAX_PRICE', '0.96'))
MIN_VIABLE_SIZE_USD      = float(os.getenv('MIN_VIABLE_SIZE_USD', '50'))
LIQUIDITY_THRESHOLD_USD  = float(os.getenv('LIQUIDITY_THRESHOLD_USD', '200'))

CONFIDENCE_THRESHOLD     = float(os.getenv('CONFIDENCE_THRESHOLD', '0.75'))

ALERT_CHECK_SECONDS      = int(os.getenv('ALERT_CHECK_SECONDS', '60'))
CALLBACK_POLL_SECONDS    = int(os.getenv('CALLBACK_POLL_SECONDS', '5'))

POLYMARKET_API_URL       = 'https://gamma-api.polymarket.com'

# Execution (Phase 5)
POLYMARKET_PRIVATE_KEY   = os.getenv('POLYMARKET_PRIVATE_KEY', '')
POLYMARKET_PROXY_ADDRESS = os.getenv('POLYMARKET_PROXY_ADDRESS', '')

SLIPPAGE_TOLERANCE       = float(os.getenv('SLIPPAGE_TOLERANCE', '0.01'))
FILL_TIMEOUT_MINUTES     = int(os.getenv('FILL_TIMEOUT_MINUTES', '10'))
FILL_MONITOR_SECONDS          = int(os.getenv('FILL_MONITOR_SECONDS', '30'))
RESOLUTION_CHECK_MINUTES      = int(os.getenv('RESOLUTION_CHECK_MINUTES', '15'))

# Phase 7 — Longshot momentum scanner
LONGSHOT_MIN_DEPTH_USD         = float(os.getenv('LONGSHOT_MIN_DEPTH_USD', '100'))
LONGSHOT_MAX_PRICE             = float(os.getenv('LONGSHOT_MAX_PRICE', '0.40'))
LONGSHOT_MIN_CONFIDENCE        = float(os.getenv('LONGSHOT_MIN_CONFIDENCE', '0.20'))
LONGSHOT_SCORE_INTERVAL_MINUTES = int(os.getenv('LONGSHOT_SCORE_INTERVAL_MINUTES', '15'))
LONGSHOT_DIGEST_INTERVAL_MINUTES = int(os.getenv('LONGSHOT_DIGEST_INTERVAL_MINUTES', '15'))
MOMENTUM_WEIGHT               = float(os.getenv('MOMENTUM_WEIGHT', '0.40'))
VOLUME_WEIGHT                 = float(os.getenv('VOLUME_WEIGHT', '0.35'))
TIME_WEIGHT                   = float(os.getenv('TIME_WEIGHT', '0.25'))
