import pandas as pd
import time
import json
from datetime import datetime, timedelta
from config import (
    MIN_MCAP, MAX_MCAP, MIN_LIQUIDITY, MIN_VOLUME_24H, MAX_LAST_TRADE_MINUTES,
    VOLUME_SCORE_DIVISOR, MCAP_SCORE_DIVISOR, LIQUIDITY_SCORE_DIVISOR,
    LOW_LIQUIDITY_THRESHOLD, LOW_VOLUME_THRESHOLD, VOLUME_BURST_HIGH,
    VOLUME_BURST_MED, VOLUME_BURST_LOW, PRICE_MOMENTUM_DIVISOR,
    FILTER_STATS_WINDOW, FILTER_TUNE_FREQUENCY, FILTER_SUCCESS_TARGET
)

# Known established tokens to exclude from sniping
EXCLUDED_TOKENS = {
    'So11111111111111111111111111111111111111112',  # SOL
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC
    'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',  # USDT
    'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263',  # BONK
    'A9mUU4qviSctJVPJdBJWkb28deg915LYJKrzQ19ji3FM',  # USDCet
    'mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So',   # mSOL
    'Jd4M8bfJG3sAkd82RsGWyEXoaBXQP7njFzBwEaCTuDa',   # ORCA
    'SRMuApVNdxXokk5GT7XD5cUUgXMBCoAz2LHeuAoKWRt',   # SRM
}

