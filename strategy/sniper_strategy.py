import time
from datetime import datetime, timedelta
from config import (
    TARGET_PROFIT, STOP_LOSS, BUY_AMOUNT_USD, MIN_SIGNAL_STRENGTH,
    MIN_LIQUIDITY_FOR_BUY, MAX_PRICE_IMPACT, MIN_VOLUME_SPIKE,
    MAX_HOLD_TIME_MINUTES, RECENT_TRADE_THRESHOLD_MIN,
    MAX_ESTABLISHED_MCAP, MAX_PRICE_CHANGE_24H, MAX_VOLUME_MCAP_RATIO,
    MAX_EXTREME_PRICE_CHANGE, MAX_VOLATILITY_RATIO, TEST_QUOTE_USD,
    HIGH_VOLUME_THRESHOLD, MED_VOLUME_THRESHOLD, LOW_VOLUME_THRESHOLD,
    HIGH_LIQUIDITY_THRESHOLD, MED_LIQUIDITY_THRESHOLD, LOW_LIQUIDITY_THRESHOLD_SIGNAL,
    FEE_BUFFER_SOL, MIN_RESIDUAL_SOL, ATA_RENT_SOL, MAX_POSITION_RATIO
)
# BirdEye API removed - security checks now use liquidity/price patterns
from data.jupiter_api import jupiter_api
from data.price_manager import price_manager
from data.onchain_security import onchain_security

