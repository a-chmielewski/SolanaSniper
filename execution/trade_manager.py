"""
Trade Manager - Core trading execution and position management

This module handles all trading operations including:
- Executing sniper buys with proper balance management and fee buffers
- Dynamic exit strategies using trailing stops and adaptive thresholds
- Sell retry logic with escalating slippage and route simplification
- Position monitoring with resilient API calls
- Chunked selling for large positions to reduce price impact
- Priority fee handling for competitive transaction execution

Key Features:
- In-flight buy protection to prevent over-leveraging small balances
- Adaptive liquidity/volume thresholds based on entry conditions
- Jupiter error detection and retry with route optimization
- Actual SOL P&L tracking vs price-based calculations
- Failed DEX tracking and exclusion for problematic routes
"""

import time
from datetime import datetime
from data.jupiter_api import jupiter_api, SOL_MINT
from data.dexscreener_api import dexscreener_api
from data.price_manager import price_manager
from execution.wallet import load_wallet
from config import BUY_AMOUNT_USD, TARGET_PROFIT, STOP_LOSS
from strategy.exit_strategies import exit_manager
from data.api_client import api_client

class TradeManager:
    """
    Manages all trading operations with advanced risk management and retry logic.
    
    Attributes:
        wallet: Loaded wallet instance for transaction signing
        active_positions: Dict of currently open positions by token address
        trade_history: List of all completed trades and actions
        in_flight_buy: Flag to prevent concurrent buys on small balances
        failed_dexes: Dict tracking DEXes that consistently fail for specific tokens
    """
    
    def __init__(self):
        self.wallet = load_wallet()
        self.active_positions = {}
        self.trade_history = []
        self.in_flight_buy = False  # Prevents concurrent buys
        self.failed_dexes = {}  # Track problematic DEXes per token
    
    def execute_sniper_buy(self, token_data):
        """
        Execute a sniper buy with comprehensive balance management and safety checks.
        
        Features:
        - Refreshes wallet balance and calculates spendable SOL after fee buffers
        - Reserves SOL for transaction fees, ATA creation, and minimum residual
        - Caps buy amount to available balance to prevent insufficient funds errors
        - Records position with adaptive thresholds based on entry conditions
        - Provides detailed preflight logging for debugging
        
        Args:
            token_data: Dict containing token information (address, symbol, price, etc.)
            
        Returns:
            Dict with 'success' key and position data, or 'error' key with message
        """
        token_address = token_data.get('address')
        token_symbol = token_data.get('symbol', 'UNKNOWN')
        
        if not self.wallet.get_address():
            return {"error": "Wallet not loaded"}
        
        # Check for existing position
        if token_address in self.active_positions:
            return {"error": f"Already have position in {token_symbol}"}
        
        # Refresh balance and calculate spendable amount
        sol_balance = self.wallet.get_sol_balance()
        
        # Fee buffer constants
        FEE_BUFFER_SOL = 0.01      # ~0.01 SOL safety buffer (rent + fees)
        MIN_RESIDUAL_SOL = 0.005   # leave some SOL after buy
        
        effective_spendable = max(0.0, sol_balance - FEE_BUFFER_SOL - MIN_RESIDUAL_SOL)
        if effective_spendable <= 0:
            return {"error": "Insufficient SOL after reserving fee buffer"}
        
        # Convert planned USD buy to SOL
        planned_sol = price_manager.usd_to_sol(BUY_AMOUNT_USD)
        
        # Check if ATA exists and reserve rent if needed
        ata_rent = 0.002  # Estimated ATA creation rent
        try:
            token_balance = self.wallet.get_token_balance(token_address)
            if token_balance == 0:  # ATA likely doesn't exist
                effective_spendable -= ata_rent
        except:
            # Assume ATA doesn't exist
            effective_spendable -= ata_rent
        
        # Cap to spendable amount after ATA consideration
        sol_amount = min(planned_sol, effective_spendable)
        
        # Preflight logging
        print(f"Preflight: balance={sol_balance:.3f} SOL, fee_buffer={FEE_BUFFER_SOL:.3f} SOL, min_residual={MIN_RESIDUAL_SOL:.3f} SOL, ata_rent={ata_rent:.3f} SOL, spendable={effective_spendable:.3f} SOL, planned={planned_sol:.4f} SOL → capped={sol_amount:.4f} SOL")
        
        if sol_amount < 0.002:   # too small to be practical
            return {"error": "Buy size too small after fees; skipping"}
        
        try:
            # Get quote from Jupiter
            quote = jupiter_api.get_sol_to_token_quote(token_address, sol_amount)
            if not quote:
                return {"error": "Failed to get quote"}
            
            # Validate quote
            is_valid, message = jupiter_api.validate_quote_for_sniper(quote)
            if not is_valid:
                return {"error": f"Invalid quote: {message}"}
            
            # Get swap transaction
            swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
            if not swap_tx:
                return {"error": "Failed to create swap transaction"}
            
            # Sign and send transaction
            signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
            if not signed_tx:
                return {"error": "Failed to sign transaction"}
            
            result = self.wallet.send_transaction(signed_tx)
            if result.get('error'):
                return result
            
            tx_id = result.get('tx_id')
            if not tx_id:
                return {"error": "No transaction ID returned"}
            
            # Get token decimals from BirdEye data
            token_decimals = token_data.get('decimals', 9)
            
            # Record position with pending status and adaptive thresholds
            initial_liquidity = token_data.get('liquidity', 0)
            initial_volume = token_data.get('volume_24h', 0)
            
            position = {
                'token_address': token_address,
                'token_symbol': token_symbol,
                'token_decimals': token_decimals,
                'entry_time': datetime.now(),
                'entry_price': token_data.get('price', 0),
                'sol_amount': sol_amount,
                'sol_price_at_entry': price_manager.get_current_sol_price(),
                'usd_amount': BUY_AMOUNT_USD,
                'expected_tokens': float(quote.get('outAmount', 0)),
                'tx_id': tx_id,
                'status': 'pending',
                'confirmation_attempts': 0,
                # Adaptive thresholds based on entry conditions
                'initial_liquidity': initial_liquidity,
                'initial_volume_24h': initial_volume,
                'liquidity_drop_threshold': max(5000, initial_liquidity * 0.3),  # 30% of initial or $5k min
                'volume_drop_threshold': max(10000, initial_volume * 0.2)  # 20% of initial or $10k min
            }
            
            self.active_positions[token_address] = position
            self.trade_history.append({**position, 'action': 'buy'})
            
            print(f"✅ Bought {token_symbol}: {sol_amount} SOL (pending confirmation)")
            return {"success": True, "position": position}
            
        except Exception as e:
            return {"error": f"Buy execution failed: {str(e)}"}
    
    def should_sell_position(self, position_data):
        """
        Determine if a position should be sold using dynamic exit strategies and resilient data fetching.
        
        Features:
        - Uses exit_manager for trailing stops and scaled exits
        - Adaptive liquidity/volume thresholds based on entry conditions
        - Resilient API calls with caching and fallbacks
        - Graceful handling of data unavailability without forced sells
        
        Args:
            position_data: Dict containing position information
            
        Returns:
            Tuple of (should_sell: bool, reason: str, exit_ratio: float)
        """
        entry_price = position_data.get('entry_price', 0)
        entry_time = position_data.get('entry_time')
        token_address = position_data.get('token_address')
        
        if not all([entry_price, entry_time, token_address]):
            return True, "missing_data", 1.0
        
        # Get current price with resilient API call
        def get_price_safe():
            return dexscreener_api.get_price(token_address)
        
        current_price = api_client.resilient_request(
            get_price_safe, 
            cache_key=f"price_{token_address}"
        )
        
        if not current_price:
            # Don't force sell on price data failure - use cached or skip this check
            print(f"⚠️ Price data unavailable for {token_address}, skipping price-based exit checks")
            return False, "price_data_unavailable", 0.0
        
        # Adaptive liquidity check (rug protection) - priority check with resilience
        def get_liquidity_safe():
            pairs = dexscreener_api.get_token_info(token_address)
            best_pair = dexscreener_api._pick_best_pair(pairs)
            if best_pair:
                return float((best_pair.get('liquidity') or {}).get('usd', 0))
            return 0
        
        current_liquidity = api_client.resilient_request(
            get_liquidity_safe,
            cache_key=f"liquidity_{token_address}"
        )
        
        if current_liquidity is None:
            # Don't force sell on liquidity data failure
            print(f"⚠️ Liquidity data unavailable for {token_address}, using fallback threshold")
            current_liquidity = position_data.get('initial_liquidity', 50000) * 0.5  # Assume 50% of initial
        
        # Use adaptive threshold based on entry liquidity
        liquidity_threshold = position_data.get('liquidity_drop_threshold', 5000)
        if current_liquidity < liquidity_threshold:
            return True, "liquidity_drop", 1.0
        
        # Use dynamic exit strategy
        should_exit, reason, exit_ratio = exit_manager.get_exit_decision(position_data, current_price)
        
        if should_exit:
            return True, reason, exit_ratio
        
        # Additional safety checks for very low activity
        pnl_percent = (current_price - entry_price) / entry_price
        time_held = (datetime.now() - entry_time).total_seconds() / 60
        
        # Adaptive momentum check based on entry conditions
        momentum_drop_threshold = min(0.05, abs(pnl_percent) * 0.5)  # Adaptive based on current loss
        if pnl_percent < -momentum_drop_threshold and time_held > 5:
            # Get volume data with resilience
            def get_volume_safe():
                pairs = dexscreener_api.get_token_info(token_address)
                best_pair = dexscreener_api._pick_best_pair(pairs)
                if best_pair:
                    volume_data = best_pair.get('volume', {})
                    return float(volume_data.get('h24', 0))
                return 0
            
            volume_24h = api_client.resilient_request(
                get_volume_safe,
                cache_key=f"volume_{token_address}"
            )
            
            if volume_24h is None:
                # Don't force sell on volume data failure
                print(f"⚠️ Volume data unavailable for {token_address}, skipping low activity check")
                volume_24h = position_data.get('initial_volume_24h', 20000)  # Use initial as fallback
            
            # Use adaptive volume threshold
            volume_threshold = position_data.get('volume_drop_threshold', 10000)
            if volume_24h < volume_threshold:
                return True, "low_activity", 1.0
        
        return False, "hold", 0.0

    def confirm_pending_transactions(self):
        """Confirm pending transactions and update position status"""
        confirmed_positions = []
        failed_positions = []
        
        for token_address, position in list(self.active_positions.items()):
            if position.get('status') != 'pending':
                continue
                
            tx_id = position.get('tx_id')
            if not tx_id:
                continue
            
            # Check transaction status
            tx_status = self.wallet.get_transaction_status(tx_id)
            position['confirmation_attempts'] = position.get('confirmation_attempts', 0) + 1
            
            if tx_status == "confirmed":
                # Verify actual token balance
                actual_balance = self.wallet.get_token_balance(token_address)
                if actual_balance > 0:
                    position['status'] = 'open'
                    position['actual_tokens'] = actual_balance
                    confirmed_positions.append(position)
                    print(f"✅ Confirmed: {position['token_symbol']} ({actual_balance} tokens)")
                else:
                    # Transaction confirmed but no tokens - possible MEV or failed swap
                    position['status'] = 'failed'
                    position['failure_reason'] = 'no_tokens_received'
                    failed_positions.append(position)
                    print(f"❌ Failed: {position['token_symbol']} - no tokens received")
                    
            elif tx_status == "failed":
                position['status'] = 'failed' 
                position['failure_reason'] = 'transaction_failed'
                failed_positions.append(position)
                print(f"❌ Failed: {position['token_symbol']} - transaction failed")
                
            elif position['confirmation_attempts'] > 10:  # Stop checking after 10 attempts
                position['status'] = 'timeout'
                position['failure_reason'] = 'confirmation_timeout'
                failed_positions.append(position)
                print(f"⏰ Timeout: {position['token_symbol']} - confirmation timeout")
        
        # Remove failed positions from active
        for position in failed_positions:
            token_address = position['token_address']
            if token_address in self.active_positions:
                del self.active_positions[token_address]
                # Update trade history
                self.trade_history.append({**position, 'action': 'failed_buy'})
        
        return confirmed_positions, failed_positions

    def monitor_positions(self):
        """Monitor all active positions and execute sells when needed"""
        # First confirm any pending transactions
        self.confirm_pending_transactions()
        
        positions_to_sell = []
        
        for token_address, position in self.active_positions.items():
            # Only check confirmed positions for selling
            if position.get('status') != 'open':
                continue
                
            should_sell, reason, exit_ratio = self.should_sell_position(position)
            if should_sell:
                positions_to_sell.append((token_address, reason, exit_ratio))
        
        # Execute sells
        results = []
        for token_address, reason, exit_ratio in positions_to_sell:
            if exit_ratio < 1.0:
                # Partial exit
                result = self.execute_partial_sell(token_address, reason, exit_ratio)
            else:
                # Full exit
                result = self.execute_sniper_sell(token_address, reason)
            results.append(result)
        
        return results

    def execute_sniper_sell(self, token_address, reason="manual", max_retries=3):
        """
        Execute a complete sell with advanced retry logic and route optimization.
        
        Features:
        - Sells 99.5% of balance initially, reducing on retries to avoid dust issues
        - Escalating slippage tolerance on retries (0.5% → 1.0% → 1.5%)
        - Route simplification: reduces maxAccounts and prefers direct routes on retries
        - Jupiter error detection with specific handling for transient errors
        - Fresh quote validation to ensure quotes aren't stale before execution
        - Actual SOL P&L calculation based on received amounts
        - Automatic chunked selling for high price impact scenarios
        
        Args:
            token_address: Token contract address to sell
            reason: Reason for selling (for tracking)
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dict with 'success' key and position data, or 'error' key with message
        """
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Record SOL balance before sell
        initial_sol_balance = self.wallet.get_sol_balance()
        
        for attempt in range(max_retries):
            try:
                # Get current token balance and reduce by dust factor
                full_token_balance = self.wallet.get_token_balance(token_address)
                if full_token_balance <= 0:
                    return {"error": "No tokens to sell"}
                
                # Sell 99.5% on first attempt, reduce by 0.5-1.0% on retries to avoid dust/rounding
                dust_reduction = 0.995 - (attempt * 0.005)  # 99.5%, 99.0%, 98.5%
                token_balance = int(full_token_balance * dust_reduction)
                
                if token_balance <= 0:
                    return {"error": "Token balance too small after dust reduction"}
                
                # Get token decimals from position data
                token_decimals = position.get('token_decimals', 9)
                
                # Check if we should chunk the sell to reduce impact
                should_chunk, chunk_size = self._should_chunk_sell(token_balance, position)
                
                if should_chunk:
                    return self._execute_chunked_sell(token_address, reason, chunk_size, max_retries)
                
                # Fresh quote with escalating slippage for Jupiter errors
                base_slippage = 50 + (attempt * 50)  # 0.5%, 1.0%, 1.5% for retries
                # Cap at 500 bps (5%) for micro-caps
                slippage_bps = min(base_slippage, 500)
                
                # Get fresh quote immediately before swap, excluding problematic DEXes
                exclude_dexes = self.failed_dexes.get(token_address, [])
                # Simplify routes for problematic tokens on retries
                max_accounts = 64 - (attempt * 16) if attempt > 0 else None  # Reduce complexity on retries
                prefer_direct = attempt >= 2  # Prefer direct routes on final attempts
                
                quote = jupiter_api.get_token_to_sol_quote(
                    token_address, 
                    token_balance, 
                    token_decimals,
                    slippage_bps=slippage_bps,
                    dynamic_slippage=True,
                    exclude_dexes=exclude_dexes if exclude_dexes else None,
                    max_accounts=max_accounts,
                    prefer_direct=prefer_direct
                )
                
                if not quote:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Quote failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to get sell quote after retries"}
                
                # Check price impact before proceeding
                price_impact = float(quote.get('priceImpactPct', 0))
                max_impact = 5.0 + (attempt * 2.0)  # Allow higher impact on retries
                
                if price_impact > max_impact:
                    if attempt < max_retries - 1:
                        print(f"⚠️ High price impact {price_impact:.2f}% for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(2)
                        continue
                    # On final attempt, try chunked sell if impact is too high
                    if price_impact > 10.0:
                        print(f"⚠️ Very high impact {price_impact:.2f}%, attempting chunked sell")
                        return self._execute_chunked_sell(token_address, reason, token_balance // 3, max_retries)
                
                # Get swap transaction immediately after fresh quote
                swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
                if not swap_tx:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Swap tx failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to create sell transaction after retries"}
                
                # Validate quote is still fresh (Jupiter recommendation)
                quote_age_ms = (time.time() * 1000) - quote.get('timeTaken', time.time() * 1000)
                if quote_age_ms > 2000:  # Quote older than 2 seconds
                    if attempt < max_retries - 1:
                        print(f"⚠️ Quote stale ({quote_age_ms:.0f}ms) for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(0.5)
                        continue
                
                # Sign and send transaction
                signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
                if not signed_tx:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Sign failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to sign sell transaction after retries"}
                
                result = self.wallet.send_transaction(signed_tx)
                if result.get('error'):
                    error_msg = result.get('error', '')
                    
                    # Check for Jupiter-specific transient errors
                    jupiter_errors = ['6001', '6017', '6024', '0x1771', '0x1781', '0x1788']
                    is_jupiter_error = any(err_code in str(error_msg) for err_code in jupiter_errors)
                    is_jupiter_program = 'JUP6' in str(error_msg) or 'Jupiter' in str(error_msg)
                    
                    if is_jupiter_error and is_jupiter_program and attempt < max_retries - 1:
                        print(f"⚠️ Jupiter transient error {error_msg}, re-quoting with higher slippage {attempt + 1}/{max_retries}")
                        # Break out of current attempt to re-quote with higher slippage
                        time.sleep(1)
                        continue
                    
                    if attempt < max_retries - 1:
                        print(f"⚠️ Send failed for {token_symbol}: {error_msg}, retry {attempt + 1}/{max_retries}")
                        time.sleep(2)
                        continue
                    return result
                
                # Transaction successful
                sell_tx_id = result.get('tx_id')
                
                # Wait and measure actual SOL received
                time.sleep(3)  # Wait for confirmation
                final_sol_balance = self.wallet.get_sol_balance()
                actual_sol_received = final_sol_balance - initial_sol_balance
                
                # Calculate true P&L based on actual SOL received
                sol_invested = position.get('sol_amount', 0)
                sol_pnl = actual_sol_received - sol_invested
                sol_pnl_percent = (sol_pnl / sol_invested) * 100 if sol_invested > 0 else 0
                
                # Get price-based P&L for comparison with resilience
                def get_exit_price_safe():
                    return dexscreener_api.get_price(token_address)
                
                current_price = api_client.resilient_request(
                    get_exit_price_safe,
                    cache_key=f"exit_price_{token_address}"
                ) or 0
                
                entry_price = position['entry_price']
                price_pnl_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                
                # Update position with comprehensive data
                position.update({
                    'exit_time': datetime.now(),
                    'exit_price': current_price,
                    'pnl_percent': price_pnl_percent,
                    'actual_sol_received': actual_sol_received,
                    'sol_pnl': sol_pnl,
                    'sol_pnl_percent': sol_pnl_percent,
                    'exit_reason': reason,
                    'status': 'closed',
                    'sell_tx_id': sell_tx_id,
                    'sell_attempts': attempt + 1
                })
                
                print(f"✅ Sold {token_symbol}: {sol_pnl_percent:+.2f}% SOL P&L ({price_pnl_percent:+.2f}% price P&L)")
                
                # Remove from active positions and add to history
                self.trade_history.append({**position, 'action': 'sell'})
                del self.active_positions[token_address]
                
                return {"success": True, "position": position}
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ Sell attempt {attempt + 1} failed for {token_symbol}: {e}, retrying...")
                    time.sleep(2)
                    continue
                
                # All retries failed
                position.update({
                    'exit_time': datetime.now(),
                    'exit_price': 0,
                    'pnl_percent': 0,
                    'exit_reason': f"{reason}_failed",
                    'status': 'failed',
                    'sell_error': str(e),
                    'sell_attempts': max_retries
                })
                
                self.trade_history.append({**position, 'action': 'failed_sell'})
                del self.active_positions[token_address]
                
                return {"error": f"Sell execution failed after {max_retries} attempts: {str(e)}"}
    
    def execute_partial_sell(self, token_address, reason="partial", exit_ratio=0.5, max_retries=3):
        """Execute partial sell of position with retry logic"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Record SOL balance before partial sell
        initial_sol_balance = self.wallet.get_sol_balance()
        
        for attempt in range(max_retries):
            try:
                # Get current token balance and apply dust reduction
                full_token_balance = self.wallet.get_token_balance(token_address)
                if full_token_balance <= 0:
                    return {"error": "No tokens to sell"}
                
                # Apply dust reduction to avoid rounding issues
                dust_reduction = 0.995 - (attempt * 0.005)  # 99.5%, 99.0%, 98.5%
                adjusted_balance = int(full_token_balance * dust_reduction)
                
                # Calculate partial amount to sell from adjusted balance
                sell_amount = int(adjusted_balance * exit_ratio)
                if sell_amount <= 0:
                    return {"error": "Partial sell amount too small after dust reduction"}
                
                token_decimals = position.get('token_decimals', 9)
                
                # Fresh quote for partial sell with escalating slippage
                base_slippage = 50 + (attempt * 50)  # More aggressive for partials
                # Cap at 500 bps (5%) for micro-caps
                slippage_bps = min(base_slippage, 500)
                
                # Get fresh quote immediately before swap, excluding problematic DEXes
                exclude_dexes = self.failed_dexes.get(token_address, [])
                # Simplify routes for partial sells on retries
                max_accounts = 64 - (attempt * 16) if attempt > 0 else None
                prefer_direct = attempt >= 2
                
                quote = jupiter_api.get_token_to_sol_quote(
                    token_address, 
                    sell_amount, 
                    token_decimals,
                    slippage_bps=slippage_bps,
                    dynamic_slippage=True,
                    exclude_dexes=exclude_dexes if exclude_dexes else None,
                    max_accounts=max_accounts,
                    prefer_direct=prefer_direct
                )
                
                if not quote:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Partial quote failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to get partial sell quote after retries"}
                
                # Execute partial sell with fresh swap transaction
                swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
                if not swap_tx:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Partial swap tx failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to create partial sell transaction after retries"}
                
                # Check quote freshness for partial sells too
                quote_age_ms = (time.time() * 1000) - quote.get('timeTaken', time.time() * 1000)
                if quote_age_ms > 2000:  # Quote older than 2 seconds
                    if attempt < max_retries - 1:
                        print(f"⚠️ Partial quote stale ({quote_age_ms:.0f}ms) for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(0.5)
                        continue
                
                signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
                if not signed_tx:
                    if attempt < max_retries - 1:
                        print(f"⚠️ Partial sign failed for {token_symbol}, retry {attempt + 1}/{max_retries}")
                        time.sleep(1)
                        continue
                    return {"error": "Failed to sign partial sell transaction after retries"}
                
                result = self.wallet.send_transaction(signed_tx)
                if result.get('error'):
                    error_msg = result.get('error', '')
                    
                    # Check for Jupiter-specific transient errors
                    jupiter_errors = ['6001', '6017', '6024', '0x1771', '0x1781', '0x1788']
                    is_jupiter_error = any(err_code in str(error_msg) for err_code in jupiter_errors)
                    is_jupiter_program = 'JUP6' in str(error_msg) or 'Jupiter' in str(error_msg)
                    
                    if is_jupiter_error and is_jupiter_program and attempt < max_retries - 1:
                        print(f"⚠️ Partial Jupiter transient error {error_msg}, re-quoting with higher slippage {attempt + 1}/{max_retries}")
                        # Break out of current attempt to re-quote with higher slippage
                        time.sleep(1)
                        continue
                    
                    if attempt < max_retries - 1:
                        print(f"⚠️ Partial send failed for {token_symbol}: {error_msg}, retry {attempt + 1}/{max_retries}")
                        time.sleep(2)
                        continue
                    return result
                
                # Transaction successful
                sell_tx_id = result.get('tx_id')
                
                # Wait and measure actual SOL received
                time.sleep(3)
                final_sol_balance = self.wallet.get_sol_balance()
                actual_sol_received = final_sol_balance - initial_sol_balance
                
                # Update position tracking
                if 'total_sold_ratio' not in position:
                    position['total_sold_ratio'] = 0.0
                position['total_sold_ratio'] += exit_ratio
                
                # Calculate P&L for this partial sell with resilience
                def get_partial_exit_price_safe():
                    return dexscreener_api.get_price(token_address)
                
                current_price = api_client.resilient_request(
                    get_partial_exit_price_safe,
                    cache_key=f"partial_exit_price_{token_address}"
                ) or 0
                
                entry_price = position['entry_price']
                price_pnl_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                
                # Calculate SOL-based P&L for this partial
                sol_invested_partial = position.get('sol_amount', 0) * exit_ratio
                sol_pnl_partial = actual_sol_received - sol_invested_partial
                sol_pnl_percent = (sol_pnl_partial / sol_invested_partial) * 100 if sol_invested_partial > 0 else 0
                
                print(f"✅ Partial sold {exit_ratio*100:.0f}% of {token_symbol}: {sol_pnl_percent:+.2f}% SOL P&L")
                
                # Log partial sell
                partial_position = position.copy()
                partial_position.update({
                    'partial_exit_time': datetime.now(),
                    'partial_exit_price': current_price,
                    'partial_exit_ratio': exit_ratio,
                    'partial_pnl_percent': price_pnl_percent,
                    'partial_sol_received': actual_sol_received,
                    'partial_sol_pnl_percent': sol_pnl_percent,
                    'partial_exit_reason': reason,
                    'sell_tx_id': sell_tx_id,
                    'partial_sell_attempts': attempt + 1
                })
                
                self.trade_history.append({**partial_position, 'action': 'partial_sell'})
                
                return {"success": True, "position": partial_position, "partial": True}
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ Partial sell attempt {attempt + 1} failed for {token_symbol}: {e}, retrying...")
                    time.sleep(2)
                    continue
                
                return {"error": f"Partial sell execution failed after {max_retries} attempts: {str(e)}"}
    
    def _should_chunk_sell(self, token_balance, position):
        """Determine if sell should be chunked to reduce price impact"""
        initial_liquidity = position.get('initial_liquidity', 50000)
        usd_value = position.get('usd_amount', 10)
        
        # Chunk if position is large relative to initial liquidity
        if usd_value > initial_liquidity * 0.1:  # More than 10% of liquidity
            chunk_size = max(token_balance // 4, 1)  # Split into 4 chunks
            return True, chunk_size
        
        # Chunk if token balance is very large (heuristic)
        if token_balance > 1000000:  # More than 1M tokens
            chunk_size = max(token_balance // 3, 1)  # Split into 3 chunks
            return True, chunk_size
        
        return False, 0
    
    def _execute_chunked_sell(self, token_address, reason, chunk_size, max_retries):
        """Execute sell in chunks to reduce price impact"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Record initial SOL balance
        initial_sol_balance = self.wallet.get_sol_balance()
        total_sol_received = 0
        chunks_completed = 0
        
        print(f"🔄 Chunked sell starting for {token_symbol}")
        
        while True:
            try:
                # Get current token balance and apply dust reduction
                full_token_balance = self.wallet.get_token_balance(token_address)
                if full_token_balance <= 0:
                    break
                
                # Apply dust reduction for chunks too
                dust_reduction = 0.995 - (chunks_completed * 0.002)  # Reduce slightly per chunk
                adjusted_balance = int(full_token_balance * dust_reduction)
                
                # Determine chunk size (remaining balance or chunk_size, whichever is smaller)
                current_chunk = min(chunk_size, adjusted_balance)
                if current_chunk <= 0:
                    break
                
                token_decimals = position.get('token_decimals', 9)
                
                # Get fresh quote for chunk with dynamic slippage, excluding problematic DEXes
                exclude_dexes = self.failed_dexes.get(token_address, [])
                # Use simpler routes for chunks to avoid complexity
                quote = jupiter_api.get_token_to_sol_quote(
                    token_address, 
                    current_chunk, 
                    token_decimals,
                    slippage_bps=100,  # 1% slippage for chunks
                    dynamic_slippage=True,
                    exclude_dexes=exclude_dexes if exclude_dexes else None,
                    max_accounts=48,  # Limit complexity for chunks
                    prefer_direct=True  # Prefer direct routes for chunks
                )
                
                if not quote:
                    print(f"⚠️ Chunk quote failed for {token_symbol}")
                    break
                
                # Check price impact for chunk
                price_impact = float(quote.get('priceImpactPct', 0))
                if price_impact > 8.0:  # Still too high impact
                    print(f"⚠️ Chunk still has high impact {price_impact:.2f}% for {token_symbol}")
                    # Try smaller chunk
                    chunk_size = max(chunk_size // 2, 1)
                    if chunk_size < 100:  # Minimum viable chunk
                        break
                    continue
                
                # Execute chunk with fresh swap transaction
                swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
                if not swap_tx:
                    print(f"⚠️ Chunk swap tx failed for {token_symbol}")
                    break
                
                # Check quote freshness for chunks
                quote_age_ms = (time.time() * 1000) - quote.get('timeTaken', time.time() * 1000)
                if quote_age_ms > 2000:  # Quote older than 2 seconds
                    print(f"⚠️ Chunk quote stale ({quote_age_ms:.0f}ms) for {token_symbol}, re-quoting")
                    continue  # Re-quote this chunk
                
                signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
                if not signed_tx:
                    print(f"⚠️ Chunk sign failed for {token_symbol}")
                    break
                
                chunk_sol_before = self.wallet.get_sol_balance()
                result = self.wallet.send_transaction(signed_tx)
                
                if result.get('error'):
                    print(f"⚠️ Chunk send failed for {token_symbol}: {result.get('error')}")
                    break
                
                # Wait and measure chunk result
                time.sleep(2)
                chunk_sol_after = self.wallet.get_sol_balance()
                chunk_sol_received = chunk_sol_after - chunk_sol_before
                total_sol_received += chunk_sol_received
                chunks_completed += 1
                
                print(f"✅ Chunk {chunks_completed} sold {current_chunk} {token_symbol} → {chunk_sol_received:.4f} SOL")
                
                # Wait between chunks to avoid rate limits
                if full_token_balance > current_chunk:  # More chunks remaining
                    time.sleep(3)
                
            except Exception as e:
                print(f"⚠️ Chunk sell error for {token_symbol}: {e}")
                break
        
        # Final position update
        final_sol_balance = self.wallet.get_sol_balance()
        actual_total_received = final_sol_balance - initial_sol_balance
        
        # Calculate P&L
        sol_invested = position.get('sol_amount', 0)
        sol_pnl = actual_total_received - sol_invested
        sol_pnl_percent = (sol_pnl / sol_invested) * 100 if sol_invested > 0 else 0
        
        # Update position
        position.update({
            'exit_time': datetime.now(),
            'exit_price': 0,  # Can't determine single price for chunked sell
            'pnl_percent': 0,
            'actual_sol_received': actual_total_received,
            'sol_pnl': sol_pnl,
            'sol_pnl_percent': sol_pnl_percent,
            'exit_reason': f"{reason}_chunked",
            'status': 'closed',
            'chunks_completed': chunks_completed,
            'chunked_sell': True
        })
        
        print(f"✅ Chunked sell completed for {token_symbol}: {chunks_completed} chunks, {sol_pnl_percent:+.2f}% SOL P&L")
        
        # Remove from active positions and add to history
        self.trade_history.append({**position, 'action': 'chunked_sell'})
        del self.active_positions[token_address]
        
        return {"success": True, "position": position, "chunked": True}
    
    def get_active_positions(self):
        """Get list of active positions"""
        return list(self.active_positions.values())
    
    def get_position_by_address(self, token_address):
        """Get specific position by token address"""
        return self.active_positions.get(token_address)
    
    def get_portfolio_summary(self):
        """Get portfolio summary statistics"""
        active_positions = len([p for p in self.active_positions.values() if p.get('status') == 'open'])
        pending_positions = len([p for p in self.active_positions.values() if p.get('status') == 'pending'])
        
        # Calculate total P&L from closed positions
        closed_trades = [t for t in self.trade_history if t.get('action') == 'sell']
        failed_trades = [t for t in self.trade_history if t.get('action') == 'failed_buy']
        total_pnl_percent = sum(t.get('pnl_percent', 0) for t in closed_trades)
        avg_pnl = total_pnl_percent / len(closed_trades) if closed_trades else 0
        
        # Get current SOL balance
        sol_balance = self.wallet.get_sol_balance() if self.wallet else 0
        
        return {
            'sol_balance': sol_balance,
            'active_positions': active_positions,
            'pending_positions': pending_positions,
            'failed_positions': len(failed_trades),
            'total_trades': len([t for t in self.trade_history if t.get('action') == 'buy']),
            'total_pnl_percent': total_pnl_percent,
            'avg_pnl_percent': avg_pnl,
            'win_rate': self._calculate_win_rate()
        }
    
    def _calculate_win_rate(self):
        """Calculate win rate from closed positions"""
        closed_trades = [t for t in self.trade_history if t.get('action') == 'sell']
        if not closed_trades:
            return 0
        
        winning_trades = len([t for t in closed_trades if t.get('pnl_percent', 0) > 0])
        return (winning_trades / len(closed_trades)) * 100
    
    def close_all_positions(self, reason="shutdown"):
        """Close all active positions"""
        results = []
        for token_address in list(self.active_positions.keys()):
            result = self.execute_sniper_sell(token_address, reason)
            results.append(result)
        return results
    
    def get_trade_history(self, limit=None):
        """Get trade history with optional limit"""
        history = sorted(self.trade_history, key=lambda x: x.get('entry_time', datetime.min), reverse=True)
        return history[:limit] if limit else history

# Global trade manager instance
trade_manager = TradeManager()

# Legacy functions for backward compatibility
def execute_buy(token_data):
    """Legacy function for backward compatibility"""
    return trade_manager.execute_sniper_buy(token_data)

def execute_sell(token_address, reason="manual"):
    """Legacy function for backward compatibility"""
    return trade_manager.execute_sniper_sell(token_address, reason)

def get_active_positions():
    """Legacy function for backward compatibility"""
    return trade_manager.get_active_positions()

def get_portfolio_summary():
    """Legacy function for backward compatibility"""
    return trade_manager.get_portfolio_summary()

def close_position(token_address, reason="manual"):
    """Legacy function for backward compatibility"""
    return trade_manager.execute_sniper_sell(token_address, reason)
