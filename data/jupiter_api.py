import requests
from config import JUPITER_API_KEY

BASE_URL = "https://quote-api.jup.ag/v6"

def get_quote(input_token, output_token, amount):
    """Get swap route for Solana via Jupiter"""
    url = f"{BASE_URL}/quote?inputMint={input_token}&outputMint={output_token}&amount={amount}"
    resp = requests.get(url)
    return resp.json()

def execute_swap(wallet, route):
    """Send swap transaction to Jupiter"""
    # Placeholder: In real use, sign with wallet
    print(f"Executing swap for {wallet} with route {route}")
    return {"tx_id": "dummy_tx_id"}

def get_transaction_status(tx_id):
    # Placeholder
    return {"status": "confirmed"}
