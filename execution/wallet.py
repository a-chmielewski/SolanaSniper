def load_wallet(private_key):
    # Placeholder: return dummy wallet
    return {"address": "dummy_wallet"}

def get_balance(wallet):
    return 1000  # mock balance in USD

def sign_transaction(wallet, tx):
    print("Signing transaction...")
    return tx

def send_transaction(signed_tx):
    print("Sending transaction...")
    return {"tx_id": "dummy_tx_id"}
