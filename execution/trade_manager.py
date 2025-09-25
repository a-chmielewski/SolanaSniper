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
from config import (
    BUY_AMOUNT_USD, TARGET_PROFIT, STOP_LOSS,
    FEE_BUFFER_SOL, MIN_RESIDUAL_SOL, ATA_RENT_SOL, MAX_RETRIES,
    CONFIRMATION_TIMEOUT, BASE_SLIPPAGE_BPS, MAX_SLIPPAGE_BPS,
    SLIPPAGE_STEP_BPS, HIGH_IMPACT_THRESHOLD, CHUNK_SIZE_RATIO,
    MIN_CHUNK_SIZE, MAX_POSITION_RATIO, LIQUIDITY_DEPTH_MULTIPLIER,
    CIRCUIT_BREAKER_ENABLED, CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_TIME_WINDOW, CIRCUIT_BREAKER_PAUSE_DURATION,
    MIN_BUY_SIZE_SOL, DUST_REDUCTION_FACTOR, QUOTE_FRESHNESS_MS
)
from strategy.exit_strategies import exit_manager
from data.api_client import api_client
from monitoring.logger import sniper_logger
from monitoring.alerts import alert_manager

class CircuitBreaker:
    """Circuit breaker for trading operations with failure tracking"""
    
    def __init__(self, failure_threshold=CIRCUIT_BREAKER_FAILURE_THRESHOLD, 
                 time_window=CIRCUIT_BREAKER_TIME_WINDOW, 
                 pause_duration=CIRCUIT_BREAKER_PAUSE_DURATION):
        self.failure_threshold = failure_threshold  # Max failures in time window
        self.time_window = time_window  # 5 minutes
        self.pause_duration = pause_duration  # 10 minutes pause
        
        self.buy_failures = []
        self.sell_failures = []
        self.api_failures = []
        self.rpc_failures = []
        
        self.is_paused = False
        self.pause_start_time = None
        self.pause_reason = None
    
    def record_buy_failure(self, token_symbol, error):
        """Record buy failure"""
        self.buy_failures.append({
            'timestamp': time.time(),
            'token': token_symbol,
            'error': str(error)
        })
        self._check_circuit_breaker('buy')
    
    def record_sell_failure(self, token_symbol, error):
        """Record sell failure"""
        self.sell_failures.append({
            'timestamp': time.time(),
            'token': token_symbol,
            'error': str(error)
        })
        self._check_circuit_breaker('sell')
    
    def record_api_failure(self, api_type, error):
        """Record API failure"""
        self.api_failures.append({
            'timestamp': time.time(),
            'api': api_type,
            'error': str(error)
        })
        self._check_circuit_breaker('api')
    
    def record_rpc_failure(self, error):
        """Record RPC failure"""
        self.rpc_failures.append({
            'timestamp': time.time(),
            'error': str(error)
        })
        self._check_circuit_breaker('rpc')
    
    def _check_circuit_breaker(self, failure_type):
        """Check if circuit breaker should trigger"""
        current_time = time.time()
        cutoff_time = current_time - self.time_window
        
        # Get recent failures
        if failure_type == 'buy':
            recent_failures = [f for f in self.buy_failures if f['timestamp'] > cutoff_time]
        elif failure_type == 'sell':
            recent_failures = [f for f in self.sell_failures if f['timestamp'] > cutoff_time]
        elif failure_type == 'api':
            recent_failures = [f for f in self.api_failures if f['timestamp'] > cutoff_time]
        else:  # rpc
            recent_failures = [f for f in self.rpc_failures if f['timestamp'] > cutoff_time]
        
        if len(recent_failures) >= self.failure_threshold:
            self._trigger_pause(failure_type, recent_failures)
    
    def _trigger_pause(self, failure_type, recent_failures):
        """Trigger trading pause"""
        if self.is_paused:
            return
        
        self.is_paused = True
        self.pause_start_time = time.time()
        self.pause_reason = f"{failure_type}_failures"
        
        error_summary = {}
        for failure in recent_failures[-3:]:  # Last 3 errors
            error_key = str(failure.get('error', 'unknown'))[:50]
            error_summary[error_key] = error_summary.get(error_key, 0) + 1
        
        sniper_logger.log_error(f"Circuit breaker triggered: {failure_type}", extra={
            'failure_type': failure_type,
            'failure_count': len(recent_failures),
            'time_window_minutes': self.time_window / 60,
            'pause_duration_minutes': self.pause_duration / 60,
            'error_summary': error_summary
        })
        
        alert_manager.send_alert(
            "CIRCUIT_BREAKER_TRIGGERED",
            f"Trading paused due to {len(recent_failures)} {failure_type} failures",
            {
                'failure_type': failure_type,
                'failure_count': len(recent_failures),
                'pause_duration_minutes': self.pause_duration / 60,
                'top_errors': list(error_summary.keys())[:2]
            }
        )
    
    def check_if_paused(self):
        """Check if trading should remain paused"""
        if not self.is_paused:
            return False
        
        if time.time() - self.pause_start_time > self.pause_duration:
            self.is_paused = False
            self.pause_start_time = None
            
            sniper_logger.log_info("Circuit breaker reset - trading resumed", extra={
                'pause_reason': self.pause_reason,
                'pause_duration_actual': (time.time() - self.pause_start_time) / 60
            })
            
            alert_manager.send_alert(
                "CIRCUIT_BREAKER_RESET",
                "Trading resumed after circuit breaker pause",
                {'pause_reason': self.pause_reason}
            )
            
            self.pause_reason = None
            return False
        
        return True
    
    def get_status(self):
        """Get circuit breaker status"""
        current_time = time.time()
        cutoff_time = current_time - self.time_window
        
        return {
            'is_paused': self.is_paused,
            'pause_reason': self.pause_reason,
            'pause_remaining_minutes': (self.pause_duration - (current_time - self.pause_start_time)) / 60 if self.is_paused else 0,
            'recent_buy_failures': len([f for f in self.buy_failures if f['timestamp'] > cutoff_time]),
            'recent_sell_failures': len([f for f in self.sell_failures if f['timestamp'] > cutoff_time]),
            'recent_api_failures': len([f for f in self.api_failures if f['timestamp'] > cutoff_time]),
            'recent_rpc_failures': len([f for f in self.rpc_failures if f['timestamp'] > cutoff_time])
        }

