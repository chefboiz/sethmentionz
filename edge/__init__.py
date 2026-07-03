import logging
from datetime import datetime, timezone

from config import CONFIDENCE_THRESHOLD
import db
from edge.clob_client import fetch_book
from edge.calculator import compute

log = logging.getLogger(__name__)


def _latest_signal(client, market_id: str) -> dict | None:
    rows = (
        client.table('mention_signals')
        .select('blended_score, scored_at')
        .eq('market_id', market_id)
        .order('scored_at', desc=True)
        .limit(1)
        .execute()
    ).data
    return rows[0] if rows else None


def run_edge() -> None:
    log.info('=== Edge run started ===')
    client   = db.get_client()
    now_iso  = datetime.now(timezone.utc).isoformat()

    markets = (
        client.table('mention_markets')
        .select('market_id, question, clob_token_ids')
        .eq('resolved', False)
        .eq('archived', False)
        .execute()
    ).data or []

    checked = qualified = 0

    for market in markets:
        mid      = market['market_id']
        clob_ids = market.get('clob_token_ids') or []
        if not clob_ids:
            continue

        signal = _latest_signal(client, mid)
        if not signal:
            continue

        blended = float(signal['blended_score'])
        if blended < CONFIDENCE_THRESHOLD:
            continue

        checked += 1
        book = fetch_book(clob_ids[0])
        if not book:
            log.warning('No CLOB book for %s', mid[:14])
            continue

        result = compute(book, blended)
        if result is None:
            # market no longer qualifies — expire any stale opportunity row
            client.table('mention_opportunities') \
                .update({'status': 'expired', 'last_price_check_at': now_iso}) \
                .eq('market_id', mid) \
                .eq('status', 'pending') \
                .execute()
            continue

        client.table('mention_opportunities').upsert({
            'market_id':           mid,
            'blended_confidence':  blended,
            'edge_pct':            result['edge_pct'],
            'implied_probability': result['implied_probability'],
            'best_ask':            result['best_ask'],
            'max_size_usd':        result['max_size_usd'],
            'total_depth_usd':     result['total_depth_usd'],
            'liquidity_flag':      result['liquidity_flag'],
            'qualified_at':        now_iso,
            'last_price_check_at': now_iso,
            'status':              'pending',
            'alerted':             False,
        }, on_conflict='market_id').execute()

        qualified += 1
        thin = '  [THIN]' if result['liquidity_flag'] else ''
        log.info('  QUALIFIED: conf=%.3f  edge=+%.1f%%  size=$%.0f  ask=%.3f%s  — %s',
                 blended, result['edge_pct'] * 100, result['max_size_usd'],
                 result['best_ask'], thin, market['question'][:70])

    log.info('=== Edge complete | checked: %d  qualified: %d ===', checked, qualified)
    _print_summary(client)


def run_price_refresh() -> None:
    """
    Fast loop: re-check CLOB prices on pending opportunities only.
    Does NOT re-run confidence scoring or market scanning.
    """
    client  = db.get_client()
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = (
        client.table('mention_opportunities')
        .select('market_id, blended_confidence, mention_markets(clob_token_ids, question)')
        .eq('status', 'pending')
        .execute()
    ).data or []

    if not rows:
        return

    log.debug('Price refresh: %d pending', len(rows))
    expired = 0

    for opp in rows:
        mid     = opp['market_id']
        blended = float(opp['blended_confidence'])
        market  = opp.get('mention_markets') or {}
        clob_ids = market.get('clob_token_ids') or []
        if not clob_ids:
            continue

        book = fetch_book(clob_ids[0])
        if not book:
            continue

        result = compute(book, blended)

        if result is None:
            client.table('mention_opportunities') \
                .update({'status': 'expired', 'last_price_check_at': now_iso}) \
                .eq('market_id', mid) \
                .execute()
            expired += 1
            log.info('Price refresh: %s expired — edge closed', mid[:14])
        else:
            client.table('mention_opportunities').update({
                'edge_pct':            result['edge_pct'],
                'implied_probability': result['implied_probability'],
                'best_ask':            result['best_ask'],
                'max_size_usd':        result['max_size_usd'],
                'total_depth_usd':     result['total_depth_usd'],
                'liquidity_flag':      result['liquidity_flag'],
                'last_price_check_at': now_iso,
            }).eq('market_id', mid).execute()

    if expired:
        log.info('Price refresh: %d expired this cycle', expired)


def _print_summary(client) -> None:
    rows = (
        client.table('mention_opportunities')
        .select('market_id, blended_confidence, edge_pct, max_size_usd, best_ask, '
                'liquidity_flag, mention_markets(question)')
        .eq('status', 'pending')
        .order('edge_pct', desc=True)
        .execute()
    ).data or []

    if not rows:
        log.info('── No pending opportunities ──')
        return

    hdr = f"{'MARKET':<16}  {'CONF':>5}  {'EDGE':>6}  {'SIZE':>7}  {'ASK':>5}  {'FLAGS'}"
    log.info(hdr)
    log.info('─' * len(hdr))
    for r in rows:
        q    = ((r.get('mention_markets') or {}).get('question') or '')[:55]
        flag = 'THIN' if r.get('liquidity_flag') else ''
        log.info('%-16s  %5.3f  %+5.1f%%  $%6.0f  %.3f  %s  %s',
                 r['market_id'][:16],
                 r['blended_confidence'],
                 r['edge_pct'] * 100,
                 r['max_size_usd'],
                 r['best_ask'],
                 flag, q)
