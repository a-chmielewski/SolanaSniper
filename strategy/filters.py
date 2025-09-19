from data.utils import token_filter, rank_tokens_by_score
from data.birdeye_api import birdeye_api

def apply_filters(raw_tokens):
    """Apply all filtering logic to raw token data"""
    if not raw_tokens:
        return []
    
    # Format tokens using BirdEye API formatter
    formatted_tokens = []
    for token in raw_tokens:
        formatted = birdeye_api.format_token_data(token)
        if formatted:
            formatted_tokens.append(formatted)
    
    # Enrich missing mc/volume/last_trade from overview
    formatted_tokens = birdeye_api.enrich_with_overview(formatted_tokens)
    
    # Apply filters using the utility class
    candidates, filter_stats = token_filter.filter_tokens_batch(formatted_tokens)
    
    # Print filter breakdown
    print("Filter stats:", {k: v for k, v in filter_stats.items()})
    
    # Print sample formatted token for debugging
    if formatted_tokens:
        print("Sample formatted:", {
            'symbol': formatted_tokens[0]['symbol'],
            'mcap': formatted_tokens[0]['market_cap'],
            'vol24': formatted_tokens[0]['volume_24h'],
            'liq': formatted_tokens[0]['liquidity'],
            'last_trade_ts': formatted_tokens[0]['last_trade_ts'],
        })
    
    # Rank candidates by score
    ranked_candidates = rank_tokens_by_score(candidates)
    
    # Return top candidates (token data only, not scores)
    top_candidates = [token for token, score in ranked_candidates[:10]]
    
    print(f"Filtered {filter_stats['total']} tokens → {len(top_candidates)} candidates")
    return top_candidates

def get_new_tokens_only():
    """Get only newly created tokens from trending list"""
    trending = birdeye_api.get_trending_tokens(limit=20)
    if not trending:
        return []
    
    new_tokens = []
    for token in trending:
        formatted = birdeye_api.format_token_data(token)
        if formatted and token_filter.is_new_token(formatted):
            new_tokens.append(formatted)
    
    return new_tokens

def get_high_volume_tokens():
    """Get tokens with recent high volume spikes"""
    trending = birdeye_api.get_trending_tokens(limit=20)
    if not trending:
        return []
    
    high_volume = []
    for token in trending:
        formatted = birdeye_api.format_token_data(token)
        if formatted:
            volume_24h = formatted.get('volume_24h', 0)
            if volume_24h > 50000:  # $50k+ volume
                high_volume.append(formatted)
    
    return sorted(high_volume, key=lambda x: x.get('volume_24h', 0), reverse=True)

def filter_by_momentum(tokens):
    """Filter tokens showing positive price momentum"""
    momentum_tokens = []
    
    for token in tokens:
        price_change = token.get('price_24h_change', 0)
        volume_24h = token.get('volume_24h', 0)
        
        # Look for positive momentum with decent volume
        if price_change > 10 and volume_24h > 10000:  # +10% with $10k+ volume
            momentum_tokens.append(token)
    
    return sorted(momentum_tokens, key=lambda x: x.get('price_24h_change', 0), reverse=True)

def get_sniper_candidates():
    """Get the best candidates for sniping based on multiple criteria"""
    print("🔍 Scanning for sniper candidates...")
    
    # Get tokens from multiple sources
    trending_tokens = birdeye_api.get_trending_tokens(limit=20) or []  # Max limit is 20
    # print(f"Discovery: fetched={len(trending_tokens)} before filtering")
    
    # Apply main filters
    candidates = apply_filters(trending_tokens)
    
    if not candidates:
        print("❌ No candidates found")
        return []
    
    # Additional filtering for momentum
    momentum_candidates = filter_by_momentum(candidates)
    
    # Combine and deduplicate, excluding already held positions
    from execution.trade_manager import trade_manager
    final_candidates = []
    seen_addresses = set()
    
    # Prioritize momentum candidates
    for token in momentum_candidates:
        token_address = token['address']
        if token_address not in seen_addresses and token_address not in trade_manager.active_positions:
            final_candidates.append(token)
            seen_addresses.add(token_address)
    
    # Add remaining candidates
    for token in candidates:
        token_address = token['address']
        if token_address not in seen_addresses and token_address not in trade_manager.active_positions and len(final_candidates) < 5:
            final_candidates.append(token)
            seen_addresses.add(token_address)
    
    print(f"✅ Found {len(final_candidates)} sniper candidates")
    return final_candidates[:5]  # Top 5 candidates
