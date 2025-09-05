from config import *
from data import birdeye_api
from datetime import datetime, timedelta

def filter_by_mcap(tokens):
    return [t for t in tokens if MIN_MCAP <= t.get("marketCap", 0) <= MAX_MCAP]

def filter_by_liquidity(tokens):
    return [t for t in tokens if t.get("liquidity", 0) >= MIN_LIQUIDITY]

def filter_by_volume(tokens):
    return [t for t in tokens if t.get("volume24h", 0) >= MIN_VOLUME_24H]

def filter_by_recent_trade(tokens):
    now = datetime.utcnow()
    result = []
    for t in tokens:
        last_trade = t.get("lastTradeTime")
        if not last_trade:
            continue
        last_dt = datetime.utcfromtimestamp(last_trade)
        if (now - last_dt).total_seconds() / 60 <= MAX_LAST_TRADE_MINUTES:
            result.append(t)
    return result

def apply_filters(tokens):
    tokens = filter_by_mcap(tokens)
    tokens = filter_by_liquidity(tokens)
    tokens = filter_by_volume(tokens)
    tokens = filter_by_recent_trade(tokens)
    return tokens
