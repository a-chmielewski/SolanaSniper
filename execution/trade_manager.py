from data import jupiter_api, birdeye_api
from execution import wallet
from strategy import sniper_strategy
from config import BUY_AMOUNT_USD
from monitoring import logger

def execute_sniper_trade(token):
    logger.log_info(f"Sniping {token.get('symbol')}...")
    w = wallet.load_wallet("private_key_here")
    route = jupiter_api.get_quote("So11111111111111111111111111111111111111112", token["address"], BUY_AMOUNT_USD)
    tx = jupiter_api.execute_swap(w, route)
    logger.log_trade(token.get("symbol"), "buy", BUY_AMOUNT_USD, token.get("price"))
    return tx

def monitor_position(token, entry_price):
    current_price = birdeye_api.get_price(token["address"])
    decision = sniper_strategy.should_sell(entry_price, current_price)
    if decision:
        logger.log_trade(token.get("symbol"), decision, None, current_price)
        # Here you would call jupiter_api.execute_swap to sell