class TokenFilter:
    def __init__(self):
        self.min_mcap = MIN_MCAP
        self.max_mcap = MAX_MCAP
        self.min_liquidity = MIN_LIQUIDITY
        self.min_volume_24h = MIN_VOLUME_24H
        self.max_last_trade_minutes = MAX_LAST_TRADE_MINUTES
        
        # Filter performance tracking
        self.filter_outcomes = []
        self.last_tune_count = 0
        self.stats_file = 'logs/filter_performance.json'
        self._load_filter_stats()
    
    def is_new_token(self, token_data):
        """Check if token is new (created recently)"""
        if not token_data.get('created_at'):
            return False
            
        created_timestamp = token_data['created_at']
        if isinstance(created_timestamp, str):
            try:
                created_timestamp = int(created_timestamp)
            except ValueError:
                return False
        
        # Consider tokens created in last 24 hours as "new"
        current_time = int(time.time())
        hours_since_creation = (current_time - created_timestamp) / 3600
        return hours_since_creation <= 24

    def has_volume_activity(self, token_data):
        """Check if token has volume activity"""
        volume_24h = token_data.get('volume_24h', 0)
        return volume_24h > 0

    def meets_market_cap_criteria(self, token_data):
        """Check if token meets market cap criteria"""
        market_cap = token_data.get('market_cap', 0)
        # If unknown, don't filter on mcap
        if market_cap <= 0:
            return True
        return self.min_mcap <= market_cap <= self.max_mcap

    def meets_liquidity_criteria(self, token_data):
        """Check if token meets liquidity criteria"""
        liquidity = token_data.get('liquidity', 0)
        return liquidity >= self.min_liquidity

    def meets_volume_criteria(self, token_data):
        """Check if token meets 24h volume criteria"""
        volume_24h = token_data.get('volume_24h', 0)
        # If unknown, don't filter on volume
        if volume_24h <= 0:
            return True
        return volume_24h >= self.min_volume_24h

    def is_excluded_token(self, token_data):
        """Check if token is in exclusion list"""
        address = token_data.get('address', '')
        return address in EXCLUDED_TOKENS

    def has_valid_metadata(self, token_data):
        """Check if token has valid metadata"""
        # Must have address
        if not token_data.get('address'):
            return False
        
        # Must have symbol (enrichment should fill this)
        if not token_data.get('symbol'):
            return False
        
        # Check for suspicious names/symbols
        symbol = token_data.get('symbol', '').lower()
        name = token_data.get('name', '').lower()
        
        suspicious_terms = ['test', 'fake', 'scam', 'rug', 'honeypot']
        for term in suspicious_terms:
            if term in symbol or (name and term in name):
                return False
        
        return True

    def filter_token(self, token_data):
        """Apply all filters to a single token"""
        if not token_data:
            return False, "No token data"
        
        if self.is_excluded_token(token_data):
            return False, "Excluded token"
        
        if not self.has_valid_metadata(token_data):
            return False, "Invalid metadata"
        
        if not self.meets_market_cap_criteria(token_data):
            mcap = token_data.get('market_cap', 0)
            return False, f"Market cap {mcap} outside range {self.min_mcap}-{self.max_mcap}"
        
        if not self.meets_liquidity_criteria(token_data):
            liquidity = token_data.get('liquidity', 0)
            return False, f"Liquidity {liquidity} below minimum {self.min_liquidity}"
        
        if not self.meets_volume_criteria(token_data):
            volume = token_data.get('volume_24h', 0)
            return False, f"24h volume {volume} below minimum {self.min_volume_24h}"
        
        if not self.has_volume_activity(token_data):
            return False, "No volume activity"
        
        return True, "Passed all filters"

    def filter_tokens_batch(self, tokens_list):
        """Filter a list of tokens and return candidates"""
        candidates = []
        filter_stats = {
            'total': len(tokens_list),
            'excluded': 0,
            'invalid_metadata': 0,
            'market_cap_filtered': 0,
            'liquidity_filtered': 0,
            'volume_filtered': 0,
            'no_volume': 0,
            'passed': 0
        }
        
        # Debug first 5 tokens
        for i, token in enumerate(tokens_list[:5]):
            passed, reason = self.filter_token(token)
            symbol = token.get('symbol', 'UNKNOWN')
            address = token.get('address', 'NO_ADDR')[:8]
            print(f"Drop {symbol} {address}: {reason}")
        
        for token in tokens_list:
            passed, reason = self.filter_token(token)
            
            if passed:
                candidates.append(token)
                filter_stats['passed'] += 1
            else:
                # Update stats based on reason
                if 'Excluded token' in reason:
                    filter_stats['excluded'] += 1
                elif 'Invalid metadata' in reason:
                    filter_stats['invalid_metadata'] += 1
                elif 'Market cap' in reason:
                    mcap = token.get('market_cap', 0)
                    if mcap > 0:  # Only count when mcap is known
                        filter_stats['market_cap_filtered'] += 1
                elif 'Liquidity' in reason:
                    filter_stats['liquidity_filtered'] += 1
                elif '24h volume' in reason:
                    volume = token.get('volume_24h', 0)
                    if volume > 0:  # Only count when volume is known
                        filter_stats['volume_filtered'] += 1
                elif 'No volume activity' in reason:
                    filter_stats['no_volume'] += 1
        
        return candidates, filter_stats
    
    def record_outcome(self, token_address, filtered_in, trade_success):
        """Record filter outcome for performance tracking"""
        outcome = {
            'timestamp': time.time(),
            'address': token_address,
            'filtered_in': filtered_in,
            'trade_success': trade_success,
            'thresholds': {
                'min_mcap': self.min_mcap,
                'min_liquidity': self.min_liquidity,
                'min_volume_24h': self.min_volume_24h
            }
        }
        
        self.filter_outcomes.append(outcome)
        
        # Keep only recent outcomes
        if len(self.filter_outcomes) > FILTER_STATS_WINDOW:
            self.filter_outcomes = self.filter_outcomes[-FILTER_STATS_WINDOW:]
        
        self._save_filter_stats()
        
        # Auto-tune thresholds periodically
        if len(self.filter_outcomes) - self.last_tune_count >= FILTER_TUNE_FREQUENCY:
            self._auto_tune_thresholds()
            self.last_tune_count = len(self.filter_outcomes)
    
    def _load_filter_stats(self):
        """Load filter performance stats from file"""
        try:
            with open(self.stats_file, 'r') as f:
                data = json.load(f)
                self.filter_outcomes = data.get('outcomes', [])
                self.last_tune_count = data.get('last_tune_count', 0)
        except FileNotFoundError:
            pass
    
    def _save_filter_stats(self):
        """Save filter performance stats to file"""
        import os
        os.makedirs('logs', exist_ok=True)
        with open(self.stats_file, 'w') as f:
            json.dump({
                'outcomes': self.filter_outcomes,
                'last_tune_count': self.last_tune_count
            }, f)
    
    def _auto_tune_thresholds(self):
        """Auto-tune filter thresholds based on outcomes"""
        if len(self.filter_outcomes) < 20:
            return
        
        # Calculate success rate of filtered-in tokens
        filtered_in = [o for o in self.filter_outcomes if o['filtered_in']]
        if not filtered_in:
            return
        
        success_rate = sum(1 for o in filtered_in if o['trade_success']) / len(filtered_in)
        
        # Adjust thresholds based on success rate vs target
        if success_rate < FILTER_SUCCESS_TARGET:
            # Too many low-quality tokens passing, tighten filters
            self.min_mcap = min(self.min_mcap * 1.2, 2000)
            self.min_liquidity = min(self.min_liquidity * 1.15, 15000)
            self.min_volume_24h = min(self.min_volume_24h * 1.15, 10000)
        elif success_rate > FILTER_SUCCESS_TARGET * 1.5:
            # Success rate too high, might be over-pruning, relax slightly
            self.min_mcap = max(self.min_mcap * 0.9, 200)
            self.min_liquidity = max(self.min_liquidity * 0.95, 2000)
            self.min_volume_24h = max(self.min_volume_24h * 0.95, 2000)
        
        print(f"Auto-tuned thresholds: mcap={self.min_mcap}, liq={self.min_liquidity}, vol={self.min_volume_24h} (success_rate={success_rate:.2f})")
    
    def get_filter_performance(self):
        """Get current filter performance metrics"""
        if not self.filter_outcomes:
            return {}
        
        filtered_in = [o for o in self.filter_outcomes if o['filtered_in']]
        total_trades = len(filtered_in)
        successful_trades = sum(1 for o in filtered_in if o['trade_success'])
        
        return {
            'total_outcomes': len(self.filter_outcomes),
            'total_trades': total_trades,
            'successful_trades': successful_trades,
            'success_rate': successful_trades / total_trades if total_trades > 0 else 0,
            'current_thresholds': {
                'min_mcap': self.min_mcap,
                'min_liquidity': self.min_liquidity,
                'min_volume_24h': self.min_volume_24h
            }
        }

