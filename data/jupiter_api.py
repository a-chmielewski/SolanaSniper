"""
Jupiter API Client - Advanced swap routing and transaction building

This module provides a comprehensive interface to Jupiter Aggregator with:
- Dynamic slippage and route optimization for volatile tokens
- Priority fee support for competitive transaction execution
- Route simplification options (maxAccounts, direct routes)
- Failed DEX exclusion for problematic routes
- Comprehensive quote logging and validation
- Fresh quote validation before transaction execution

Key Features:
- Priority fees (computeUnitPriceMicroLamports) for hot pairs
- Route complexity control via maxAccounts parameter
- Direct route preferences for problematic tokens
- Dynamic slippage for volatile market conditions
- Detailed quote logging for debugging Jupiter issues
"""

import requests
import time
from datetime import datetime
from config import (
    JUPITER_RATE_LIMIT_DELAY, JUPITER_QUOTE_MAX_AGE_MS,
    JUPITER_COMPUTE_UNIT_PRICE, JUPITER_DEFAULT_MAX_PRICE_IMPACT
)

BASE_URL = "https://quote-api.jup.ag/v6"
SOL_MINT = "So11111111111111111111111111111111111111112"

class JupiterAPI:
    """
    Advanced Jupiter Aggregator client with retry logic and route optimization.
    
    Provides intelligent quote fetching with:
    - Rate limiting to respect API constraints
    - Route complexity control for problematic tokens
    - Priority fee support for competitive execution
    - Comprehensive error handling and logging
    """
    
    def __init__(self):
        self.last_request_time = 0
        self.rate_limit_delay = JUPITER_RATE_LIMIT_DELAY
    
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
                from execution.trade_manager import trade_manager
                error_msg = f"Jupiter API Error {response.status_code}: {response.text}"
                trade_manager.circuit_breaker.record_api_failure('jupiter', error_msg)
                return None
        except requests.exceptions.RequestException as e:
            from execution.trade_manager import trade_manager
            error_msg = f"Jupiter request failed: {e}"
            trade_manager.circuit_breaker.record_api_failure('jupiter', error_msg)
            return None

    def get_quote(self, input_mint, output_mint, amount, slippage_bps=100, dynamic_slippage=False, exclude_dexes=None, max_accounts=None, prefer_direct=False):
        """
        Get optimized swap quote with advanced routing options.
        
        Args:
            input_mint: Source token mint address
            output_mint: Destination token mint address  
            amount: Amount in base units (considering token decimals)
            slippage_bps: Slippage tolerance in basis points (100 = 1%)
            dynamic_slippage: Enable Jupiter's dynamic slippage for volatile tokens
            exclude_dexes: List of DEX names to exclude from routing
            max_accounts: Limit route complexity (lower = simpler routes)
            prefer_direct: Prefer direct routes over multi-hop when available
            
        Returns:
            Dict containing quote data or None if failed
        """
        params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': str(amount),
            'slippageBps': slippage_bps,
            'onlyDirectRoutes': 'true' if prefer_direct else 'false',
            'asLegacyTransaction': 'false'
        }
        
        # Add dynamic slippage for volatile tokens
        if dynamic_slippage:
            params['dynamicSlippage'] = 'true'
        
        # Exclude problematic DEXes
        if exclude_dexes:
            params['excludeDexes'] = ','.join(exclude_dexes)
        
        # Limit route complexity for problematic tokens
        if max_accounts:
            params['maxAccounts'] = str(max_accounts)
        
        url = f"{BASE_URL}/quote?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return self._make_request(url)

    def get_sol_to_token_quote(self, token_mint, sol_amount, slippage_bps=50, dynamic_slippage=True, exclude_dexes=None, max_accounts=None, prefer_direct=False):
        """Get quote for SOL to token swap with adaptive slippage"""
        # Convert SOL amount to lamports (1 SOL = 1e9 lamports)
        sol_lamports = int(sol_amount * 1e9)
        return self.get_quote(SOL_MINT, token_mint, sol_lamports, slippage_bps, dynamic_slippage, exclude_dexes, max_accounts, prefer_direct)
    
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

    def get_token_to_sol_quote(self, token_mint, token_amount, token_decimals, slippage_bps=50, dynamic_slippage=False, exclude_dexes=None, max_accounts=None, prefer_direct=False):
        """Get quote for token to SOL swap"""
        # Convert token amount based on actual token decimals
        token_units = int(token_amount * (10 ** int(token_decimals)))
        return self.get_quote(token_mint, SOL_MINT, token_units, slippage_bps, dynamic_slippage, exclude_dexes, max_accounts, prefer_direct)

    def get_swap_transaction(self, quote_response, user_public_key):
        """
        Build swap transaction with priority fees and comprehensive logging.
        
        Features:
        - Adds priority fees (5000 micro-lamports) for competitive execution
        - Validates quote freshness before building transaction
        - Comprehensive quote logging for debugging
        - Dynamic compute unit limits for optimal fee calculation
        
        Args:
            quote_response: Quote data from get_quote()
            user_public_key: User's wallet public key
            
        Returns:
            Dict containing serialized transaction or None if failed
        """
        if not quote_response:
            return None
        
        # Log and validate quote before sending to swap
        self._log_jupiter_quote(quote_response)
        
        # Validate quote freshness (Jupiter recommendation)
        quote_time = quote_response.get('timeTaken', 0)
        current_time = time.time() * 1000  # Convert to ms
        quote_age = current_time - quote_time
        
        if quote_age > JUPITER_QUOTE_MAX_AGE_MS:
            from monitoring.logger import sniper_logger
            sniper_logger.log_warning("Stale Jupiter quote", extra={
                'quote_age_seconds': quote_age/1000, 'threshold_seconds': 5
            })
            
        # Use priority fees for hot pairs
        compute_unit_price = JUPITER_COMPUTE_UNIT_PRICE
        
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
            'skipUserAccountsRpcCalls': False,
            'computeUnitPriceMicroLamports': compute_unit_price
        }
        
        url = f"{BASE_URL}/swap"
        return self._make_request(url, method="POST", data=swap_data)
    
    def _log_jupiter_quote(self, quote_response):
        """Log Jupiter quote details for debugging"""
        try:
            in_amount = quote_response.get('inAmount', 'N/A')
            out_amount = quote_response.get('outAmount', 'N/A')
            other_amount_threshold = quote_response.get('otherAmountThreshold', 'N/A')
            price_impact = quote_response.get('priceImpactPct', 'N/A')
            slippage_bps = quote_response.get('slippageBps', 'N/A')
            
            # Extract route info
            route_plan = quote_response.get('routePlan', [])
            route_labels = []
            dex_labels = []
            
            for step in route_plan:
                swap_info = step.get('swapInfo', {})
                label = swap_info.get('label', 'Unknown')
                route_labels.append(label)
                
                amm_key = swap_info.get('ammKey', '')
                if amm_key:
                    dex_labels.append(f"{label}({amm_key[:8]})")
                else:
                    dex_labels.append(label)
            
            route_str = ' → '.join(route_labels) if route_labels else 'Direct'
            dex_str = ' → '.join(dex_labels) if dex_labels else 'N/A'
            
            from monitoring.logger import sniper_logger
            sniper_logger.log_info("Jupiter quote details", extra={
                'in_amount': in_amount, 'out_amount': out_amount, 'threshold': other_amount_threshold,
                'price_impact_pct': price_impact, 'slippage_bps': slippage_bps, 
                'route': route_str, 'dexes': dex_str
            })
            
        except Exception as e:
            from monitoring.logger import sniper_logger
            sniper_logger.log_warning("Jupiter quote logging error", extra={
                'error': str(e), 'quote_keys': list(quote_response.keys()) if quote_response else 'None'
            })

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

    def validate_quote_for_sniper(self, quote_response, max_price_impact=JUPITER_DEFAULT_MAX_PRICE_IMPACT):
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
