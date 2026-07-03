from config import (
    CONFIDENCE_THRESHOLD,
    EDGE_MIN_PCT,
    EDGE_MAX_PRICE,
    MIN_VIABLE_SIZE_USD,
    LIQUIDITY_THRESHOLD_USD,
)


def compute(book: dict, blended_confidence: float) -> dict | None:
    """
    Compute YES-side edge metrics by walking the ask ladder.

    Returns a result dict if all qualification thresholds pass, None otherwise.

    Qualification gates (applied in order):
      1. blended_confidence >= CONFIDENCE_THRESHOLD
      2. best_ask < EDGE_MAX_PRICE  (96c ceiling)
      3. top-of-book edge_pct >= EDGE_MIN_PCT
      4. max_size_at_edge >= MIN_VIABLE_SIZE_USD
    """
    asks = book.get('asks', [])
    if not asks:
        return None

    best_ask = asks[0]['price']

    if blended_confidence < CONFIDENCE_THRESHOLD:
        return None

    if best_ask >= EDGE_MAX_PRICE:
        return None

    edge_pct = blended_confidence - best_ask
    if edge_pct < EDGE_MIN_PCT:
        return None

    # Walk the ladder: accumulate fillable size while each level still preserves edge
    max_fill_price = blended_confidence - EDGE_MIN_PCT
    max_size_usd   = 0.0
    total_depth_usd = 0.0

    for lvl in asks:
        p, s = lvl['price'], lvl['size']
        cost = p * s
        total_depth_usd += cost
        if p <= max_fill_price:
            max_size_usd += cost

    if max_size_usd < MIN_VIABLE_SIZE_USD:
        return None

    return {
        'edge_pct':            round(edge_pct, 4),
        'implied_probability': round(best_ask, 4),
        'best_ask':            round(best_ask, 4),
        'max_size_usd':        round(max_size_usd, 2),
        'total_depth_usd':     round(total_depth_usd, 2),
        'liquidity_flag':      total_depth_usd < LIQUIDITY_THRESHOLD_USD,
    }
