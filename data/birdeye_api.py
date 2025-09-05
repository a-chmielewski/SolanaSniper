import requests
from config import BIRDEYE_API_KEY

BASE_URL = "https://public-api.birdeye.so"

def get_token_list():
    """Fetch list of tokens from BirdEye"""
    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    url = f"{BASE_URL}/defi/tokenlist?chain=solana"
    resp = requests.get(url, headers=headers)
    return resp.json().get("data", [])

def get_token_overview(token_address):
    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    url = f"{BASE_URL}/defi/token_overview?address={token_address}&chain=solana"
    resp = requests.get(url, headers=headers)
    return resp.json().get("data", {})

def get_recent_trades(token_address):
    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    url = f"{BASE_URL}/defi/trades?address={token_address}&chain=solana"
    resp = requests.get(url, headers=headers)
    return resp.json().get("data", [])

def get_price(token_address):
    headers = {"X-API-KEY": BIRDEYE_API_KEY}
    url = f"{BASE_URL}/defi/price?address={token_address}&chain=solana"
    resp = requests.get(url, headers=headers)
    return resp.json().get("data", {}).get("value", None)
