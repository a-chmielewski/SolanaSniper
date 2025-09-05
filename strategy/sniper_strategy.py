import time
from datetime import datetime, timedelta
from config import TARGET_PROFIT, STOP_LOSS, BUY_AMOUNT_USD
from data.birdeye_api import birdeye_api
from data.jupiter_api import jupiter_api

class SniperStrategy:
    def __init__(self):
        self.min_liquidity_for_buy = 10000  # $10k minimum
        self.max_price_impact = 3.0  # 3% max price impact
        self.min_volume_spike = 2.0  # 2x volume increase
        self.max_hold_time_minutes = 30
    
    def should_buy(self, token_data):
        """Determine if we should buy this token"""
        
        # Basic safety checks
        if not self._basic_safety_checks(token_data):
            return False, "Failed basic safety checks"
        
        # Check liquidity depth
        if not self._check_liquidity_depth(token_data):
            return False, "Insufficient liquidity depth"
        
        # Check for suspicious activity
        if self._detect_suspicious_activity(token_data):
            return False, "Suspicious activity detected"
        
        # Check Jupiter quote feasibility
        if not self._check_swap_feasibility(token_data):
            return False, "Swap not feasible"
        
        # All checks passed
        return True, "All checks passed"
    
    def _basic_safety_checks(self, token_data):
        """Basic safety checks for token"""
        
        # Must have valid metadata
        if not token_data.get('symbol') or not token_data.get('name'):
            return False
        
        # Check for obvious scam indicators
        symbol = token_data.get('symbol', '').lower()
        name = token_data.get('name', '').lower()
        
        scam_keywords = ['test', 'fake', 'scam', 'rug', 'honeypot', 'admin']
        for keyword in scam_keywords:
            if keyword in symbol or keyword in name:
                return False
        
        # Market cap should be reasonable
        market_cap = token_data.get('market_cap', 0)
        if market_cap <= 0 or market_cap > 1000000:  # Over $1M might be too established
            return False
        
        # Must have recent trading activity
        last_trade_time = token_data.get('last_trade_unix_time', 0)
        if not last_trade_time:
            return False
        
        minutes_since_trade = (time.time() - last_trade_time) / 60
        if minutes_since_trade > 10:  # More than 10 minutes ago
            return False
        
        return True
    
    def _check_liquidity_depth(self, token_data):
        """Check if there's enough liquidity for our trade"""
        liquidity = token_data.get('liquidity', 0)
        
        # Need at least 10x our trade size in liquidity
        min_required = BUY_AMOUNT_USD * 10
        return liquidity >= min_required
    
    def _detect_suspicious_activity(self, token_data):
        """Detect potential rug pulls or honeypots"""
        
        # Check for extreme price movements (possible pump)
        price_change = token_data.get('price_24h_change', 0)
        if price_change > 1000:  # More than 1000% in 24h is suspicious
            return True
        
        # Check volume to market cap ratio
        volume_24h = token_data.get('volume_24h', 0)
        market_cap = token_data.get('market_cap', 1)
        
        volume_ratio = volume_24h / market_cap
        if volume_ratio > 10:  # Volume 10x market cap is suspicious
            return True
        
        return False
    
    def _check_swap_feasibility(self, token_data):
        """Check if we can actually execute the swap via Jupiter"""
        try:
            token_address = token_data.get('address')
            if not token_address:
                return False
            
            # Get a small test quote
            sol_amount = 0.001  # Test with 0.001 SOL
            quote = jupiter_api.get_sol_to_token_quote(token_address, sol_amount)
            
            if not quote:
                return False
            
            # Check price impact
            price_impact = float(quote.get('priceImpactPct', 0))
            if price_impact > self.max_price_impact:
                return False
            
            # Validate quote
            is_valid, _ = jupiter_api.validate_quote_for_sniper(quote, self.max_price_impact)
            return is_valid
            
        except Exception:
            return False
    
    def should_sell(self, position_data):
        """Determine if we should sell a position"""
        
        entry_price = position_data.get('entry_price', 0)
        entry_time = position_data.get('entry_time')
        token_address = position_data.get('token_address')
        
        if not all([entry_price, entry_time, token_address]):
            return True, "missing_data"
        
        # Get current price
        current_price = birdeye_api.get_price(token_address)
        if not current_price:
            return True, "no_price_data"
        
        # Calculate P&L
        pnl_percent = (current_price - entry_price) / entry_price
        
        # Take profit
        if pnl_percent >= TARGET_PROFIT:
            return True, "take_profit"
        
        # Stop loss
        if pnl_percent <= -STOP_LOSS:
            return True, "stop_loss"
        
        # Time-based exit
        time_held = (datetime.now() - entry_time).total_seconds() / 60
        if time_held > self.max_hold_time_minutes:
            return True, "time_limit"
        
        # Liquidity check (rug protection)
        token_overview = birdeye_api.get_token_overview(token_address)
        current_liquidity = token_overview.get('liquidity', 0)
        if current_liquidity < 5000:  # Less than $5k liquidity left
            return True, "liquidity_drop"
        
        # Check for negative momentum
        if pnl_percent < -0.05 and time_held > 5:  # -5% after 5 minutes
            recent_trades = birdeye_api.get_recent_trades(token_address, limit=10)
            if len(recent_trades) < 3:  # Very few recent trades
                return True, "low_activity"
        
        return False, "hold"
    
    def calculate_position_size(self, wallet_balance, token_data):
        """Calculate appropriate position size"""
        
        # Use fixed amount for now, but could be dynamic based on:
        # - Wallet balance
        # - Token risk score
        # - Market conditions
        
        max_position = min(BUY_AMOUNT_USD, wallet_balance * 0.1)  # Max 10% of balance
        return max_position
    
    def get_entry_signals(self, token_data):
        """Generate entry signal strength (0-100)"""
        signal_strength = 0
        
        # Volume signal (0-30 points)
        volume_24h = token_data.get('volume_24h', 0)
        if volume_24h > 100000:  # $100k+
            signal_strength += 30
        elif volume_24h > 50000:  # $50k+
            signal_strength += 20
        elif volume_24h > 20000:  # $20k+
            signal_strength += 10
        
        # Price momentum signal (0-25 points)
        price_change = token_data.get('price_24h_change', 0)
        if 10 <= price_change <= 100:  # 10-100% gain
            signal_strength += 25
        elif 5 <= price_change < 10:  # 5-10% gain
            signal_strength += 15
        elif price_change > 0:  # Any positive gain
            signal_strength += 5
        
        # Liquidity signal (0-20 points)
        liquidity = token_data.get('liquidity', 0)
        if liquidity > 100000:  # $100k+
            signal_strength += 20
        elif liquidity > 50000:  # $50k+
            signal_strength += 15
        elif liquidity > 20000:  # $20k+
            signal_strength += 10
        
        # Recency signal (0-25 points)
        last_trade_time = token_data.get('last_trade_unix_time', 0)
        if last_trade_time:
            minutes_ago = (time.time() - last_trade_time) / 60
            if minutes_ago < 1:  # Less than 1 minute
                signal_strength += 25
            elif minutes_ago < 3:  # Less than 3 minutes
                signal_strength += 15
            elif minutes_ago < 5:  # Less than 5 minutes
                signal_strength += 10
        
        return min(signal_strength, 100)

# Global strategy instance
sniper_strategy = SniperStrategy()

# Legacy functions for backward compatibility
def should_buy(token_data):
    return sniper_strategy.should_buy(token_data)[0]

def should_sell(entry_price, current_price):
    position_data = {
        'entry_price': entry_price,
        'entry_time': datetime.now() - timedelta(minutes=10),  # Assume 10 min ago
        'token_address': 'dummy'
    }
    return sniper_strategy.should_sell(position_data)[1] if sniper_strategy.should_sell(position_data)[0] else None

def position_size(balance, fixed_usd=None):
    return fixed_usd or BUY_AMOUNT_USD
