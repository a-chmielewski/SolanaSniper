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
        url = f"{BASE_URL}/defi/token_trending"
        params = {"chain": "solana", "limit": max(1, min(limit, 20))}

        # 1 RPS rate-limit (already in your class)
        current_time = time.time()
        if current_time - self.last_request_time < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - (current_time - self.last_request_time))

        try:
            resp = requests.get(url, params=params, headers=self.headers, timeout=10)
            self.last_request_time = time.time()
            # print(f"[BirdEye] trending status={resp.status_code} url={resp.url}")

            if resp.status_code != 200:
                print(f"BirdEye API Error {resp.status_code}: {resp.text[:300]}")
                return []

            payload = resp.json()
            data = payload.get("data")
            # Log inner shape
            # print(f"[BirdEye] data type={type(data).__name__} inner_keys={list(data.keys())[:10] if isinstance(data, dict) else '—'}")

            tokens = []

            if isinstance(data, list):
                tokens = data

            elif isinstance(data, dict):
                # Common container keys used by different endpoints
                for k in ("tokens", "items", "pairs", "list"):
                    v = data.get(k)
                    if isinstance(v, list) and v:
                        tokens = v
                        break

                # Fallback: pick the first list value found in the dict
                if not tokens:
                    for v in data.values():
                        if isinstance(v, list) and v:
                            tokens = v
                            break

            # As a last resort, if data itself is falsy or no list found
            if not isinstance(tokens, list):
                tokens = []

            # print(f"[BirdEye] trending items={len(tokens)}  payload_keys={list(payload.keys())[:5]}")
            return tokens

        except Exception as e:
            print(f"[BirdEye] trending JSON error: {e}")
            # Show a snippet so you can see unexpected shapes in logs
            try:
                print(f"[BirdEye] body={resp.text[:300]}")
            except Exception:
                pass
            return []


    def format_token_data(self, token):
        if not isinstance(token, dict):
            return None

        address = (
            token.get('address') or token.get('mint') or
            token.get('token_address') or token.get('tokenAddress')
        )
        symbol = token.get('symbol') or token.get('tokenSymbol') or token.get('baseSymbol') or ""
        name = token.get('name') or token.get('tokenName') or ""

        decimals = (
            token.get('decimals') or token.get('token_decimals') or
            token.get('tokenDecimals') or 9
        )

        # Price (USD)
        price = (
            token.get('price') or token.get('priceUsd') or
            (token.get('value') and token['value'].get('price')) or
            token.get('price_usd') or 0.0
        )

        # 24h change (percent)
        price_change_24h = (
            token.get('price24hChangePercent') or token.get('priceChange24h') or
            token.get('price_change_24h') or token.get('change24h') or 0.0
        )

        # 24h volume (USD)
        volume_24h = (
            token.get('v24hUSD') or token.get('volume24hUsd') or
            token.get('volume_24h_usd') or token.get('volume_24h') or 0.0
        )

        # Liquidity (USD)
        liquidity = (
            token.get('liquidity') or token.get('liquidityUsd') or
            token.get('poolLiquidityUsd') or 0.0
        )

        # Timestamps
        created_at = token.get('createdAt') or token.get('created_at') or 0
        last_trade_ts = (
            token.get('lastTradeUnixTime') or token.get('lastTradeTime') or
            token.get('last_trade_ts') or 0
        )

        if not address or not symbol:
            return None

        try:
            return {
                'address': address,
                'symbol': symbol,
                'name': name,
                'decimals': int(decimals),
                'supply': token.get('supply') or token.get('totalSupply') or 0,
                'market_cap': token.get('mc') or token.get('marketCapUsd') or token.get('market_cap') or 0.0,
                'price': float(price) if price is not None else 0.0,
                'price_24h_change': float(price_change_24h) if price_change_24h is not None else 0.0,
                'volume_24h': float(volume_24h) if volume_24h is not None else 0.0,
                'liquidity': float(liquidity) if liquidity is not None else 0.0,
                'created_at': created_at,
                'last_trade_unix_time': last_trade_ts
            }
        except Exception:
            return None


# Global instance
birdeye_api = BirdEyeAPI()