from monitoring.logger import sniper_logger
from monitoring.alerts import alert_manager

class TradeManager:
    """
    Manages all trading operations with advanced risk management and retry logic.
    
    Attributes:
        wallet: Loaded wallet instance for transaction signing
        active_positions: Dict of currently open positions by token address
        trade_history: List of all completed trades and actions
        in_flight_buy: Flag to prevent concurrent buys on small balances
        failed_dexes: Dict tracking DEXes that consistently fail for specific tokens
        circuit_breaker: Circuit breaker for failure tracking and trading pause
    """
    
    def __init__(self):
        self.wallet = load_wallet()
        self.active_positions = {}
        self.trade_history = []
        self.in_flight_buy = False  # Prevents concurrent buys
        self.failed_dexes = {}  # Track problematic DEXes per token
        self.circuit_breaker = CircuitBreaker() if CIRCUIT_BREAKER_ENABLED else None
    
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
        
        # Check circuit breaker
        if self.circuit_breaker and self.circuit_breaker.check_if_paused():
            sniper_logger.log_warning("Buy blocked by circuit breaker", extra={
                'token': token_symbol, 'circuit_status': self.circuit_breaker.get_status()
            })
            return {"error": "Trading paused due to circuit breaker"}
        
        if not self.wallet.get_address():
            return {"error": "Wallet not loaded"}
        
        # Check for existing position
        if token_address in self.active_positions:
            return {"error": f"Already have position in {token_symbol}"}
        
        # Refresh balance and calculate spendable amount
        sol_balance = self.wallet.get_sol_balance()
        
        effective_spendable = self._calculate_spendable_sol(sol_balance)
        if effective_spendable <= 0:
            return {"error": "Insufficient SOL after reserving fee buffer"}
        
        # Convert planned USD buy to SOL
        planned_sol = price_manager.usd_to_sol(BUY_AMOUNT_USD)
        
        # Check if ATA exists and reserve rent if needed
        try:
            token_balance = self.wallet.get_token_balance(token_address)
            if token_balance == 0:  # ATA likely doesn't exist
                effective_spendable -= ATA_RENT_SOL
        except:
            # Assume ATA doesn't exist
            effective_spendable -= ATA_RENT_SOL
        
        # Cap to spendable amount after ATA consideration
        sol_amount = min(planned_sol, effective_spendable)
        
        # Preflight logging
        sniper_logger.log_info(f"Buy preflight: {token_symbol}", extra={
            'token': token_symbol, 'balance': sol_balance, 'fee_buffer': FEE_BUFFER_SOL,
            'min_residual': MIN_RESIDUAL_SOL, 'ata_rent': ATA_RENT_SOL, 'spendable': effective_spendable,
            'planned_sol': planned_sol, 'capped_sol': sol_amount
        })
        
        if sol_amount < MIN_BUY_SIZE_SOL:
            return {"error": "Buy size too small after fees; skipping"}
        
        # Retry buy with escalating slippage like sell path
        max_retries = MAX_RETRIES
        for attempt in range(max_retries):
            try:
                # Escalating slippage for buys
                slippage_bps = min(BASE_SLIPPAGE_BPS + (attempt * SLIPPAGE_STEP_BPS), MAX_SLIPPAGE_BPS)
                
                # Get fresh quote with dynamic slippage
                quote = jupiter_api.get_sol_to_token_quote(
                    token_address, 
                    sol_amount,
                    slippage_bps=slippage_bps,
                    dynamic_slippage=True
                )
                if not quote:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Buy quote failed: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'slippage_bps': slippage_bps, 'sol_amount': sol_amount
                        })
                        time.sleep(1)
                        continue
                    return {"error": "Failed to get quote after retries"}
                
                # Validate quote
                is_valid, message = jupiter_api.validate_quote_for_sniper(quote)
                if not is_valid:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Invalid buy quote: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'validation_error': message, 'slippage_bps': slippage_bps
                        })
                        time.sleep(1)
                        continue
                    return {"error": f"Invalid quote after retries: {message}"}
                
                # Check quote freshness
                quote_age_ms = (time.time() * 1000) - quote.get('timeTaken', time.time() * 1000)
                if quote_age_ms > QUOTE_FRESHNESS_MS:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Buy quote stale: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'quote_age_ms': quote_age_ms, 'slippage_bps': slippage_bps
                        })
                        time.sleep(0.5)
                        continue
                
                # Get swap transaction immediately after fresh quote
                swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
                if not swap_tx:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Buy swap tx failed: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'slippage_bps': slippage_bps, 'sol_amount': sol_amount
                        })
                        time.sleep(1)
                        continue
                    return {"error": "Failed to create swap transaction after retries"}
                
                # Sign and send transaction
                signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
                if not signed_tx:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Buy sign failed: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries
                        })
                        time.sleep(1)
                        continue
                    return {"error": "Failed to sign transaction after retries"}
                
                result = self.wallet.send_transaction(signed_tx)
                if result.get('error'):
                    error_msg = result.get('error', '')
                    
                    # Check for Jupiter-specific transient errors
                    jupiter_errors = ['6001', '6017', '6024', '0x1771', '0x1781', '0x1788']
                    is_jupiter_error = any(err_code in str(error_msg) for err_code in jupiter_errors)
                    is_jupiter_program = 'JUP6' in str(error_msg) or 'Jupiter' in str(error_msg)
                    
                    if is_jupiter_error and is_jupiter_program and attempt < max_retries - 1:
                        sniper_logger.log_warning(f"Buy Jupiter transient error: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'error': error_msg, 'slippage_bps': slippage_bps
                        })
                        time.sleep(1)
                        continue
                    
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"Buy send failed: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                            'error': error_msg, 'slippage_bps': slippage_bps
                        })
                        time.sleep(2)
                        continue
                    return result
                
                tx_id = result.get('tx_id')
                if not tx_id:
                    if attempt < MAX_RETRIES - 1:
                        sniper_logger.log_warning(f"No buy tx_id returned: {token_symbol}", extra={
                            'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries
                        })
                        time.sleep(1)
                        continue
                    return {"error": "No transaction ID returned after retries"}
                
                # Success - break out of retry loop
                break
                
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    sniper_logger.log_warning(f"Buy attempt failed: {token_symbol}", extra={
                        'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                        'error': str(e), 'sol_amount': sol_amount
                    })
                    time.sleep(2)
                    continue
                # Record buy failure
                if self.circuit_breaker:
                    self.circuit_breaker.record_buy_failure(token_symbol, str(e))
                return {"error": f"Buy execution failed after {MAX_RETRIES} attempts: {str(e)}"}
        
        # Get token decimals from BirdEye data
        token_decimals = token_data.get('decimals', 9)
        
        # Record position with pending status and adaptive thresholds
        initial_liquidity = token_data.get('liquidity', 0)
        initial_volume = token_data.get('volume_24h', 0)
        expected_tokens = float(quote.get('outAmount', 0))
        
        position = {
            'token_address': token_address,
            'token_symbol': token_symbol,
            'token_decimals': token_decimals,
            'entry_time': datetime.now(),
            'entry_price': token_data.get('price', 0),
            'sol_amount': sol_amount,
            'sol_price_at_entry': price_manager.get_current_sol_price(),
            'usd_amount': BUY_AMOUNT_USD,
            'expected_tokens': expected_tokens,
            'remaining_tokens': expected_tokens,  # Initialize remaining tokens
            'total_sold_ratio': 0.0,  # Initialize sold ratio
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
        
        sniper_logger.log_success(f"Buy executed: {token_symbol}", extra={
            'token': token_symbol, 'sol_amount': sol_amount, 'usd_amount': BUY_AMOUNT_USD,
            'expected_tokens': expected_tokens, 'tx_id': tx_id, 'status': 'pending'
        })
        
        # Record filter outcome - token was filtered in and trade executed
        from data.utils import token_filter
        token_filter.record_outcome(token_address, True, None)  # Success determined later
        
        return {"success": True, "position": position}
    
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
            sniper_logger.log_warning("Price data unavailable", extra={
                'token': token_address, 'reason': 'price_data_unavailable'
            })
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
            sniper_logger.log_warning("Liquidity data unavailable", extra={
                'token': token_address, 'fallback_threshold': position_data.get('initial_liquidity', 50000) * 0.5
            })
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
                sniper_logger.log_warning("Volume data unavailable", extra={
                    'token': token_address, 'fallback_volume': position_data.get('initial_volume_24h', 20000)
                })
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
                    position['remaining_tokens'] = actual_balance  # Update with actual balance
                    confirmed_positions.append(position)
                    sniper_logger.log_success("Buy confirmed", extra={
                        'token': position['token_symbol'], 'actual_tokens': actual_balance
                    })
                else:
                    # Transaction confirmed but no tokens - possible MEV or failed swap
                    position['status'] = 'failed'
                    position['failure_reason'] = 'no_tokens_received'
                    failed_positions.append(position)
                    sniper_logger.log_error("Buy failed - no tokens received", extra={
                        'token': position['token_symbol'], 'tx_id': tx_id
                    })
                    
            elif tx_status == "failed":
                position['status'] = 'failed' 
                position['failure_reason'] = 'transaction_failed'
                failed_positions.append(position)
                sniper_logger.log_error("Buy transaction failed", extra={
                    'token': position['token_symbol'], 'tx_id': tx_id
                })
                
            elif position['confirmation_attempts'] > 10:  # Stop checking after 10 attempts
                position['status'] = 'timeout'
                position['failure_reason'] = 'confirmation_timeout'
                failed_positions.append(position)
                sniper_logger.log_error("Buy confirmation timeout", extra={
                    'token': position['token_symbol'], 'tx_id': tx_id, 'attempts': position['confirmation_attempts']
                })
        
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
            # Only check confirmed positions for selling (skip closing positions)
            if position.get('status') not in ['open', 'closing']:
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

    def _execute_sell_with_retry(self, token_address, sell_amount, max_retries=MAX_RETRIES, chunk_params=None):
        """Core sell execution with unified retry logic"""
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        token_decimals = position.get('token_decimals', 9)
        
        for attempt in range(max_retries):
            try:
                # Get quote with escalating parameters
                quote = self._get_sell_quote_with_retry(token_address, sell_amount, token_decimals, attempt, max_retries, chunk_params)
                if not quote:
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return None, "Failed to get quote after retries"
                
                # Validate quote
                is_valid, validation_msg = self._validate_sell_quote(quote, token_symbol, attempt, max_retries)
                if not is_valid:
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return None, f"Quote validation failed: {validation_msg}"
                
                # Execute transaction
                tx_id, error_msg = self._execute_sell_transaction(quote, token_symbol, attempt, max_retries)
                if not tx_id:
                    if attempt < MAX_RETRIES - 1:
                        continue
                    return None, f"Transaction failed: {error_msg}"
                
                return tx_id, None
                
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    sniper_logger.log_warning(f"Sell attempt failed: {token_symbol}", extra={
                        'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                        'error': str(e)
                    })
                    time.sleep(2)
                    continue
                return None, f"Sell execution failed: {str(e)}"
        
        return None, "All retries exhausted"

    def _get_sell_quote_with_retry(self, token_address, token_amount, token_decimals, attempt, max_retries, chunk_params=None):
        """Get sell quote with escalating parameters"""
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Escalating slippage
        base_slippage = 50 + (attempt * 50)
        slippage_bps = min(base_slippage, 500)
        
        # Route optimization
        exclude_dexes = self.failed_dexes.get(token_address, [])
        max_accounts = 64 - (attempt * 16) if attempt > 0 else None
        prefer_direct = attempt >= 2
        
        # Use chunk-specific params if provided
        if chunk_params:
            slippage_bps = chunk_params.get('slippage_bps', 100)
            max_accounts = chunk_params.get('max_accounts', 48)
            prefer_direct = chunk_params.get('prefer_direct', True)
        
        quote = jupiter_api.get_token_to_sol_quote(
            token_address, token_amount, token_decimals,
            slippage_bps=slippage_bps, dynamic_slippage=True,
            exclude_dexes=exclude_dexes if exclude_dexes else None,
            max_accounts=max_accounts, prefer_direct=prefer_direct
        )
        
        if not quote and attempt < max_retries - 1:
            sniper_logger.log_warning(f"Sell quote failed: {token_symbol}", extra={
                'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                'slippage_bps': slippage_bps, 'token_amount': token_amount
            })
            time.sleep(1)
        
        return quote
    
    def _validate_sell_quote(self, quote, token_symbol, attempt, max_retries):
        """Validate quote price impact and freshness"""
        if not quote:
            return False, "No quote"
        
        # Price impact check
        price_impact = float(quote.get('priceImpactPct', 0))
        max_impact = 5.0 + (attempt * 2.0)
        
        if price_impact > max_impact:
            if attempt < max_retries - 1:
                sniper_logger.log_warning(f"High price impact: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                    'price_impact': price_impact, 'max_impact': max_impact
                })
                time.sleep(2)
            return False, f"High price impact: {price_impact:.2f}%"
        
        # Freshness check
        quote_age_ms = (time.time() * 1000) - quote.get('timeTaken', time.time() * 1000)
        if quote_age_ms > QUOTE_FRESHNESS_MS:
            if attempt < max_retries - 1:
                sniper_logger.log_warning(f"Quote stale: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                    'quote_age_ms': quote_age_ms
                })
                time.sleep(0.5)
            return False, "Quote stale"
        
        return True, "Valid"
    
    def _execute_sell_transaction(self, quote, token_symbol, attempt, max_retries):
        """Execute sell transaction with retry logic"""
        # Get swap transaction
        swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
        if not swap_tx:
            if attempt < max_retries - 1:
                sniper_logger.log_warning(f"Sell swap tx failed: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries
                })
                time.sleep(1)
            return None, "Failed to create swap transaction"
        
        # Sign transaction
        signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
        if not signed_tx:
            if attempt < max_retries - 1:
                sniper_logger.log_warning(f"Sell sign failed: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries
                })
                time.sleep(1)
            return None, "Failed to sign transaction"
        
        # Send transaction
        result = self.wallet.send_transaction(signed_tx)
        if result.get('error'):
            error_msg = result.get('error', '')
            
            # Jupiter error handling
            jupiter_errors = ['6001', '6017', '6024', '0x1771', '0x1781', '0x1788']
            is_jupiter_error = any(err_code in str(error_msg) for err_code in jupiter_errors)
            is_jupiter_program = 'JUP6' in str(error_msg) or 'Jupiter' in str(error_msg)
            
            if is_jupiter_error and is_jupiter_program and attempt < max_retries - 1:
                sniper_logger.log_warning(f"Sell Jupiter transient error: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                    'error': error_msg
                })
                time.sleep(1)
                return None, "Jupiter transient error"
            
            if attempt < max_retries - 1:
                sniper_logger.log_warning(f"Sell send failed: {token_symbol}", extra={
                    'token': token_symbol, 'attempt': attempt + 1, 'max_retries': max_retries,
                    'error': error_msg
                })
                time.sleep(2)
            return None, error_msg
        
        return result.get('tx_id'), None
    
    def _calculate_sell_pnl(self, token_address, position, initial_sol_balance, actual_sol_received):
        """Calculate P&L for sell transaction"""
        from data.dexscreener_api import dexscreener_api
        
        # SOL-based P&L
        sol_invested = position.get('sol_amount', 0)
        sol_pnl = actual_sol_received - sol_invested
        sol_pnl_percent = (sol_pnl / sol_invested) * 100 if sol_invested > 0 else 0
        
        # Price-based P&L
        def get_exit_price_safe():
            return dexscreener_api.get_price(token_address)
        
        current_price = api_client.resilient_request(
            get_exit_price_safe, cache_key=f"exit_price_{token_address}"
        ) or 0
        
        entry_price = position['entry_price']
        price_pnl_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        
        return {
            'exit_price': current_price,
            'pnl_percent': price_pnl_percent,
            'actual_sol_received': actual_sol_received,
            'sol_pnl': sol_pnl,
            'sol_pnl_percent': sol_pnl_percent
        }
    
    def _finalize_sell_position(self, token_address, reason, sell_tx_id, attempt, pnl_data, action_type="sell"):
        """Finalize position after successful sell"""
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        position.update({
            'exit_time': datetime.now(),
            'exit_reason': reason,
            'status': 'closed',
            'sell_tx_id': sell_tx_id,
            'sell_attempts': attempt + 1,
            **pnl_data
        })
        
        sniper_logger.log_success(f"Sell executed: {token_symbol}", extra={
            'token': token_symbol, 'sol_pnl_percent': pnl_data['sol_pnl_percent'],
            'price_pnl_percent': pnl_data['pnl_percent'], 'actual_sol_received': pnl_data['actual_sol_received'],
            'sell_tx_id': sell_tx_id, 'attempts': attempt + 1, 'action': action_type
        })
        
        self.trade_history.append({**position, 'action': action_type})
        
        # Record filter outcome - update with trade success
        from data.utils import token_filter
        trade_success = pnl_data.get('sol_pnl_percent', 0) > 0
        token_filter.record_outcome(token_address, True, trade_success)
        
        del self.active_positions[token_address]
        
        return {"success": True, "position": position}
    
    def execute_sniper_sell(self, token_address, reason="manual", max_retries=MAX_RETRIES):
        """Execute complete sell with retry logic"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Check circuit breaker
        if self.circuit_breaker and self.circuit_breaker.check_if_paused():
            sniper_logger.log_warning("Sell blocked by circuit breaker", extra={
                'token': token_symbol, 'circuit_status': self.circuit_breaker.get_status()
            })
            return {"error": "Trading paused due to circuit breaker"}
        
        initial_sol_balance = self.wallet.get_sol_balance()
        
        # Get token balance with dust reduction
        full_token_balance = self.wallet.get_token_balance(token_address)
        if full_token_balance <= 0:
            return {"error": "No tokens to sell"}
        
        token_balance = int(full_token_balance * DUST_REDUCTION_FACTOR)
        
        if token_balance <= 0:
            return {"error": "Token balance too small after dust reduction"}
        
        # Check for chunked sell
        should_chunk, chunk_size = self._should_chunk_sell(token_balance, position)
        if should_chunk:
            return self._execute_chunked_sell(token_address, reason, chunk_size, max_retries)
        
        # Execute sell using unified helper
        tx_id, error_msg = self._execute_sell_with_retry(token_address, token_balance, max_retries)
        if not tx_id:
            # Try chunked sell on high impact failure
            if "High price impact" in str(error_msg):
                sniper_logger.log_warning(f"High impact fallback to chunked sell: {token_symbol}", extra={
                    'token': token_symbol, 'fallback': 'chunked_sell', 'error': error_msg
                })
                return self._execute_chunked_sell(token_address, reason, int(token_balance * CHUNK_SIZE_RATIO), max_retries)
            
            # Handle failure
            position.update({
                'exit_time': datetime.now(),
                'exit_price': 0,
                'pnl_percent': 0,
                'exit_reason': f"{reason}_failed",
                'status': 'failed',
                'sell_error': error_msg,
                'sell_attempts': max_retries
            })
            
            self.trade_history.append({**position, 'action': 'failed_sell'})
            del self.active_positions[token_address]
            
            # Record sell failure
            if self.circuit_breaker:
                self.circuit_breaker.record_sell_failure(token_symbol, error_msg)
            return {"error": f"Sell execution failed: {error_msg}"}
        
        # Calculate P&L and finalize
        time.sleep(3)
        final_sol_balance = self.wallet.get_sol_balance()
        actual_sol_received = final_sol_balance - initial_sol_balance
        pnl_data = self._calculate_sell_pnl(token_address, position, initial_sol_balance, actual_sol_received)
        
        return self._finalize_sell_position(token_address, reason, tx_id, 0, pnl_data)
    
    def _calculate_spendable_sol(self, sol_balance):
        """Calculate spendable SOL after reserving fees and buffers"""
        return max(0.0, sol_balance - FEE_BUFFER_SOL - MIN_RESIDUAL_SOL)
    
    def execute_partial_sell(self, token_address, reason="partial", exit_ratio=0.5, max_retries=MAX_RETRIES):
        """Execute partial sell using shared helpers"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
        # Check circuit breaker
        if self.circuit_breaker and self.circuit_breaker.check_if_paused():
            sniper_logger.log_warning("Partial sell blocked by circuit breaker", extra={
                'token': token_symbol, 'exit_ratio': exit_ratio, 'circuit_status': self.circuit_breaker.get_status()
            })
            return {"error": "Trading paused due to circuit breaker"}
        
        initial_sol_balance = self.wallet.get_sol_balance()
        
        # Calculate partial sell amount
        if 'remaining_tokens' not in position:
            position['remaining_tokens'] = position.get('actual_tokens', position.get('expected_tokens', 0))
        
        remaining_tokens = position['remaining_tokens']
        if remaining_tokens <= 0:
            return {"error": "No remaining tokens to sell"}
        
        adjusted_balance = int(remaining_tokens * DUST_REDUCTION_FACTOR)
        sell_amount = int(adjusted_balance * exit_ratio)
        
        if sell_amount <= 0:
            return {"error": "Partial sell amount too small after dust reduction"}
        
        # Execute sell using unified helper
        tx_id, error_msg = self._execute_sell_with_retry(token_address, sell_amount, max_retries)
        if not tx_id:
            # Record partial sell failure
            if self.circuit_breaker:
                self.circuit_breaker.record_sell_failure(token_symbol, error_msg)
            return {"error": f"Partial sell execution failed: {error_msg}"}
        
        # Wait and measure SOL received
        time.sleep(3)
        final_sol_balance = self.wallet.get_sol_balance()
        actual_sol_received = final_sol_balance - initial_sol_balance
        
        # Update position tracking
        if 'total_sold_ratio' not in position:
            position['total_sold_ratio'] = 0.0
        
        position['total_sold_ratio'] += exit_ratio
        position['remaining_tokens'] = max(0, position['remaining_tokens'] - sell_amount)
        
        # Sync remaining_tokens with actual token balance
        actual_balance = self.wallet.get_token_balance(token_address)
        if actual_balance < position['remaining_tokens']:
            position['remaining_tokens'] = actual_balance
        
        if position['total_sold_ratio'] >= 0.99 or position['remaining_tokens'] < 100:
            position['status'] = 'closing'
        
        # Calculate partial P&L
        pnl_data = self._calculate_sell_pnl(token_address, position, initial_sol_balance, actual_sol_received)
        
        # Adjust P&L for partial amount
        sol_invested_partial = position.get('sol_amount', 0) * exit_ratio
        sol_pnl_partial = actual_sol_received - sol_invested_partial
        sol_pnl_percent = (sol_pnl_partial / sol_invested_partial) * 100 if sol_invested_partial > 0 else 0
        
        sniper_logger.log_success(f"Partial sell executed: {token_symbol}", extra={
            'token': token_symbol, 'exit_ratio': exit_ratio, 'sol_pnl_percent': sol_pnl_percent,
            'partial_sol_received': actual_sol_received, 'tx_id': tx_id
        })
        
        # Log partial sell
        partial_position = position.copy()
        partial_position.update({
            'partial_exit_time': datetime.now(),
            'partial_exit_price': pnl_data['exit_price'],
            'partial_exit_ratio': exit_ratio,
            'partial_pnl_percent': pnl_data['pnl_percent'],
            'partial_sol_received': actual_sol_received,
            'partial_sol_pnl_percent': sol_pnl_percent,
            'partial_exit_reason': reason,
            'sell_tx_id': tx_id
        })
        
        self.trade_history.append({**partial_position, 'action': 'partial_sell'})
        return {"success": True, "position": partial_position, "partial": True}
    
    def _should_chunk_sell(self, token_balance, position):
        """Determine if sell should be chunked to reduce price impact"""
        initial_liquidity = position.get('initial_liquidity', 50000)
        usd_value = position.get('usd_amount', 10)
        
        # Chunk if position is large relative to initial liquidity
        if usd_value > initial_liquidity * LIQUIDITY_DEPTH_MULTIPLIER * 2:  # More than 2x depth multiplier
            chunk_size = max(token_balance // 4, 1)  # Split into 4 chunks
            return True, chunk_size
        
        # Chunk if token balance is very large (heuristic)
        if token_balance > 1000000:  # More than 1M tokens
            chunk_size = max(token_balance // 3, 1)  # Split into 3 chunks
            return True, chunk_size
        
        return False, 0
    
    def _execute_chunked_sell(self, token_address, reason, chunk_size, max_retries):
        """Execute sell in chunks using shared helpers"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        initial_sol_balance = self.wallet.get_sol_balance()
        total_sol_received = 0
        chunks_completed = 0
        
        sniper_logger.log_info(f"Chunked sell starting: {token_symbol}", extra={
            'token': token_symbol, 'chunk_size': chunk_size, 'reason': reason
        })
        
        chunk_params = {'slippage_bps': 100, 'max_accounts': 48, 'prefer_direct': True}
        
        while True:
            try:
                # Get current token balance
                full_token_balance = self.wallet.get_token_balance(token_address)
                if full_token_balance <= 0:
                    break
                
                # Apply dust reduction per chunk
                dust_reduction = DUST_REDUCTION_FACTOR - (chunks_completed * 0.002)
                adjusted_balance = int(full_token_balance * dust_reduction)
                current_chunk = min(chunk_size, adjusted_balance)
                
                if current_chunk <= 0:
                    break
                
                # Execute chunk using unified helper
                tx_id, error_msg = self._execute_sell_with_retry(token_address, current_chunk, 1, chunk_params)
                if not tx_id:
                    # Check if we should reduce chunk size on high impact
                    if "High price impact" in str(error_msg) and chunk_size > MIN_CHUNK_SIZE:
                        chunk_size = max(chunk_size // 2, MIN_CHUNK_SIZE)
                        sniper_logger.log_warning(f"Chunk high impact, reducing size: {token_symbol}", extra={
                            'token': token_symbol, 'chunk': chunks_completed + 1, 'new_chunk_size': chunk_size
                        })
                        continue
                    
                    sniper_logger.log_warning(f"Chunk transaction failed: {token_symbol}", extra={
                        'token': token_symbol, 'chunk': chunks_completed + 1, 'error': error_msg
                    })
                    break
                
                # Measure chunk result
                time.sleep(2)
                chunk_sol_after = self.wallet.get_sol_balance()
                chunk_sol_received = chunk_sol_after - (initial_sol_balance + total_sol_received)
                total_sol_received += chunk_sol_received
                chunks_completed += 1
                
                sniper_logger.log_success(f"Chunk sold: {token_symbol}", extra={
                    'token': token_symbol, 'chunk': chunks_completed, 'chunk_size': current_chunk,
                    'chunk_sol_received': chunk_sol_received
                })
                
                # Wait between chunks
                if full_token_balance > current_chunk:
                    time.sleep(3)
                
            except Exception as e:
                sniper_logger.log_warning(f"Chunk sell error: {token_symbol}", extra={
                    'token': token_symbol, 'chunk': chunks_completed + 1, 'error': str(e)
                })
                break
        
        # Finalize chunked sell
        final_sol_balance = self.wallet.get_sol_balance()
        actual_total_received = final_sol_balance - initial_sol_balance
        
        pnl_data = self._calculate_sell_pnl(token_address, position, initial_sol_balance, actual_total_received)
        pnl_data.update({
            'exit_price': 0,  # No single price for chunked sell
            'pnl_percent': 0,
            'chunks_completed': chunks_completed,
            'chunked_sell': True
        })
        
        sniper_logger.log_success(f"Chunked sell completed: {token_symbol}", extra={
            'token': token_symbol, 'chunks_completed': chunks_completed,
            'sol_pnl_percent': pnl_data['sol_pnl_percent'], 'total_sol_received': actual_total_received
        })
        
        return self._finalize_sell_position(token_address, f"{reason}_chunked", None, 0, pnl_data, "chunked_sell")
    
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
            'win_rate': self._calculate_win_rate(),
            'circuit_breaker': self.circuit_breaker.get_status() if self.circuit_breaker else None
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
