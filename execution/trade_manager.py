import time
from datetime import datetime
from data.jupiter_api import jupiter_api, SOL_MINT
from data.birdeye_api import birdeye_api
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
        
        # Check SOL balance
        sol_balance = self.wallet.get_sol_balance()
        sol_amount = BUY_AMOUNT_USD / 100  # Assume $100 per SOL for estimation
        
        if sol_balance < sol_amount:
            return {"error": f"Insufficient SOL balance: {sol_balance}"}
        
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
            
            # Record position
            position = {
                'token_address': token_address,
                'token_symbol': token_symbol,
                'entry_time': datetime.now(),
                'entry_price': token_data.get('price', 0),
                'sol_amount': sol_amount,
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
            
            # Get quote for token to SOL
            token_decimals = 9  # Default, should be fetched from token metadata
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
            pnl_percent = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
            
            # Update position
            position['exit_time'] = datetime.now()
            position['exit_price'] = current_price
            position['pnl_percent'] = pnl_percent
            position['exit_reason'] = reason
            position['status'] = 'closed'
            position['sell_tx_id'] = result.get('tx_id')
            
            # Remove from active positions
            del self.active_positions[token_address]
            self.trade_history.append({**position, 'action': 'sell'})
            
            print(f"✅ Sold {token_symbol}: {pnl_percent:+.2f}% P&L")
            return {"success": True, "position": position}
            
        except Exception as e:
            return {"error": f"Sell execution failed: {str(e)}"}
    
    def monitor_positions(self):
        """Monitor active positions for exit conditions"""
        positions_to_close = []
        
        for token_address, position in self.active_positions.items():
            try:
                current_price = birdeye_api.get_price(token_address)
                if not current_price:
                    continue
                
                entry_price = position['entry_price']
                if entry_price <= 0:
                    continue
                
                pnl_percent = (current_price - entry_price) / entry_price
                
                # Check profit target
                if pnl_percent >= TARGET_PROFIT:
                    positions_to_close.append((token_address, "profit_target"))
                    continue
                
                # Check stop loss
                if pnl_percent <= -STOP_LOSS:
                    positions_to_close.append((token_address, "stop_loss"))
                    continue
                
                # Check time-based exit (30 minutes max hold)
                time_held = (datetime.now() - position['entry_time']).total_seconds() / 60
                if time_held > 30:
                    positions_to_close.append((token_address, "time_limit"))
                    continue
                
                # Check liquidity drop (rug protection)
                token_overview = birdeye_api.get_token_overview(token_address)
                current_liquidity = token_overview.get('liquidity', 0)
                if current_liquidity < 5000:  # Less than $5k liquidity
                    positions_to_close.append((token_address, "liquidity_drop"))
                
            except Exception as e:
                print(f"Error monitoring {position.get('token_symbol', token_address)}: {e}")
        
        # Execute sells for positions that need closing
        results = []
        for token_address, reason in positions_to_close:
            result = self.execute_sniper_sell(token_address, reason)
            results.append(result)
        
        return results
    
    def get_portfolio_summary(self):
        """Get current portfolio status"""
        sol_balance = self.wallet.get_sol_balance()
        active_count = len(self.active_positions)
        
        total_pnl = 0
        for trade in self.trade_history:
            if trade.get('action') == 'sell' and 'pnl_percent' in trade:
                total_pnl += trade['pnl_percent']
        
        return {
            'sol_balance': sol_balance,
            'active_positions': active_count,
            'total_trades': len([t for t in self.trade_history if t.get('action') == 'buy']),
            'total_pnl_percent': total_pnl,
            'wallet_address': self.wallet.get_address()
        }
    
    def get_active_positions(self):
        """Get list of active positions with current P&L"""
        positions_with_pnl = []
        
        for token_address, position in self.active_positions.items():
            current_price = birdeye_api.get_price(token_address) or 0
            entry_price = position['entry_price']
            
            current_pnl = 0
            if entry_price > 0:
                current_pnl = (current_price - entry_price) / entry_price * 100
            
            position_copy = position.copy()
            position_copy['current_price'] = current_price
            position_copy['current_pnl_percent'] = current_pnl
            positions_with_pnl.append(position_copy)
        
        return positions_with_pnl

# Global instance
trade_manager = TradeManager()

def execute_sniper_trade(token_data):
    """Legacy function for backward compatibility"""
    return trade_manager.execute_sniper_buy(token_data)

def monitor_position(token_address, entry_price):
    """Legacy function for backward compatibility"""
    return trade_manager.monitor_positions()
