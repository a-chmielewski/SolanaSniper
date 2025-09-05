from config import TARGET_PROFIT, STOP_LOSS

def should_buy(token_data):
    # For now, if it passed filters → buy
    return True

def position_size(balance, fixed_usd=10):
    # Placeholder: return fixed amount
    return fixed_usd

def should_sell(entry_price, current_price):
    if current_price >= entry_price * (1 + TARGET_PROFIT):
        return "take_profit"
    elif current_price <= entry_price * (1 - STOP_LOSS):
        return "stop_loss"
    return None
