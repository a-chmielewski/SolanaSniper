import requests
import time
from datetime import datetime

BASE_URL = "https://quote-api.jup.ag/v6"
SOL_MINT = "So11111111111111111111111111111111111111112"

class JupiterAPI:
    def __init__(self):
        self.last_request_time = 0
        self.rate_limit_delay = 0.1  # 100ms between requests
    
    def _make_request(self, url, method="GET", data=None):
        """Make rate-limited request with error handling"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        
        try:
            if method == "GET":
                response = requests.get(url, timeout=10)
            else:
                response = requests.post(url, json=data, timeout=10)
            
            self.last_request_time = time.time()
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Jupiter API Error {response.status_code}: {response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Jupiter request failed: {e}")
            return None

    def get_quote(self, input_mint, output_mint, amount, slippage_bps=50):
        """Get swap quote from Jupiter aggregator"""
        params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': str(amount),
            'slippageBps': slippage_bps,
            'onlyDirectRoutes': 'false',
            'asLegacyTransaction': 'false'
        }
        
        url = f"{BASE_URL}/quote?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return self._make_request(url)

    def get_sol_to_token_quote(self, token_mint, sol_amount, slippage_bps=50):
        """Get quote for SOL to token swap"""
        # Convert SOL amount to lamports (1 SOL = 1e9 lamports)
        sol_lamports = int(sol_amount * 1e9)
        return self.get_quote(SOL_MINT, token_mint, sol_lamports, slippage_bps)
    
    def get_usd_to_sol_amount(self, usd_amount):
        """Convert USD amount to SOL using Jupiter quote (USDC->SOL)"""
        try:
            usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            # Convert USD to USDC units (6 decimals)
            usdc_amount = int(usd_amount * 1e6)
            
            # Get quote from USDC to SOL
            quote = self.get_quote(usdc_mint, SOL_MINT, usdc_amount)
            if quote and quote.get('outAmount'):
                # Convert lamports to SOL
                sol_amount = int(quote['outAmount']) / 1e9
                return sol_amount
            return None
        except Exception:
            return None

    def get_token_to_sol_quote(self, token_mint, token_amount, token_decimals, slippage_bps=50):
        """Get quote for token to SOL swap"""
        # Convert token amount based on actual token decimals
        token_units = int(token_amount * (10 ** int(token_decimals)))
        return self.get_quote(token_mint, SOL_MINT, token_units, slippage_bps)

    def get_swap_transaction(self, quote_response, user_public_key):
        """Get serialized transaction for the swap"""
        if not quote_response:
            return None
            
        swap_data = {
            'quoteResponse': quote_response,
            'userPublicKey': user_public_key,
            'wrapAndUnwrapSol': True,
            'useSharedAccounts': True,
            'feeAccount': None,
            'trackingAccount': None,
            'asLegacyTransaction': False,
            'useTokenLedger': False,
            'destinationTokenAccount': None,
            'dynamicComputeUnitLimit': True,
            'skipUserAccountsRpcCalls': False
        }
        
        url = f"{BASE_URL}/swap"
        return self._make_request(url, method="POST", data=swap_data)

    def get_token_price_impact(self, quote_response):
        """Calculate price impact from quote"""
        if not quote_response:
            return None
            
        return {
            'price_impact_pct': quote_response.get('priceImpactPct', 0),
            'input_amount': quote_response.get('inAmount', 0),
            'output_amount': quote_response.get('outAmount', 0),
            'route_plan': len(quote_response.get('routePlan', [])),
            'other_amount_threshold': quote_response.get('otherAmountThreshold', 0)
        }

    def validate_quote_for_sniper(self, quote_response, max_price_impact=5.0):
        """Validate quote is suitable for sniping"""
        if not quote_response:
            return False, "No quote response"
            
        price_impact = float(quote_response.get('priceImpactPct', 0))
        if price_impact > max_price_impact:
            return False, f"Price impact too high: {price_impact}%"
            
        if not quote_response.get('outAmount'):
            return False, "No output amount in quote"
            
        return True, "Quote valid"

# Global instance
jupiter_api = JupiterAPI()
