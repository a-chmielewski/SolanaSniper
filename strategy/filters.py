from data.utils import token_filter, rank_tokens_by_score
from data.dexscreener_api import dexscreener_api

def process_token_pipeline(raw_tokens):
    """Standard pipeline: format → enrich → dedupe → filter → score"""
    if not raw_tokens:
        return []
    
    # Format
    formatted_tokens = []
    for raw_token in raw_tokens:
        try:
            formatted = dexscreener_api.format_token_data(raw_token)
            if formatted and formatted.get('address'):
                formatted_tokens.append(formatted)
        except Exception:
            continue
    
    # Enrich
    enriched_tokens = dexscreener_api.enrich_with_overview(formatted_tokens)
    
    # Dedupe
    from execution.trade_manager import trade_manager
    seen_addresses = set()
    deduped_tokens = []
    
    for token in enriched_tokens:
        address = token.get('address')
        if address and address not in seen_addresses and address not in trade_manager.active_positions:
            deduped_tokens.append(token)
            seen_addresses.add(address)
    
    # Filter
    candidates, filter_stats = token_filter.filter_tokens_batch(deduped_tokens)
    
    # Score
    ranked_candidates = rank_tokens_by_score(candidates)
    
    return [token for token, score in ranked_candidates]


def get_sniper_candidates():
    """Get top sniper candidates using streamlined pipeline"""
    print("🔍 Scanning for sniper candidates...")
    
    try:
        raw_tokens = dexscreener_api.get_all_discovery_sources(limit=30) or []
        candidates = process_token_pipeline(raw_tokens)
        
        print(f"✅ Found {len(candidates[:5])} sniper candidates")
        return candidates[:5]
        
    except Exception as e:
        print(f"❌ Critical error: {str(e)}")
        return []
