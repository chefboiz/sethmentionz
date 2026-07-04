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
    trades = db.fetchall("""
        SELECT t.market_id, t.confidence, t.won, t.actual_outcome, t.size_usd, t.approved_at
        FROM mention_trades t
        WHERE t.won IS NOT NULL
        ORDER BY t.approved_at DESC
    """)

    if not trades:
        return (
            '📊 <b>Calibration Report</b>\n\n'
            'No resolved trades yet.\n'
            'Check back after markets start resolving.'
        )

    total   = len(trades)
    wins    = sum(1 for t in trades if t.get('won'))
    overall = wins / total * 100

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

    # Per-leg summary using latest signal per resolved trade market
    trade_mids = list({t['market_id'] for t in trades})
    if trade_mids:
        signals = db.fetchall("""
            SELECT DISTINCT ON (market_id) market_id, llm_score, signal_score
            FROM mention_signals
            WHERE market_id = ANY(%s)
            ORDER BY market_id, scored_at DESC
        """, (trade_mids,))

        if signals:
            latest = {s['market_id']: s for s in signals}
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

    # Strategy breakdown
    strategy_rows = db.fetchall("""
        SELECT strategy,
               COUNT(*) FILTER (WHERE won IS NOT NULL) AS resolved,
               COUNT(*) FILTER (WHERE won = TRUE)      AS wins
        FROM mention_trades
        GROUP BY strategy
        ORDER BY strategy
    """)
    if strategy_rows:
        lines += ['', '<b>By strategy</b>',
                  '<code>Strategy              Resolved  Wins  Win%</code>']
        for s in strategy_rows:
            resolved = int(s.get('resolved') or 0)
            wins     = int(s.get('wins') or 0)
            win_pct  = wins / resolved * 100 if resolved else 0
            name     = (s.get('strategy') or 'unknown')[:22]
            lines.append(
                f'<code>{name:<22} {resolved:>8}  {wins:>4}  {win_pct:>4.0f}%</code>'
            )

    return '\n'.join(lines)


def _split_4096(text: str) -> list[str]:
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
