from data.utils import token_filter, rank_tokens_by_score
from data.dexscreener_api import dexscreener_api

def apply_filters(raw_tokens):
    """Apply all filtering logic to raw token data"""
    if not raw_tokens:
        return []
    
    # Validate input tokens - only require address before enrichment
    validated_tokens = []
    for token in raw_tokens:
        if not isinstance(token, dict):
            print(f"❌ Invalid token type: {type(token)} - {str(token)[:100]}")
            continue
        if not token.get('address'):
            print(f"❌ Token missing address: {token.get('symbol', 'UNKNOWN')} - {str(token)[:100]}")
            continue
        validated_tokens.append(token)
    
    # Skip formatting since tokens are already formatted
    formatted_tokens = validated_tokens
    
    # Enrich missing mc/volume/last_trade from overview
    formatted_tokens = dexscreener_api.enrich_with_overview(formatted_tokens)
    
    # Apply filters using the utility class
    candidates, filter_stats = token_filter.filter_tokens_batch(formatted_tokens)
    
    # Print filter breakdown
    print("Filter stats:", {k: v for k, v in filter_stats.items()})
    
    # Log enriched token if no candidates pass
    if filter_stats.get('passed', 0) == 0 and formatted_tokens:
        print(f"Sample enriched token: {formatted_tokens[0]}")
    
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
    trending = dexscreener_api.get_trending_tokens(limit=20)
    if not trending:
        return []
    
    formatted_tokens = []
    for token in trending:
        formatted = dexscreener_api.format_token_data(token)
        if formatted:
            formatted_tokens.append(formatted)
    
    # Enrich before checking creation date
    enriched_tokens = dexscreener_api.enrich_with_overview(formatted_tokens)
    
    new_tokens = []
    for token in enriched_tokens:
        if token_filter.is_new_token(token):
            new_tokens.append(token)
    
    return new_tokens

def get_high_volume_tokens():
    """Get tokens with recent high volume spikes"""
    trending = dexscreener_api.get_trending_tokens(limit=20)
    if not trending:
        return []
    
    formatted_tokens = []
    for token in trending:
        formatted = dexscreener_api.format_token_data(token)
        if formatted:
            formatted_tokens.append(formatted)
    
    # Enrich before checking volume
    enriched_tokens = dexscreener_api.enrich_with_overview(formatted_tokens)
    
    high_volume = []
    for token in enriched_tokens:
        volume_24h = token.get('volume_24h', 0)
        if volume_24h > 50000:  # $50k+ volume
            high_volume.append(token)
    
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
    
    try:
        # Get tokens from multiple sources - all return formatted tokens
        trending_tokens = []
        trending_raw = dexscreener_api.get_trending_tokens(limit=20) or []
        for raw_token in trending_raw:
            try:
                formatted = dexscreener_api.format_token_data(raw_token)
                if formatted:
                    trending_tokens.append(formatted)
                else:
                    print(f"❌ Failed to format token: {raw_token.get('baseToken', {}).get('symbol', 'UNKNOWN')} - {str(raw_token)[:200]}")
            except Exception as e:
                print(f"❌ Error formatting token: {str(e)} - {str(raw_token)[:100]}")
        
        new_tokens = get_new_tokens_only() or []
        high_volume_tokens = get_high_volume_tokens() or []
        
        # Combine all sources and deduplicate
        all_tokens = []
        seen_addresses = set()
        
        # Add from all sources
        for token_list in [trending_tokens, new_tokens, high_volume_tokens]:
            for token in token_list:
                try:
                    address = token.get('address')
                    if not address:
                        print(f"❌ Token missing address: {token.get('symbol', 'UNKNOWN')} - {str(token)[:200]}")
                        continue
                    if address not in seen_addresses:
                        all_tokens.append(token)
                        seen_addresses.add(address)
                except Exception as e:
                    print(f"❌ Error processing token: {str(e)} - {str(token)[:100]}")
        
        print(f"Discovery: trending={len(trending_tokens)}, new={len(new_tokens)}, high_vol={len(high_volume_tokens)}, total={len(all_tokens)}")
        
        # Apply main filters
        candidates = apply_filters(all_tokens)
        
        if not candidates:
            print("❌ No candidates found")
            return []
        
        # Additional filtering for momentum
        momentum_candidates = filter_by_momentum(candidates)
        
        # Combine and deduplicate, excluding already held positions
        from execution.trade_manager import trade_manager
        final_candidates = []
        seen_final = set()
        
        # Prioritize momentum candidates
        for token in momentum_candidates:
            try:
                token_address = token['address']
                if token_address not in seen_final and token_address not in trade_manager.active_positions:
                    final_candidates.append(token)
                    seen_final.add(token_address)
            except Exception as e:
                print(f"❌ Error processing momentum candidate: {str(e)}")
        
        # Add remaining candidates
        for token in candidates:
            try:
                token_address = token['address']
                if token_address not in seen_final and token_address not in trade_manager.active_positions and len(final_candidates) < 5:
                    final_candidates.append(token)
                    seen_final.add(token_address)
            except Exception as e:
                print(f"❌ Error processing candidate: {str(e)}")
        
        print(f"✅ Found {len(final_candidates)} sniper candidates")
        return final_candidates[:5]  # Top 5 candidates
        
    except Exception as e:
        print(f"❌ Critical error in get_sniper_candidates: {str(e)}")
        return []
