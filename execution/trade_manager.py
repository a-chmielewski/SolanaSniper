import time
from datetime import datetime
from data.jupiter_api import jupiter_api, SOL_MINT
from data.dexscreener_api import dexscreener_api
from data.price_manager import price_manager
from execution.wallet import load_wallet
from config import BUY_AMOUNT_USD, TARGET_PROFIT, STOP_LOSS

class TradeManager:
    def __init__(self):
        self.wallet = load_wallet()
        self.active_positions = {}
        self.trade_history = []
    
    def execute_sniper_buy(self, token_data):
        """Execute buy order for sniping"""
        token_address = token_data.get('address')
        token_symbol = token_data.get('symbol', 'UNKNOWN')
        
        if not self.wallet.get_address():
            return {"error": "Wallet not loaded"}
        
        # Check for existing position
        if token_address in self.active_positions:
            return {"error": f"Already have position in {token_symbol}"}
        
        # Check SOL balance and calculate required amount
        sol_balance = self.wallet.get_sol_balance()
        
        # Use dynamic SOL pricing
        is_valid, pricing_info = price_manager.validate_trade_amount(sol_balance, BUY_AMOUNT_USD)
        if not is_valid:
            return {"error": pricing_info}
        
        sol_amount = pricing_info['base_sol_amount']
        
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
            
            # Record position with pending status
            position = {
                'token_address': token_address,
                'token_symbol': token_symbol,
                'token_decimals': token_decimals,
                'entry_time': datetime.now(),
                'entry_price': token_data.get('price', 0),
                'sol_amount': sol_amount,
                'sol_price_at_entry': pricing_info['sol_price_used'],
                'usd_amount': BUY_AMOUNT_USD,
                'expected_tokens': float(quote.get('outAmount', 0)),
                'tx_id': tx_id,
                'status': 'pending',
                'confirmation_attempts': 0
            }
            
            self.active_positions[token_address] = position
            self.trade_history.append({**position, 'action': 'buy'})
            
            print(f"✅ Bought {token_symbol}: {sol_amount} SOL (pending confirmation)")
            return {"success": True, "position": position}
            
        except Exception as e:
            return {"error": f"Buy execution failed: {str(e)}"}
    
    def should_sell_position(self, position_data):
        """Determine if position should be sold - consolidated logic"""
        entry_price = position_data.get('entry_price', 0)
        entry_time = position_data.get('entry_time')
        token_address = position_data.get('token_address')
        
        if not all([entry_price, entry_time, token_address]):
            return True, "missing_data"
        
        # Get current price
        current_price = dexscreener_api.get_price(token_address)
        if not current_price:
            return True, "no_price_data"
        
        # Calculate P&L
        pnl_percent = (current_price - entry_price) / entry_price
        
        # Take profit
        if pnl_percent >= TARGET_PROFIT:
            return True, "take_profit"
        
        # Stop loss
        if pnl_percent <= -STOP_LOSS:
            return True, "stop_loss"
        
        # Time-based exit (30 minutes max hold)
        time_held = (datetime.now() - entry_time).total_seconds() / 60
        if time_held > 30:
            return True, "time_limit"
        
        # Liquidity check (rug protection)
        pairs = dexscreener_api.get_token_info(token_address)
        best_pair = dexscreener_api._pick_best_pair(pairs)
        current_liquidity = 0
        if best_pair:
            current_liquidity = float((best_pair.get('liquidity') or {}).get('usd', 0))
        
        if current_liquidity < 5000:  # Less than $5k liquidity left
            return True, "liquidity_drop"
        
        # Check for negative momentum - simplified without recent trades
        if pnl_percent < -0.05 and time_held > 5:  # -5% after 5 minutes
            # DexScreener doesn't provide recent trades, so use volume as proxy
            volume_24h = 0
            if best_pair:
                volume_data = best_pair.get('volume', {})
                volume_24h = float(volume_data.get('h24', 0))
            
            if volume_24h < 10000:  # Very low volume
                return True, "low_activity"
        
        return False, "hold"

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
                
            should_sell, reason = self.should_sell_position(position)
            if should_sell:
                positions_to_sell.append((token_address, reason))
        
        # Execute sells
        results = []
        for token_address, reason in positions_to_sell:
            result = self.execute_sniper_sell(token_address, reason)
            results.append(result)
        
        return results

    def execute_sniper_sell(self, token_address, reason="manual"):
        """Execute sell order for position"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        sell_tx_sent = False
        sell_tx_id = None
        
        try:
            # Get current token balance
            token_balance = self.wallet.get_token_balance(token_address)
            if token_balance <= 0:
                return {"error": "No tokens to sell"}
            
            # Get token decimals from position data (stored from buy)
            token_decimals = position.get('token_decimals', 9)
            
            # Get quote for token to SOL using correct decimals
            quote = jupiter_api.get_token_to_sol_quote(
                token_address, 
                token_balance, 
                token_decimals
            )
            
            if not quote:
                return {"error": "Failed to get sell quote"}
            
            # Get swap transaction
            swap_tx = jupiter_api.get_swap_transaction(quote, self.wallet.get_address())
            if not swap_tx:
                return {"error": "Failed to create sell transaction"}
            
            # Sign and send transaction
            signed_tx = self.wallet.sign_transaction(swap_tx.get('swapTransaction'))
            if not signed_tx:
                return {"error": "Failed to sign sell transaction"}
            
            result = self.wallet.send_transaction(signed_tx)
            if result.get('error'):
                return result
            
            # Transaction sent successfully - mark it
            sell_tx_sent = True
            sell_tx_id = result.get('tx_id')
            
        except Exception as e:
            # If transaction was sent but post-processing failed, still remove position
            if sell_tx_sent:
                position.update({
                    'exit_time': datetime.now(),
                    'exit_price': 0,
                    'pnl_percent': 0,
                    'exit_reason': f"{reason}_error",
                    'status': 'closed',
                    'sell_tx_id': sell_tx_id,
                    'error': str(e)
                })
                self.trade_history.append({**position, 'action': 'sell'})
                del self.active_positions[token_address]
                print(f"⚠️ Sold {token_symbol} (tx sent but error in post-processing: {e})")
                return {"success": True, "position": position, "warning": str(e)}
            
            return {"error": f"Sell execution failed: {str(e)}"}
        
        # Post-transaction processing (outside try-catch for transaction sending)
        try:
            # Calculate P&L
            current_price = dexscreener_api.get_price(token_address) or 0
            entry_price = position['entry_price']
            pnl_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            
            # Update position
            position.update({
                'exit_time': datetime.now(),
                'exit_price': current_price,
                'pnl_percent': pnl_percent,
                'exit_reason': reason,
                'status': 'closed',
                'sell_tx_id': sell_tx_id
            })
            
            print(f"✅ Sold {token_symbol}: {pnl_percent:+.2f}% P&L")
            
        except Exception as e:
            # Fallback if price calculation fails
            position.update({
                'exit_time': datetime.now(),
                'exit_price': 0,
                'pnl_percent': 0,
                'exit_reason': reason,
                'status': 'closed',
                'sell_tx_id': sell_tx_id,
                'price_error': str(e)
            })
            
            print(f"✅ Sold {token_symbol} (price calculation failed: {e})")
        
        # Always remove from active positions and add to history if tx was sent
        self.trade_history.append({**position, 'action': 'sell'})
        del self.active_positions[token_address]
        
        return {"success": True, "position": position}
    
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