class SniperStrategy:
    def __init__(self):
        self.min_liquidity_for_buy = MIN_LIQUIDITY_FOR_BUY
        self.max_price_impact = MAX_PRICE_IMPACT
        self.min_volume_spike = MIN_VOLUME_SPIKE
        self.max_hold_time_minutes = MAX_HOLD_TIME_MINUTES
    
    def should_buy(self, token_data):
        """Determine if we should buy this token"""
        
        # Check signal strength first
        signal_strength = self.get_entry_signals(token_data)
        if signal_strength < MIN_SIGNAL_STRENGTH:
            return False, f"Signal too weak: {signal_strength}/{MIN_SIGNAL_STRENGTH}"
        
        # Basic safety checks
        if not self._basic_safety_checks(token_data):
            return False, "Failed basic safety checks"
        
        # Security checks via BirdEye
        if not self._check_token_security(token_data):
            return False, "Failed security checks"
        
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
        return True, f"All checks passed (Signal: {signal_strength})"
    
    def _basic_safety_checks(self, token_data):
        """Basic safety checks for token"""
        
        # Must have valid metadata - only require symbol
        if not token_data.get('symbol'):
            return False
        
        # Check for obvious scam indicators
        symbol = token_data.get('symbol', '').lower()
        name = token_data.get('name', '').lower()
        
        scam_keywords = ['test', 'fake', 'scam', 'rug', 'honeypot', 'admin']
        for keyword in scam_keywords:
            if keyword in symbol or (name and keyword in name):
                return False
        
        # Market cap should be reasonable - use fdv as fallback
        market_cap = token_data.get('market_cap', 0)
        if market_cap <= 0:
            market_cap = token_data.get('fdv', 0)
        if market_cap <= 0 or market_cap > MAX_ESTABLISHED_MCAP:
            return False
        
        # Check for recent trading activity using volume as proxy
        volume_24h = token_data.get('volume_24h', 0)
        if volume_24h <= 0:
            return False
        
        return True
    
    def _check_liquidity_depth(self, token_data):
        """Check if there's enough liquidity for our trade"""
        liquidity = token_data.get('liquidity', 0)
        
        # Need at least 10x our trade size in liquidity
        min_required = BUY_AMOUNT_USD * 10
        
        # Also check that we have enough SOL for the trade
        try:
            pricing = price_manager.get_optimal_sol_amount(BUY_AMOUNT_USD)
            # Ensure liquidity can handle our trade size
            return liquidity >= min_required
        except Exception:
            return liquidity >= min_required
    
    def _detect_suspicious_activity(self, token_data):
        """Detect potential rug pulls or honeypots"""
        
        # Check for extreme price movements (possible pump)
        price_change = token_data.get('price_24h_change', 0)
        if price_change > MAX_PRICE_CHANGE_24H:
            return True
        
        # Check volume to market cap ratio
        volume_24h = token_data.get('volume_24h', 0)
        market_cap = token_data.get('market_cap', 1)
        
        volume_ratio = volume_24h / market_cap
        if volume_ratio > MAX_VOLUME_MCAP_RATIO:
            return True
        
        return False
    
    def _check_swap_feasibility(self, token_data):
        """Check if we can actually execute the swap via Jupiter"""
        try:
            token_address = token_data.get('address')
            if not token_address:
                return False
            
            # Get a small test quote using current SOL pricing
            test_usd = TEST_QUOTE_USD
            sol_amount = price_manager.usd_to_sol(test_usd)
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
    
    
    def _check_token_security(self, token_data):
        """Check token security using on-chain validation and patterns"""
        
        token_address = token_data.get('address')
        if not token_address:
            return False
        
        # On-chain security checks (mint authority, freeze authority, honeypot)
        security_result = onchain_security.check_token_security(token_address)
        if not security_result.get('safe', False):
            print(f"❌ On-chain security failed: {security_result.get('reason', 'unknown')}")
            return False
        
        # Legacy liquidity and volatility checks
        liquidity = token_data.get('liquidity', 0)
        if liquidity < 1000:  # Very low liquidity is risky
            return False
        
        # Check for suspicious price movements
        price_change = token_data.get('price_24h_change', 0)
        if abs(price_change) > MAX_EXTREME_PRICE_CHANGE:
            return False
        
        # Check liquidity to price change ratio
        if liquidity > 0 and abs(price_change) > 0:
            volatility_ratio = abs(price_change) / (liquidity / 1000)
            if volatility_ratio > MAX_VOLATILITY_RATIO:
                return False
        
        return True
    
    def calculate_position_size(self, wallet_balance_sol, token_data):
        """Calculate appropriate position size"""
        
        # Calculate spendable SOL after fee buffer
        effective_spendable = max(0.0, wallet_balance_sol - FEE_BUFFER_SOL - MIN_RESIDUAL_SOL - ATA_RENT_SOL)
        
        # Convert to USD equivalent
        from data.price_manager import price_manager
        spendable_usd = price_manager.sol_to_usd(effective_spendable)
        
        # Use fixed amount for now, but cap by spendable balance
        max_position = min(BUY_AMOUNT_USD, spendable_usd * MAX_POSITION_RATIO)
        
        # Hard-cap by available SOL
        max_sol = price_manager.usd_to_sol(max_position)
        if max_sol > effective_spendable:
            max_position = price_manager.sol_to_usd(effective_spendable)
        
        return max_position
    
    def get_entry_signals(self, token_data):
        """Generate entry signal strength (0-100)"""
        signal_strength = 0
        
        # Volume signal (0-30 points)
        volume_24h = token_data.get('volume_24h', 0)
        if volume_24h > HIGH_VOLUME_THRESHOLD:
            signal_strength += 30
        elif volume_24h > MED_VOLUME_THRESHOLD:
            signal_strength += 20
        elif volume_24h > LOW_VOLUME_THRESHOLD:
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
        if liquidity > HIGH_LIQUIDITY_THRESHOLD:
            signal_strength += 20
        elif liquidity > MED_LIQUIDITY_THRESHOLD:
            signal_strength += 15
        elif liquidity > LOW_LIQUIDITY_THRESHOLD_SIGNAL:
            signal_strength += 10
        
        # Recency signal (0-25 points)
        last_trade_time = token_data.get('last_trade_ts', 0)
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


def position_size(balance, fixed_usd=None):
    return fixed_usd or BUY_AMOUNT_USD
