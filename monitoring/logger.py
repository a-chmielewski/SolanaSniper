import datetime

def log_trade(symbol, action, amount, price):
    ts = datetime.datetime.utcnow().isoformat()
    print(f"[{ts}] {action.upper()} {symbol} amount={amount} price={price}")

def log_error(msg):
    ts = datetime.datetime.utcnow().isoformat()
    print(f"[{ts}] ERROR: {msg}")

def log_info(msg):
    ts = datetime.datetime.utcnow().isoformat()
    print(f"[{ts}] INFO: {msg}")
