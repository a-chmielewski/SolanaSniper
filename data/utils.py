import pandas as pd
import time
from datetime import datetime, timedelta
from config import MIN_MCAP, MAX_MCAP, MIN_LIQUIDITY, MIN_VOLUME_24H, MAX_LAST_TRADE_MINUTES

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

    def has_recent_trades(self, token_data):
        """Check if token has recent trading activity"""
        # DexScreener doesn't provide last_trade_ts, so check volume instead
        volume_24h = token_data.get('volume_24h', 0)
        return volume_24h > 0  # Any volume indicates recent activity

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
            if term in symbol or term in name:
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
        
        if not self.has_recent_trades(token_data):
            return False, "No recent volume"
        
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
            'no_recent_trades': 0,
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
                elif 'No recent volume' in reason:
                    filter_stats['no_recent_trades'] += 1
        
        return candidates, filter_stats

def calculate_token_score(token_data):
    """Calculate a score for token ranking"""
    score = 0
    
    # Volume score (higher volume = higher score)
    volume_24h = token_data.get('volume_24h', 0)
    if volume_24h > 0:
        score += min(volume_24h / 10000, 10)  # Max 10 points for volume
    
    # Market cap score (prefer FDV if marketCap missing)
    market_cap = token_data.get('market_cap', 0)
    fdv = token_data.get('fdv', 0)
    cap_value = market_cap if market_cap > 0 else fdv
    if cap_value > 0:
        score += min(cap_value / 100000, 5)  # Max 5 points for cap
    
    # Liquidity score with penalty for low liquidity
    liquidity = token_data.get('liquidity', 0)
    if liquidity >= 10000:
        score += min(liquidity / 50000, 5)  # Max 5 points for liquidity
    elif liquidity > 0:
        score -= 2  # Penalty for low liquidity
    
    # Volume penalty for very low volume
    if volume_24h > 0 and volume_24h < 20000:
        score -= 1
    
    # Recent activity score (based on volume since no last_trade_ts)
    if volume_24h > 100000:  # High volume
        score += 5
    elif volume_24h > 50000:  # Medium volume
        score += 3
    elif volume_24h > 10000:  # Low volume
        score += 1
    
    # Price change score (positive momentum)
    price_change = token_data.get('price_24h_change', 0)
    if price_change > 0:
        score += min(price_change / 10, 3)  # Max 3 points for price momentum
    
    # Quote token preference (if available in token data)
    quote_symbol = token_data.get('quote_symbol', '').upper()
    if quote_symbol in ('USDC', 'SOL', 'WSOL'):
        score += 2
    
    # Pair age scoring (if available)
    created_at = token_data.get('created_at', 0)
    if created_at > 0:
        import time
        age_minutes = (time.time() - created_at) / 60
        if 2 <= age_minutes <= 180:  # 2 min to 3 hours
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
        'last_trade_minutes_ago': get_minutes_since_last_trade(token_data)
    }

def get_minutes_since_last_trade(token_data):
    """Get minutes since last trade - DexScreener doesn't provide this"""
    return "N/A"  # DexScreener doesn't provide last trade timestamp

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
