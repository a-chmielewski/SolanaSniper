import time
from datetime import datetime
from data.jupiter_api import jupiter_api, SOL_MINT
from data.birdeye_api import birdeye_api
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
            
            # Get token decimals from BirdEye data
            token_decimals = token_data.get('decimals', 9)
            
            # Record position
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
                'tx_id': result.get('tx_id'),
                'status': 'open'
            }
            
            self.active_positions[token_address] = position
            self.trade_history.append({**position, 'action': 'buy'})
            
            print(f"✅ Bought {token_symbol}: {sol_amount} SOL")
            return {"success": True, "position": position}
            
        except Exception as e:
            return {"error": f"Buy execution failed: {str(e)}"}
    
    def execute_sniper_sell(self, token_address, reason="manual"):
        """Execute sell order for position"""
        if token_address not in self.active_positions:
            return {"error": "No active position found"}
        
        position = self.active_positions[token_address]
        token_symbol = position['token_symbol']
        
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
            
            # Calculate P&L
            current_price = birdeye_api.get_price(token_address) or 0
            entry_price = position['entry_price']
            pnl_percent = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            
            # Update position
            position.update({
                'exit_time': datetime.now(),
                'exit_price': current_price,
                'pnl_percent': pnl_percent,
                'exit_reason': reason,
                'status': 'closed',
                'sell_tx_id': result.get('tx_id')
            })
            
            # Move to history and remove from active
            self.trade_history.append({**position, 'action': 'sell'})
            del self.active_positions[token_address]
            
            print(f"✅ Sold {token_symbol}: {pnl_percent:+.2f}% P&L")
            return {"success": True, "position": position}
            
        except Exception as e:
            return {"error": f"Sell execution failed: {str(e)}"}
    
    def get_active_positions(self):
        """Get list of active positions"""
        return list(self.active_positions.values())
    
    def get_position_by_address(self, token_address):
        """Get specific position by token address"""
        return self.active_positions.get(token_address)
    
    def get_portfolio_summary(self):
        """Get portfolio summary statistics"""
        active_positions = len(self.active_positions)
        
        # Calculate total P&L from closed positions
        closed_trades = [t for t in self.trade_history if t.get('action') == 'sell']
        total_pnl_percent = sum(t.get('pnl_percent', 0) for t in closed_trades)
        avg_pnl = total_pnl_percent / len(closed_trades) if closed_trades else 0
        
        # Get current SOL balance
        sol_balance = self.wallet.get_sol_balance() if self.wallet else 0
        
        return {
            'sol_balance': sol_balance,
            'active_positions': active_positions,
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
