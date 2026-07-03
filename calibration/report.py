"""
Calibration report — buckets resolved trades by confidence decile and compares
actual win rate vs predicted confidence per bucket.

Run standalone:
    python calibration/report.py

Or trigger via Telegram /calibration command.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db
from config import LLM_WEIGHT, SIGNAL_WEIGHT

log = logging.getLogger(__name__)

# Bucket lower boundaries (the last value is the open-ended upper limit)
_BUCKETS = [
    (0.94, 0.95),
    (0.95, 0.96),
    (0.96, 0.97),
    (0.97, 0.98),
    (0.98, 0.99),
    (0.99, 1.01),
]


def _bucket_label(lo: float, hi: float) -> str:
    return f'{lo:.2f}–{hi:.2f}'


def build_report() -> str:
    client = db.get_client()

    all_trades = (
        client.table('mention_trades')
        .select('market_id, confidence, llm_score:mention_signals(llm_score), '
                'won, actual_outcome, size_usd, approved_at')
        .execute()
    ).data or []

    # Filter resolved trades in Python to avoid PostgREST IS NOT NULL quirks
    trades = [t for t in all_trades if t.get('won') is not None]

    if not trades:
        return (
            '📊 <b>Calibration Report</b>\n\n'
            'No resolved trades yet.\n'
            'Check back after markets start resolving.'
        )

    total    = len(trades)
    wins     = sum(1 for t in trades if t.get('won'))
    overall  = wins / total * 100

    lines = [
        '📊 <b>Calibration Report</b>',
        f'Resolved trades: <b>{total}</b>  |  Overall win rate: <b>{overall:.0f}%</b>',
        '',
        '<code>Conf range    N  Wins  Actual  Pred   Δ</code>',
    ]

    any_bucket = False
    for lo, hi in _BUCKETS:
        bucket = [t for t in trades if lo <= float(t.get('confidence') or 0) < hi]
        if not bucket:
            continue
        any_bucket = True
        n      = len(bucket)
        w      = sum(1 for t in bucket if t.get('won'))
        actual = w / n * 100
        pred   = sum(float(t.get('confidence') or 0) for t in bucket) / n * 100
        delta  = actual - pred
        sign   = '+' if delta >= 0 else ''
        lines.append(
            f'<code>{_bucket_label(lo, hi):<12} {n:>2}  {w:>4}  '
            f'{actual:>5.0f}%  {pred:>4.1f}%  {sign}{delta:.1f}pp</code>'
        )

    if not any_bucket:
        lines.append('<i>No trades fall within confidence thresholds yet</i>')

    # Per-leg summary using mention_signals (if available)
    signals = (
        client.table('mention_signals')
        .select('market_id, llm_score, signal_score, blended_score')
        .execute()
    ).data or []

    if signals and trades:
        # Match latest signal per market to resolved trade
        latest: dict[str, dict] = {}
        for s in signals:
            mid = s['market_id']
            if mid not in latest:
                latest[mid] = s

        matched = [(t, latest[t['market_id']]) for t in trades if t['market_id'] in latest]
        if matched:
            avg_llm    = sum(float(s.get('llm_score') or 0) for _, s in matched) / len(matched)
            avg_signal = sum(float(s.get('signal_score') or 0) for _, s in matched) / len(matched)
            lines += [
                '',
                '<b>Leg averages (matched resolved trades)</b>',
                f'LLM leg avg predicted:    <b>{avg_llm*100:.1f}%</b>',
                f'Signal leg avg:           <b>{avg_signal*100:.1f}%</b>',
                f'Actual win rate:          <b>{overall:.0f}%</b>',
            ]

    lines += [
        '',
        f'<i>Blend: LLM {LLM_WEIGHT*100:.0f}% / Signal {SIGNAL_WEIGHT*100:.0f}%</i>',
        '<i>Positive Δ = over-performed (LLM may be under-confident → raise LLM_WEIGHT)</i>',
        '<i>Negative Δ = under-performed (LLM may be over-confident → lower LLM_WEIGHT)</i>',
    ]

    return '\n'.join(lines)


def _split_4096(text: str) -> list[str]:
    """Split a message into Telegram-safe chunks."""
    chunks, buf = [], ''
    for line in text.split('\n'):
        if len(buf) + len(line) + 1 > 4000:
            chunks.append(buf)
            buf = ''
        buf += line + '\n'
    if buf:
        chunks.append(buf)
    return chunks or ['']


def get_chunks() -> list[str]:
    return _split_4096(build_report())


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    print(build_report())
