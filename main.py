from config import *
from data import birdeye_api
from strategy import filters, sniper_strategy
from execution import trade_manager
from monitoring import logger

def main_loop():
    logger.log_info("Starting sniper bot...")

    tokens = birdeye_api.get_token_list()
    candidates = filters.apply_filters(tokens)

    for token in candidates:
        if sniper_strategy.should_buy(token):
            trade_manager.execute_sniper_trade(token)

if __name__ == "__main__":
    main_loop()
