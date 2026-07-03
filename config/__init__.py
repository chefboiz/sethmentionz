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

CONFIDENCE_THRESHOLD     = float(os.getenv('CONFIDENCE_THRESHOLD', '0.94'))

ALERT_CHECK_SECONDS      = int(os.getenv('ALERT_CHECK_SECONDS', '60'))
CALLBACK_POLL_SECONDS    = int(os.getenv('CALLBACK_POLL_SECONDS', '5'))

POLYMARKET_API_URL       = 'https://gamma-api.polymarket.com'

# Execution (Phase 5)
POLYMARKET_PRIVATE_KEY   = os.getenv('POLYMARKET_PRIVATE_KEY', '')
POLYMARKET_PROXY_ADDRESS = os.getenv('POLYMARKET_PROXY_ADDRESS', '')
POLY_API_KEY             = os.getenv('POLY_API_KEY', '')
POLY_API_SECRET          = os.getenv('POLY_API_SECRET', '')
POLY_API_PASSPHRASE      = os.getenv('POLY_API_PASSPHRASE', '')

SLIPPAGE_TOLERANCE       = float(os.getenv('SLIPPAGE_TOLERANCE', '0.01'))
FILL_TIMEOUT_MINUTES     = int(os.getenv('FILL_TIMEOUT_MINUTES', '10'))
FILL_MONITOR_SECONDS          = int(os.getenv('FILL_MONITOR_SECONDS', '30'))
RESOLUTION_CHECK_MINUTES      = int(os.getenv('RESOLUTION_CHECK_MINUTES', '15'))
