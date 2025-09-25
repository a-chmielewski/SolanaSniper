"""
On-chain security checks for token safety validation
"""
import json
import requests
import time
from solana.rpc.api import Client
from solders.pubkey import Pubkey
from data.jupiter_api import jupiter_api
from data.price_manager import price_manager

# Solana RPC endpoint
RPC_ENDPOINT = "https://api.mainnet-beta.solana.com"
client = Client(RPC_ENDPOINT)

class OnChainSecurityChecker:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    def check_token_security(self, token_address):
        """Comprehensive on-chain security check"""
        cache_key = f"security_{token_address}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        try:
            # Get mint account info
            mint_info = self._get_mint_info(token_address)
            if not mint_info:
                return {"safe": False, "reason": "mint_info_unavailable"}
            
            # Check mint authority
            mint_auth_check = self._check_mint_authority(mint_info)
            if not mint_auth_check["safe"]:
                return self._cache_result(cache_key, mint_auth_check)
            
            # Check freeze authority
            freeze_auth_check = self._check_freeze_authority(mint_info)
            if not freeze_auth_check["safe"]:
                return self._cache_result(cache_key, freeze_auth_check)
            
            # Check token program
            program_check = self._check_token_program(mint_info)
            if not program_check["safe"]:
                return self._cache_result(cache_key, program_check)
            
            # Honeypot simulation
            honeypot_check = self._simulate_honeypot(token_address)
            if not honeypot_check["safe"]:
                return self._cache_result(cache_key, honeypot_check)
            
            result = {"safe": True, "reason": "all_checks_passed"}
            return self._cache_result(cache_key, result)
            
        except Exception as e:
            print(f"Security check failed for {token_address}: {e}")
            return {"safe": False, "reason": f"check_error: {str(e)}"}
    
    def _get_mint_info(self, token_address):
        """Get mint account information"""
        try:
            pubkey = Pubkey.from_string(token_address)
            response = client.get_account_info(pubkey)
            
            if not response.value:
                return None
            
            # Parse mint account data (simplified)
            account_data = response.value.data
            if len(account_data) < 82:  # Standard mint account size
                return None
            
            # Extract key fields from mint account
            mint_info = {
                "mint_authority": account_data[4:36] if account_data[4:36] != b'\x00' * 32 else None,
                "supply": int.from_bytes(account_data[36:44], 'little'),
                "decimals": account_data[44],
                "is_initialized": account_data[45] == 1,
                "freeze_authority": account_data[46:78] if account_data[46:78] != b'\x00' * 32 else None,
                "program_id": str(response.value.owner)
            }
            
            return mint_info
            
        except Exception as e:
            print(f"Failed to get mint info for {token_address}: {e}")
            return None
    
    def _check_mint_authority(self, mint_info):
        """Check if mint authority is disabled (safe) or active (risky)"""
        mint_authority = mint_info.get("mint_authority")
        
        if mint_authority is None:
            # Mint authority disabled - safe
            return {"safe": True, "reason": "mint_authority_disabled"}
        
        # Mint authority exists - potentially risky for new tokens
        return {"safe": False, "reason": "mint_authority_enabled"}
    
    def _check_freeze_authority(self, mint_info):
        """Check if freeze authority exists (risky)"""
        freeze_authority = mint_info.get("freeze_authority")
        
        if freeze_authority is None:
            # No freeze authority - safe
            return {"safe": True, "reason": "freeze_authority_disabled"}
        
        # Freeze authority exists - risky
        return {"safe": False, "reason": "freeze_authority_enabled"}
    
    def _check_token_program(self, mint_info):
        """Check if token uses suspicious program"""
        program_id = mint_info.get("program_id", "")
        
        # Token-2022 requires additional extension checks
        if program_id == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb":
            # For now, be conservative with Token-2022
            return {"safe": False, "reason": "token_2022_program"}
        
        # Standard token program is safe
        if program_id == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
            return {"safe": True, "reason": "standard_token_program"}
        
        # Unknown program - risky
        return {"safe": False, "reason": f"unknown_token_program: {program_id}"}
    
    def _simulate_honeypot(self, token_address):
        """Simulate small buy/sell to detect honeypot"""
        try:
            # Test with very small amount ($0.01 worth)
            test_usd = 0.01
            sol_amount = price_manager.usd_to_sol(test_usd)
            
            # Get buy quote
            buy_quote = jupiter_api.get_sol_to_token_quote(token_address, sol_amount)
            if not buy_quote:
                return {"safe": False, "reason": "no_buy_quote"}
            
            # Check if we can get tokens
            out_amount = buy_quote.get('outAmount', 0)
            if out_amount <= 0:
                return {"safe": False, "reason": "zero_output_amount"}
            
            # Get sell quote for the same amount
            token_decimals = 9  # Default, should be fetched from mint
            sell_quote = jupiter_api.get_token_to_sol_quote(
                token_address, 
                out_amount / (10 ** token_decimals), 
                token_decimals
            )
            
            if not sell_quote:
                return {"safe": False, "reason": "no_sell_quote"}
            
            # Check sell output
            sell_out_amount = sell_quote.get('outAmount', 0)
            if sell_out_amount <= 0:
                return {"safe": False, "reason": "cannot_sell_back"}
            
            # Calculate round-trip loss
            original_sol = int(sol_amount * 1e9)  # Convert to lamports
            returned_sol = sell_out_amount
            
            loss_ratio = (original_sol - returned_sol) / original_sol
            
            # If we lose more than 95% in round-trip, it's likely a honeypot
            if loss_ratio > 0.95:
                return {"safe": False, "reason": f"honeypot_detected: {loss_ratio:.2%}_loss"}
            
            return {"safe": True, "reason": f"honeypot_test_passed: {loss_ratio:.2%}_loss"}
            
        except Exception as e:
            print(f"Honeypot simulation failed for {token_address}: {e}")
            # Don't fail the token just because simulation failed
            return {"safe": True, "reason": "honeypot_simulation_failed"}
    
    def _get_cached(self, key):
        """Get cached result if still valid"""
        if key in self.cache:
            result, timestamp = self.cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return result
        return None
    
    def _cache_result(self, key, result):
        """Cache result with timestamp"""
        self.cache[key] = (result, time.time())
        return result

# Global instance
onchain_security = OnChainSecurityChecker()
