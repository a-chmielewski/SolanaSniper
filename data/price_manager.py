"""
Price Manager - Handles dynamic pricing and conversions
"""

import time
from datetime import datetime, timedelta
# BirdEye API removed - using Jupiter for SOL pricing
from data.jupiter_api import jupiter_api

class PriceManager:
    def __init__(self):
        self.sol_price_cache = None
        self.sol_price_timestamp = None
        self.cache_duration = 300  # Cache SOL price for 5 minutes to reduce API calls
        self.fallback_sol_price = 150.0  # Updated fallback closer to current SOL price
    
    def get_current_sol_price(self):
        """Get current SOL price in USD with caching"""
        current_time = time.time()
        
        # Check if cached price is still valid
        if (self.sol_price_cache and self.sol_price_timestamp and 
            (current_time - self.sol_price_timestamp) < self.cache_duration):
            return self.sol_price_cache
        
        # Try multiple methods to get SOL price
        sol_price = self._fetch_sol_price()
        
        if sol_price:
            self.sol_price_cache = sol_price
            self.sol_price_timestamp = current_time
            return sol_price
        
        # Return cached price if available, otherwise fallback
        return self.sol_price_cache or self.fallback_sol_price
    
    def _fetch_sol_price(self):
        """Fetch SOL price using Jupiter quotes"""
        
        # Method 1: Jupiter USDC->SOL quote (reverse calculation)
        try:
            # Quote 100 USDC for SOL to get price
            usdc_amount = 100
            sol_amount = jupiter_api.get_usd_to_sol_amount(usdc_amount)
            if sol_amount and sol_amount > 0:
                price = usdc_amount / sol_amount
                print(f"📊 SOL price from Jupiter: ${price:.2f}")
                return price
        except Exception as e:
            print(f"Jupiter SOL price failed: {e}")
        
        print(f"⚠️ Using fallback SOL price: ${self.fallback_sol_price}")
        return None
    
    def usd_to_sol(self, usd_amount):
        """Convert USD amount to SOL amount"""
        sol_price = self.get_current_sol_price()
        return usd_amount / sol_price
    
    def sol_to_usd(self, sol_amount):
        """Convert SOL amount to USD value"""
        sol_price = self.get_current_sol_price()
        return sol_amount * sol_price
    
    def get_optimal_sol_amount(self, target_usd, max_slippage=0.02):
        """Get optimal SOL amount accounting for slippage"""
        base_sol_amount = self.usd_to_sol(target_usd)
        
        # Add buffer for slippage and fees
        buffered_amount = base_sol_amount * (1 + max_slippage)
        
        return {
            'base_sol_amount': base_sol_amount,
            'buffered_sol_amount': buffered_amount,
            'sol_price_used': self.get_current_sol_price(),
            'target_usd': target_usd
        }
    
    def validate_trade_amount(self, sol_balance, target_usd):
        """Validate if wallet has enough SOL for trade"""
        pricing = self.get_optimal_sol_amount(target_usd)
        required_sol = pricing['buffered_sol_amount']
        
        if sol_balance < required_sol:
            return False, f"Insufficient SOL: need {required_sol:.4f}, have {sol_balance:.4f}"
        
        return True, pricing

# Global price manager instance
price_manager = PriceManager()