def calculate_token_score(token_data):
    """Calculate a score for token ranking"""
    score = 0
    
    # Volume score (higher volume = higher score)
    volume_24h = token_data.get('volume_24h', 0)
    if volume_24h > 0:
        score += min(volume_24h / VOLUME_SCORE_DIVISOR, 10)
    
    # Market cap score (prefer FDV if marketCap missing)
    market_cap = token_data.get('market_cap', 0)
    fdv = token_data.get('fdv', 0)
    cap_value = market_cap if market_cap > 0 else fdv
    if cap_value > 0:
        score += min(cap_value / MCAP_SCORE_DIVISOR, 5)
    
    # Liquidity score with penalty for low liquidity
    liquidity = token_data.get('liquidity', 0)
    if liquidity >= LOW_LIQUIDITY_THRESHOLD:
        score += min(liquidity / LIQUIDITY_SCORE_DIVISOR, 5)
    elif liquidity > 0:
        score -= 2
    
    # Volume penalty for very low volume
    if volume_24h > 0 and volume_24h < LOW_VOLUME_THRESHOLD:
        score -= 1
    
    # Volume burst proxy - high volume relative to market cap indicates activity burst
    if market_cap > 0 and volume_24h > 0:
        volume_mcap_ratio = volume_24h / market_cap
        if volume_mcap_ratio > VOLUME_BURST_HIGH:
            score += 5
        elif volume_mcap_ratio > VOLUME_BURST_MED:
            score += 3
        elif volume_mcap_ratio > VOLUME_BURST_LOW:
            score += 1
    
    # Price change score (positive momentum)
    price_change = token_data.get('price_24h_change', 0)
    if price_change > 0:
        score += min(price_change / PRICE_MOMENTUM_DIVISOR, 3)
    
    # Price burst proxy - significant price change indicates momentum
    price_change_abs = abs(price_change)
    if price_change_abs > 20:
        score += 3
    elif price_change_abs > 10:
        score += 2
    elif price_change_abs > 5:
        score += 1
    
    # Quote token preference (if available in token data)
    quote_symbol = token_data.get('quote_symbol', '').upper()
    if quote_symbol in ('USDC', 'SOL', 'WSOL'):
        score += 2
    
    # New token bonus (if available)
    created_at = token_data.get('created_at', 0)
    if created_at > 0:
        age_hours = (time.time() - created_at) / 3600
        if age_hours <= 24:
            score += 2
    
    return score

def rank_tokens_by_score(tokens_list):
    """Rank tokens by calculated score"""
    scored_tokens = []
    for token in tokens_list:
        score = calculate_token_score(token)
        scored_tokens.append((token, score))
    
    # Sort by score descending
    scored_tokens.sort(key=lambda x: x[1], reverse=True)
    return scored_tokens

def format_token_summary(token_data):
    """Format token data for display"""
    return {
        'symbol': token_data.get('symbol', 'N/A'),
        'name': token_data.get('name', 'N/A'),
        'address': token_data.get('address', 'N/A')[:8] + '...',
        'market_cap': f"${token_data.get('market_cap', 0):,.0f}",
        'liquidity': f"${token_data.get('liquidity', 0):,.0f}",
        'volume_24h': f"${token_data.get('volume_24h', 0):,.0f}",
        'price_change_24h': f"{token_data.get('price_24h_change', 0):+.2f}%",
        'volume_burst': get_volume_burst_indicator(token_data)
    }

def get_volume_burst_indicator(token_data):
    """Get volume burst indicator"""
    volume_24h = token_data.get('volume_24h', 0)
    market_cap = token_data.get('market_cap', 0)
    if market_cap > 0 and volume_24h > 0:
        ratio = volume_24h / market_cap
        if ratio > 0.5:
            return "HIGH"
        elif ratio > 0.2:
            return "MED"
        elif ratio > 0.05:
            return "LOW"
    return "NONE"

def save_candidates_to_csv(candidates, filename=None):
    """Save filtered candidates to CSV file"""
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"token_candidates_{timestamp}.csv"
    
    if not candidates:
        print("No candidates to save")
        return
    
    df = pd.DataFrame([format_token_summary(token) for token in candidates])
    df.to_csv(filename, index=False)
    print(f"Saved {len(candidates)} candidates to {filename}")

# Global instance
token_filter = TokenFilter()
