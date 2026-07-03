import logging
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


def send_message(text: str, parse_mode: str = 'HTML') -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning('Telegram not configured — skipping message')
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    with httpx.Client(timeout=10) as client:
        r = client.post(url, json={
            'chat_id':    TELEGRAM_CHAT_ID,
            'text':       text,
            'parse_mode': parse_mode,
        })
        r.raise_for_status()
