from config import (
    CONFIDENCE_THRESHOLD,
    EDGE_MIN_PCT,
    EDGE_MAX_PRICE,
    MIN_VIABLE_SIZE_USD,
    LIQUIDITY_THRESHOLD_USD,
)


def compute(book: dict, blended_confidence: float) -> tuple[dict | None, str]:
    """
    Compute YES-side edge metrics by walking the ask ladder.

    Returns (result_dict, reason).  result_dict is None when any gate fails;
    reason is always set so callers can log exactly which gate fired.

    Qualification gates (applied in order):
      1. blended_confidence >= CONFIDENCE_THRESHOLD
      2. best_ask < EDGE_MAX_PRICE  (96c ceiling)
      3. top-of-book edge_pct >= EDGE_MIN_PCT
      4. max_size_at_edge >= MIN_VIABLE_SIZE_USD
    """
    asks = book.get('asks', [])
    if not asks:
        return None, 'no_asks'

    best_ask = asks[0]['price']

    if blended_confidence < CONFIDENCE_THRESHOLD:
        return None, f'conf {blended_confidence:.3f} < threshold {CONFIDENCE_THRESHOLD:.3f}'

    if best_ask >= EDGE_MAX_PRICE:
        return None, f'ask {best_ask:.3f} >= ceiling {EDGE_MAX_PRICE:.2f}'

    edge_pct = blended_confidence - best_ask
    if edge_pct < EDGE_MIN_PCT:
        return None, f'edge {edge_pct:+.3f} < floor {EDGE_MIN_PCT:.2f}  (conf={blended_confidence:.3f} ask={best_ask:.3f})'

    max_fill_price  = blended_confidence - EDGE_MIN_PCT
    max_size_usd    = 0.0
    total_depth_usd = 0.0

    for lvl in asks:
        p, s = lvl['price'], lvl['size']
        cost = p * s
        total_depth_usd += cost
        if p <= max_fill_price:
            max_size_usd += cost

    if max_size_usd < MIN_VIABLE_SIZE_USD:
        return None, f'size ${max_size_usd:.0f} < min ${MIN_VIABLE_SIZE_USD:.0f}  (depth=${total_depth_usd:.0f})'

    return {
        'edge_pct':            round(edge_pct, 4),
        'implied_probability': round(best_ask, 4),
        'best_ask':            round(best_ask, 4),
        'max_size_usd':        round(max_size_usd, 2),
        'total_depth_usd':     round(total_depth_usd, 2),
        'liquidity_flag':      total_depth_usd < LIQUIDITY_THRESHOLD_USD,
    }, 'qualified'
