#!/usr/bin/env python3
"""
Solana Sniper Bot - Complete Implementation
Automatically detects and trades new Solana tokens
"""

import time
import signal
import sys
from datetime import datetime

# Import all modules
from config import WALLET_PRIVATE_KEY
from data.price_manager import price_manager
from strategy.filters import get_sniper_candidates
from strategy.sniper_strategy import sniper_strategy
from execution.trade_manager import trade_manager
from execution.wallet import load_wallet
from monitoring.logger import sniper_logger
from monitoring.alerts import alert_system

class SolanaSniper:
    def __init__(self):
        self.running = False
        self.wallet = None
        self.scan_interval = 60  # Increased to 60s to reduce API calls
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        print(f"\n🛑 Shutdown signal received")
        self.stop()
    
    def initialize(self):
        print("🚀 Initializing Solana Sniper Bot...")
        
        if not WALLET_PRIVATE_KEY or WALLET_PRIVATE_KEY == "your-wallet-private-key":
            sniper_logger.log_error("No wallet private key configured!")
            return False
        
        self.wallet = load_wallet()
        if not self.wallet.get_address():
            sniper_logger.log_error("Failed to load wallet")
            return False
        
        sol_balance = self.wallet.get_sol_balance()
        wallet_address = self.wallet.get_address()
        
        # Get current SOL price
        sol_price = price_manager.get_current_sol_price()
        usd_balance = price_manager.sol_to_usd(sol_balance)
        
        sniper_logger.log_success(f"Wallet: {wallet_address}")
        sniper_logger.log_info(f"SOL Balance: {sol_balance:.4f} (~${usd_balance:.2f})")
        sniper_logger.log_info(f"SOL Price: ${sol_price:.2f}")
        alert_system.alert_system_startup(wallet_address, sol_balance)
        
        return True
    
    def scan_and_trade(self):
        try:
            sniper_logger.log_info("🔍 Scanning for opportunities...")
            candidates = get_sniper_candidates()
            
            if not candidates:
                sniper_logger.log_info("No candidates found")
                return
            
            for candidate in candidates:
                self.evaluate_candidate(candidate)
        except Exception as e:
            sniper_logger.log_error(f"Scan failed: {str(e)}")
    
    def evaluate_candidate(self, token_data):
        token_symbol = token_data.get('symbol', 'UNKNOWN')
        
        try:
            signal_strength = sniper_strategy.get_entry_signals(token_data)
            token_data['signal_strength'] = signal_strength  # Store for logging
            should_buy, reason = sniper_strategy.should_buy(token_data)
            
            if should_buy:
                sniper_logger.log_info(f"✅ Buy signal: {token_symbol} (Signal: {signal_strength}/100)")
                sniper_logger.log_market_opportunity(token_symbol, signal_strength, reason)
                self.execute_buy(token_data)
            else:
                sniper_logger.log_info(f"❌ Skip: {token_symbol} - {reason} (Signal: {signal_strength}/100)")
        except Exception as e:
            sniper_logger.log_error(f"Error evaluating {token_symbol}: {str(e)}")
    
    def execute_buy(self, token_data):
        token_symbol = token_data.get('symbol', 'UNKNOWN')
        token_address = token_data.get('address')
        
        try:
            # Check for duplicate position
            if token_address in trade_manager.active_positions:
                sniper_logger.log_warning(f"🚧 Already holding {token_symbol}, skipping duplicate buy")
                return
            
            active_count = len(trade_manager.get_active_positions())
            if active_count >= 5:
                sniper_logger.log_warning("Position limit reached")
                return
            
            result = trade_manager.execute_sniper_buy(token_data)
            
            if result.get('success'):
                position = result['position']
                sniper_logger.log_success(f"Bought {token_symbol}")
                sniper_logger.log_trade('buy', token_data, position)
                alert_system.alert_trade_executed('buy', token_symbol, position.get('sol_amount', 0), position.get('entry_price', 0))
            else:
                sniper_logger.log_error(f"Buy failed: {result.get('error')}")
        except Exception as e:
            sniper_logger.log_error(f"Execute buy failed: {str(e)}")
    
    def monitor_positions(self):
        try:
            results = trade_manager.monitor_positions()
            for result in results:
                if result.get('success'):
                    position = result['position']
                    token_symbol = position.get('token_symbol', 'UNKNOWN')
                    pnl = position.get('pnl_percent', 0)
                    sniper_logger.log_success(f"Sold {token_symbol}: {pnl:+.2f}%")
                    sniper_logger.log_trade('sell', {'symbol': token_symbol}, position)
                elif result.get('error'):
                    sniper_logger.log_error(f"Sell failed: {result['error']}")
        except Exception as e:
            sniper_logger.log_error(f"Position monitoring failed: {str(e)}")
    
    def print_status(self):
        try:
            portfolio = trade_manager.get_portfolio_summary()
            sol_price = price_manager.get_current_sol_price()
            usd_balance = price_manager.sol_to_usd(portfolio['sol_balance'])
            
            print("\n" + "="*50)
            print("📊 SOLANA SNIPER STATUS")
            print("="*50)
            print(f"SOL Price: ${sol_price:.2f}")
            print(f"SOL Balance: {portfolio['sol_balance']:.4f} (~${usd_balance:.2f})")
            print(f"Active Positions: {portfolio['active_positions']}")
            if portfolio.get('pending_positions', 0) > 0:
                print(f"Pending Positions: {portfolio['pending_positions']}")
            if portfolio.get('failed_positions', 0) > 0:
                print(f"Failed Positions: {portfolio['failed_positions']}")
            print(f"Total P&L: {portfolio['total_pnl_percent']:+.2f}%")
            print("="*50)
        except Exception as e:
            sniper_logger.log_error(f"Status failed: {str(e)}")
    
    def run(self):
        if not self.initialize():
            return False
        
        self.running = True
        sniper_logger.log_success("🎯 Solana Sniper Bot started!")
        
        try:
            while self.running:
                start_time = time.time()
                
                self.scan_and_trade()
                self.monitor_positions()
                
                # Status every 5 minutes
                if int(time.time()) % 300 < 30:
                    self.print_status()
                
                elapsed = time.time() - start_time
                sleep_time = max(0, self.scan_interval - elapsed)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        except KeyboardInterrupt:
            sniper_logger.log_info("Keyboard interrupt")
        except Exception as e:
            sniper_logger.log_error(f"Main loop error: {str(e)}")
        finally:
            self.stop()
    
    def stop(self):
        if not self.running:
            return

        sniper_logger.log_info("🛑 Stopping bot...")
        self.running = False

        try:
            self.print_status()
            sniper_logger.print_session_summary()
            sniper_logger.save_session_stats()
            alert_system.alert_system_shutdown("normal")
            sniper_logger.log_success("✅ Bot stopped gracefully")
        except Exception as e:
            sniper_logger.log_error(f"Shutdown error: {str(e)}")
        finally:
            sys.exit(0)

def main():
    print("🎯 Solana Sniper Bot v1.0")
    print("=" * 50)
    
    sniper = SolanaSniper()
    
    try:
        sniper.run()
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
