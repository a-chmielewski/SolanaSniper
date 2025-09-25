"""
Oracle Price Feeds - Real-time price data from Pyth and Chainlink

This module provides access to on-chain price oracles for reduced latency:
- Pyth Network price feeds with proper exponent handling
- Chainlink price feeds via API endpoints  
- Oracle aggregator with fallback prioritization
- 30-second caching for high-frequency price requests

Benefits over API-only pricing:
- Lower latency than DexScreener/Jupiter for price data
- Reduced dependency on external APIs
- More reliable price feeds during high volatility
- Direct access to on-chain oracle data

Integration:
- Used by price_manager as primary price source
- Falls back to Jupiter quotes if oracles fail
- Provides SOL/USD pricing for position sizing
- Supports real-time price updates for exit decisions
"""

import requests
import time
from typing import Optional
from data.api_client import api_client

class PythPriceClient:
    """Client for Pyth price feeds"""
    
    def __init__(self):
        self.base_url = "https://hermes.pyth.network/api/latest_price_feeds"
        # SOL/USD price feed ID from Pyth
        self.sol_usd_feed_id = "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d"
        self.cache = {}
        self.cache_ttl = 5  # 5 seconds cache
    
    def get_sol_price(self) -> Optional[float]:
        """Get current SOL/USD price from Pyth oracle"""
        cache_key = "sol_usd"
        current_time = time.time()
        
        # Check cache first
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if current_time - cached_data['timestamp'] < self.cache_ttl:
                return cached_data['price']
        
        def fetch_pyth_price():
            params = {
                'ids[]': self.sol_usd_feed_id,
                'verbose': 'false',
                'binary': 'false'
            }
            
            response = requests.get(self.base_url, params=params, timeout=3)
            response.raise_for_status()
            
            data = response.json()
            if not data or len(data) == 0:
                return None
            
            price_feed = data[0]
            price_data = price_feed.get('price', {})
            
            # Pyth prices come with exponent
            price_raw = int(price_data.get('price', 0))
            exponent = int(price_data.get('expo', 0))
            
            if price_raw == 0:
                return None
            
            # Calculate actual price: price * 10^exponent
            price = price_raw * (10 ** exponent)
            return price
        
        price = api_client.resilient_request(
            fetch_pyth_price,
            cache_key=f"pyth_{cache_key}"
        )
        
        if price:
            # Cache the result
            self.cache[cache_key] = {
                'price': price,
                'timestamp': current_time
            }
        
        return price

class ChainlinkPriceClient:
    """Client for Chainlink price feeds via API"""
    
    def __init__(self):
        self.base_url = "https://api.chain.link/v1/feeds"
        self.sol_usd_feed = "solana-usd"
        self.cache = {}
        self.cache_ttl = 5  # 5 seconds cache
    
    def get_sol_price(self) -> Optional[float]:
        """Get current SOL/USD price from Chainlink"""
        cache_key = "sol_usd"
        current_time = time.time()
        
        # Check cache first
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if current_time - cached_data['timestamp'] < self.cache_ttl:
                return cached_data['price']
        
        def fetch_chainlink_price():
            url = f"{self.base_url}/{self.sol_usd_feed}"
            response = requests.get(url, timeout=3)
            response.raise_for_status()
            
            data = response.json()
            price = float(data.get('answer', 0))
            
            if price <= 0:
                return None
            
            return price
        
        price = api_client.resilient_request(
            fetch_chainlink_price,
            cache_key=f"chainlink_{cache_key}"
        )
        
        if price:
            # Cache the result
            self.cache[cache_key] = {
                'price': price,
                'timestamp': current_time
            }
        
        return price

class OracleAggregator:
    """Aggregates multiple price oracle sources"""
    
    def __init__(self):
        self.pyth_client = PythPriceClient()
        self.chainlink_client = ChainlinkPriceClient()
        self.fallback_cache = {}
        self.fallback_ttl = 30  # 30 seconds fallback cache
    
    def get_sol_price(self) -> Optional[float]:
        """Get SOL price with fallback strategy"""
        current_time = time.time()
        
        # Try Pyth first (fastest)
        price = self.pyth_client.get_sol_price()
        if price and price > 0:
            self._update_fallback_cache('sol_usd', price, current_time)
            return price
        
        # Try Chainlink as backup
        price = self.chainlink_client.get_sol_price()
        if price and price > 0:
            self._update_fallback_cache('sol_usd', price, current_time)
            return price
        
        # Use fallback cache if available
        if 'sol_usd' in self.fallback_cache:
            cached_data = self.fallback_cache['sol_usd']
            if current_time - cached_data['timestamp'] < self.fallback_ttl:
                print("Using cached SOL price as fallback")
                return cached_data['price']
        
        return None
    
    def _update_fallback_cache(self, key: str, price: float, timestamp: float):
        """Update fallback cache"""
        self.fallback_cache[key] = {
            'price': price,
            'timestamp': timestamp
        }

# Global oracle instance
oracle_client = OracleAggregator()
