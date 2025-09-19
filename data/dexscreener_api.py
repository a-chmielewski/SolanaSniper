import requests
import time
from datetime import datetime, timedelta

BASE_URL = "https://api.dexscreener.com"

class DexScreenerAPI:
    def __init__(self):
        self.headers = {
            "accept": "application/json"
        }
        self.last_request_time = 0
        self.rate_limit_delay = 1.0  # 1 second between requests (60 RPM = 1 RPS)
    
    def _first_list(self, obj):
        """Extract first list from response object"""
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list) and v:
                    return v
        return []
    
    def _pick_best_pair(self, pairs):
        """Pick best pair from list - prefer USDC/SOL quotes, otherwise highest liquidity"""
        if not pairs:
            return None
        def score(p):
            qsym = ((p.get('quoteToken') or {}).get('symbol') or '').upper()
            liq = (p.get('liquidity') or {}).get('usd', 0) or 0
            pref = 2 if qsym in ('USDC', 'SOL', 'WSOL') else 0
            return (pref, liq)
        return max(pairs, key=score)
    
    def _make_request(self, url):
        """Make rate-limited request with error handling"""
        # Rate limiting - ensure 1 RPS for 60 RPM limit
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
                print(f"DexScreener API Error {response.status_code}: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None

    def get_latest_tokens(self, limit=20):
        """Fetch latest token profiles from DexScreener"""
        url = f"{BASE_URL}/token-profiles/latest/v1"
        result = self._make_request(url)
        items = self._first_list(result)[:limit]
        return [it for it in items if (it.get('chainId') == 'solana')]

    def get_boosted_tokens(self, limit=20):
        """Get boosted/trending tokens from DexScreener"""
        url = f"{BASE_URL}/token-boosts/latest/v1"
        result = self._make_request(url)
        items = self._first_list(result)[:limit]
        return [it for it in items if (it.get('chainId') == 'solana')]

    def get_top_boosted_tokens(self, limit=20):
        """Get top boosted tokens from DexScreener"""
        url = f"{BASE_URL}/token-boosts/top/v1"
        result = self._make_request(url)
        items = self._first_list(result)[:limit]
        return [it for it in items if (it.get('chainId') == 'solana')]

    def get_token_info(self, token_addresses):
        """Get detailed token information for multiple addresses"""
        if isinstance(token_addresses, str):
            token_addresses = [token_addresses]
        
        # Limit to 30 addresses per request as per DexScreener API
        addresses_str = ",".join(token_addresses[:30])
        url = f"{BASE_URL}/tokens/v1/solana/{addresses_str}"
        result = self._make_request(url)
        return result if isinstance(result, list) else []

    def get_price(self, token_address):
        """Get current token price"""
        pairs = self.get_token_info(token_address)
        best = self._pick_best_pair(pairs)
        if not best:
            return None
        try:
            return float(best.get('priceUsd') or 0)
        except:
            return None


    def get_trending_tokens(self, limit=20):
        latest  = self.get_latest_tokens(limit//2)
        boosted = self.get_boosted_tokens(limit - len(latest))
        # Optional: top boosted to diversify
        if len(latest) + len(boosted) < limit:
            topb = self.get_top_boosted_tokens(limit - len(latest) - len(boosted))
        else:
            topb = []

        seen, merged = set(), []
        for it in latest + boosted + topb:
            if it.get('chainId') != 'solana':
                continue
            addr = it.get('tokenAddress')
            if not addr or addr in seen:
                continue
            seen.add(addr)
            merged.append(it)
            if len(merged) >= limit:
                break
        return merged


    def format_token_data(self, token_data):
        """Format DexScreener token data to match expected schema"""
        if not isinstance(token_data, dict):
            return None

        # Case 1: objects from /token-profiles or /token-boosts
        if token_data.get('tokenAddress'):
            if token_data.get('chainId') != 'solana':
                return None
            address = token_data['tokenAddress']
            symbol = token_data.get('symbol') or token_data.get('tokenSymbol') or ''
            name = token_data.get('name') or ''
            # bare minimum; enrichment will populate metrics
            return {
                'address': address, 'symbol': symbol, 'name': name,
                'decimals': 9, 'supply': 0,
                'market_cap': 0.0, 'price': 0.0, 'price_24h_change': 0.0,
                'volume_24h': 0.0, 'liquidity': 0.0, 'fdv': 0.0,
                'quote_symbol': '',
                'created_at': int(time.time()),
                'last_trade_ts': 0,
            }

        # Case 2: pair object (from /tokens/v1 already)
        base = (token_data.get('baseToken') or {})
        address = base.get('address')
        symbol = base.get('symbol', '')
        name = base.get('name', '')
        if not address or not symbol:
            return None

        price = float(token_data.get('priceUsd') or 0)
        liq = float((token_data.get('liquidity') or {}).get('usd', 0) or 0)
        mcap = float(token_data.get('marketCap') or 0)
        vol = token_data.get('volume') or {}
        chg = token_data.get('priceChange') or {}
        created_ms = token_data.get('pairCreatedAt') or 0

        return {
            'address': address, 'symbol': symbol, 'name': name,
            'decimals': 9, 'supply': 0,
            'market_cap': mcap, 'price': price,
            'price_24h_change': float(chg.get('h24', 0) or 0),
            'volume_24h': float(vol.get('h24', 0) or 0),
            'liquidity': liq,
            'fdv': float(token_data.get('fdv') or 0),
            'quote_symbol': (token_data.get('quoteToken') or {}).get('symbol') or '',
            'created_at': int(created_ms/1000) if created_ms else 0,
            'last_trade_ts': 0,  # DS doesn't give per-trade timestamp
        }

    def enrich_with_overview(self, formatted_tokens):
        """Enrich tokens with additional data (batch request)"""
        if not formatted_tokens:
            return formatted_tokens

        addrs = list({t['address'] for t in formatted_tokens if t.get('address')})[:30]
        pairs = self.get_token_info(addrs)  # returns a LIST of pair objects

        # index: baseToken.address -> best pair
        index = {}
        for p in pairs or []:
            liq = (p.get('liquidity') or {}).get('usd', 0) or 0
            if liq <= 0:
                continue
            base = (p.get('baseToken') or {}).get('address')
            if not base:
                continue
            prev = index.get(base)
            index[base] = self._pick_best_pair([prev, p] if prev else [p])

        enriched = []
        for t in formatted_tokens:
            addr = t.get('address')
            p = index.get(addr)
            if p:
                base = p.get('baseToken') or {}
                # backfill metadata if missing
                if not t.get('symbol'):
                    t['symbol'] = base.get('symbol') or t.get('symbol') or ''
                if not t.get('name'):
                    t['name'] = base.get('name') or t.get('name') or ''
                # add quote symbol for scoring
                quote = p.get('quoteToken') or {}
                t['quote_symbol'] = quote.get('symbol') or ''
                # numbers
                t['price'] = float(p.get('priceUsd') or t.get('price') or 0)
                t['market_cap'] = float(p.get('marketCap') or t.get('market_cap') or 0)
                t['liquidity'] = float((p.get('liquidity') or {}).get('usd', 0)) or t['liquidity']
                t['fdv'] = float(p.get('fdv') or t.get('fdv') or 0)
                vol = p.get('volume') or {}
                chg = p.get('priceChange') or {}
                t['volume_24h'] = float(vol.get('h24', t.get('volume_24h', 0)) or 0)
                t['price_24h_change'] = float(chg.get('h24', t.get('price_24h_change', 0)) or 0)
                created_ms = p.get('pairCreatedAt') or 0
                if created_ms and isinstance(created_ms, (int, float)):
                    t['created_at'] = int(created_ms / 1000)
            enriched.append(t)
        return enriched

# Global instance
dexscreener_api = DexScreenerAPI()
