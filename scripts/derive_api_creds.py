"""
Run this on the VPS to derive (or re-derive) your Polymarket API credentials.
They're deterministically computed from your wallet's private key, so they're
always recoverable without logging in via the website.

Usage:
    cd /path/to/sethmentionz
    python scripts/derive_api_creds.py

It prints the three values you need to put in your .env file:
    POLY_API_KEY
    POLY_API_SECRET
    POLY_API_PASSPHRASE
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

key     = os.environ.get('POLYMARKET_PRIVATE_KEY', '')
funder  = os.environ.get('POLYMARKET_PROXY_ADDRESS', '')

if not key or not funder:
    print('ERROR: POLYMARKET_PRIVATE_KEY and POLYMARKET_PROXY_ADDRESS must be set in .env')
    sys.exit(1)

from py_clob_client.client import ClobClient

print(f'Deriving API creds for proxy wallet {funder[:10]}…')

clob = ClobClient(
    host='https://clob.polymarket.com',
    chain_id=137,
    key=key,
    signature_type=1,
    funder=funder,
)

try:
    creds = clob.derive_api_key()
    print()
    print('Paste these into your .env file (replacing existing POLY_ lines):')
    print()
    print(f'POLY_API_KEY={creds.api_key}')
    print(f'POLY_API_SECRET={creds.api_secret}')
    print(f'POLY_API_PASSPHRASE={creds.api_passphrase}')
    print()

    current_key = os.environ.get('POLY_API_KEY', '')
    if current_key and current_key != creds.api_key:
        print('⚠️  Your current POLY_API_KEY does NOT match the derived key.')
        print('   Update .env and run: pm2 restart sethmentionz')
    elif current_key == creds.api_key:
        print('✅  Current POLY_API_KEY already matches — credentials are correct.')
        print('   The problem is something else (see below).')
    else:
        print('ℹ️  No POLY_API_KEY set — copy the values above into .env')

except Exception as e:
    print(f'ERROR: {e}')
    sys.exit(1)
