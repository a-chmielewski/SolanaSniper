import requests
import time
import pandas as pd
from datetime import datetime, timedelta
from config import BIRDEYE_API_KEY

BASE_URL = "https://public-api.birdeye.so"

class BirdEyeAPI:
    def __init__(self):
        self.headers = {
            "X-API-KEY": BIRDEYE_API_KEY,
            "x-chain": "solana",
            "accept": "application/json"
        } if BIRDEYE_API_KEY else {
            "x-chain": "solana", 
            "accept": "application/json"
        }
        self.last_request_time = 0
        self.rate_limit_delay = 1.0  # 1 second between requests for free tier (1 RPS)
    
    def _make_request(self, url):
        """Make rate-limited request with error handling"""
        # Rate limiting - ensure 1 RPS for free tier
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            time.sleep(sleep_time)
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            self.last_request_time = time.time()
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"BirdEye API Error {response.status_code}: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None

    def get_token_list(self, limit=100):
        """Fetch list of tokens from BirdEye"""
        url = f"{BASE_URL}/defi/tokenlist?limit={limit}"
        result = self._make_request(url)
        return result.get("data", []) if result else []

    def get_token_overview(self, token_address):
        """Get detailed token overview including market data"""
        url = f"{BASE_URL}/defi/token_overview?address={token_address}"
        result = self._make_request(url)
        return result.get("data", {}) if result else {}

    def get_token_security(self, token_address):
        """Get token security information"""
        url = f"{BASE_URL}/defi/token_security?address={token_address}"
        result = self._make_request(url)
        return result.get("data", {}) if result else {}

    def get_recent_trades(self, token_address, limit=50):
        """Get recent trades for a token"""
        url = f"{BASE_URL}/defi/v3/token/txs?address={token_address}&limit={limit}&tx_type=swap"
        result = self._make_request(url)
        return result.get("data", {}).get("items", []) if result else []

    def get_price(self, token_address):
        """Get current token price"""
        url = f"{BASE_URL}/defi/price?address={token_address}"
        result = self._make_request(url)
        return result.get("data", {}).get("value", None) if result else None

    def get_multi_price(self, token_addresses):
        """Get prices for multiple tokens"""
        addresses_str = ",".join(token_addresses)
        url = f"{BASE_URL}/defi/multi_price?list_address={addresses_str}"
        result = self._make_request(url)
        return result.get("data", {}) if result else {}
    
    def get_sol_price_usd(self):
        """Get current SOL price in USD"""
        sol_mint = "So11111111111111111111111111111111111111112"
        price_data = self.get_price(sol_mint)
        return price_data if price_data else 150.0  # Fallback to $150

    def get_trending_tokens(self, limit=20):
        """Get trending tokens on Solana"""
        url = f"{BASE_URL}/defi/token_trending?limit={limit}"
        result = self._make_request(url)
        if result and isinstance(result, dict):
            return result.get("data", []) if result.get("success", False) else []
        return []

    def format_token_data(self, token_data):
        """Format token data into standardized structure"""
        if not token_data or not isinstance(token_data, dict):
            return None
            
        return {
            'address': token_data.get('address', ''),
            'symbol': token_data.get('symbol', ''),
            'name': token_data.get('name', ''),
            'decimals': int(token_data.get('decimals', 9)),
            'supply': token_data.get('supply', 0),
            'market_cap': token_data.get('mc', 0),
            'price': token_data.get('price', 0),
            'price_24h_change': token_data.get('price24hChangePercent', 0),
            'volume_24h': token_data.get('v24hUSD', 0),
            'liquidity': token_data.get('liquidity', 0),
            'created_at': token_data.get('createdAt', 0),
            'last_trade_unix_time': token_data.get('lastTradeUnixTime', 0)
        }

# Global instance
birdeye_api = BirdEyeAPI()
