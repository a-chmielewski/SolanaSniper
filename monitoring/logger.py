import os
import json
import csv
from datetime import datetime
from pathlib import Path

class SniperLogger:
    def __init__(self):
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(exist_ok=True)
        
        # Log files
        self.trade_log_file = self.logs_dir / "trades.csv"
        self.error_log_file = self.logs_dir / "errors.log"
        self.info_log_file = self.logs_dir / "info.log"
        self.performance_file = self.logs_dir / "performance.json"
        
        # Initialize CSV headers if files don't exist
        self._init_csv_files()
        
        # Performance tracking
        self.session_stats = {
            'start_time': datetime.now().isoformat(),
            'trades_attempted': 0,
            'trades_successful': 0,
            'total_pnl': 0,
            'best_trade': 0,
            'worst_trade': 0,
            'tokens_scanned': 0,
            'candidates_found': 0
        }
    
    def _init_csv_files(self):
        """Initialize CSV files with headers"""
        if not self.trade_log_file.exists():
            with open(self.trade_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'action', 'token_symbol', 'token_address',
                    'amount_sol', 'price', 'market_cap', 'liquidity',
                    'tx_id', 'pnl_percent', 'reason', 'signal_strength'
                ])
    
    def _get_timestamp(self):
        """Get formatted timestamp"""
        return datetime.now().isoformat()
    
    def _write_to_file(self, filepath, message):
        """Write message to file with timestamp"""
        timestamp = self._get_timestamp()
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {message}\n")
    
    def log_trade(self, action, token_data, trade_data=None):
        """Log trade execution details"""
        timestamp = self._get_timestamp()
        
        # Console output
        symbol = token_data.get('symbol', 'UNKNOWN')
        amount = trade_data.get('sol_amount', 0) if trade_data else 0
        price = token_data.get('price', 0)
        
        print(f"🔄 [{timestamp}] {action.upper()} {symbol} | Amount: {amount} SOL | Price: ${price:.8f}")
        
        # CSV logging
        try:
            with open(self.trade_log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    timestamp,
                    action,
                    token_data.get('symbol', ''),
                    token_data.get('address', ''),
                    trade_data.get('sol_amount', 0) if trade_data else 0,
                    token_data.get('price', 0),
                    token_data.get('market_cap', 0),
                    token_data.get('liquidity', 0),
                    trade_data.get('tx_id', '') if trade_data else '',
                    trade_data.get('pnl_percent', 0) if trade_data else 0,
                    trade_data.get('exit_reason', '') if trade_data else '',
                    token_data.get('signal_strength', 0)
                ])
        except Exception as e:
            self.log_error(f"Failed to write trade to CSV: {e}")
        
        # Update session stats
        if action == 'buy':
            self.session_stats['trades_attempted'] += 1
        elif action == 'sell' and trade_data:
            pnl = trade_data.get('pnl_percent', 0)
            self.session_stats['total_pnl'] += pnl
            self.session_stats['best_trade'] = max(self.session_stats['best_trade'], pnl)
            self.session_stats['worst_trade'] = min(self.session_stats['worst_trade'], pnl)
            if trade_data.get('tx_id'):
                self.session_stats['trades_successful'] += 1
    
    def log_scan_results(self, tokens_scanned, candidates_found):
        """Log token scanning results"""
        self.session_stats['tokens_scanned'] += tokens_scanned
        self.session_stats['candidates_found'] += candidates_found
        
        timestamp = self._get_timestamp()
        message = f"Scan complete: {tokens_scanned} tokens → {candidates_found} candidates"
        print(f"🔍 [{timestamp}] {message}")
        self._write_to_file(self.info_log_file, message)
    
    def log_position_update(self, token_symbol, current_pnl, reason="monitoring"):
        """Log position monitoring updates"""
        timestamp = self._get_timestamp()
        message = f"Position {token_symbol}: {current_pnl:+.2f}% P&L ({reason})"
        print(f"📊 [{timestamp}] {message}")
        self._write_to_file(self.info_log_file, message)
    
    def log_error(self, message):
        """Log error messages"""
        timestamp = self._get_timestamp()
        print(f"❌ [{timestamp}] ERROR: {message}")
        self._write_to_file(self.error_log_file, f"ERROR: {message}")
    
    def log_warning(self, message):
        """Log warning messages"""
        timestamp = self._get_timestamp()
        print(f"⚠️  [{timestamp}] WARNING: {message}")
        self._write_to_file(self.info_log_file, f"WARNING: {message}")
    
    def log_info(self, message):
        """Log informational messages"""
        timestamp = self._get_timestamp()
        print(f"ℹ️  [{timestamp}] INFO: {message}")
        self._write_to_file(self.info_log_file, f"INFO: {message}")
    
    def log_success(self, message):
        """Log success messages"""
        timestamp = self._get_timestamp()
        print(f"✅ [{timestamp}] SUCCESS: {message}")
        self._write_to_file(self.info_log_file, f"SUCCESS: {message}")
    
    def log_wallet_status(self, wallet_address, sol_balance, active_positions):
        """Log wallet status"""
        message = f"Wallet {wallet_address[:8]}... | SOL: {sol_balance:.4f} | Active: {active_positions}"
        self.log_info(message)
    
    def log_market_opportunity(self, token_symbol, signal_strength, reason):
        """Log market opportunities detected"""
        timestamp = self._get_timestamp()
        message = f"Opportunity: {token_symbol} (Signal: {signal_strength}/100) - {reason}"
        print(f"💡 [{timestamp}] {message}")
        self._write_to_file(self.info_log_file, message)
    
    def save_session_stats(self):
        """Save session statistics to JSON"""
        try:
            self.session_stats['end_time'] = datetime.now().isoformat()
            self.session_stats['session_duration'] = (
                datetime.now() - datetime.fromisoformat(self.session_stats['start_time'])
            ).total_seconds()
            
            # Load existing stats
            existing_stats = []
            if self.performance_file.exists():
                with open(self.performance_file, 'r') as f:
                    existing_stats = json.load(f)
            
            # Append current session
            existing_stats.append(self.session_stats)
            
            # Keep only last 100 sessions
            if len(existing_stats) > 100:
                existing_stats = existing_stats[-100:]
            
            # Save updated stats
            with open(self.performance_file, 'w') as f:
                json.dump(existing_stats, f, indent=2)
                
        except Exception as e:
            self.log_error(f"Failed to save session stats: {e}")
    
    def get_session_summary(self):
        """Get current session summary"""
        stats = self.session_stats
        success_rate = 0
        if stats['trades_attempted'] > 0:
            success_rate = (stats['trades_successful'] / stats['trades_attempted']) * 100
        
        return {
            'trades_attempted': stats['trades_attempted'],
            'trades_successful': stats['trades_successful'],
            'success_rate': success_rate,
            'total_pnl': stats['total_pnl'],
            'best_trade': stats['best_trade'],
            'worst_trade': stats['worst_trade'],
            'tokens_scanned': stats['tokens_scanned'],
            'candidates_found': stats['candidates_found']
        }
    
    def print_session_summary(self):
        """Print session summary to console"""
        summary = self.get_session_summary()
        
        print("\n" + "="*50)
        print("📊 SESSION SUMMARY")
        print("="*50)
        print(f"Trades Attempted: {summary['trades_attempted']}")
        print(f"Trades Successful: {summary['trades_successful']}")
        print(f"Success Rate: {summary['success_rate']:.1f}%")
        print(f"Total P&L: {summary['total_pnl']:+.2f}%")
        print(f"Best Trade: {summary['best_trade']:+.2f}%")
        print(f"Worst Trade: {summary['worst_trade']:+.2f}%")
        print(f"Tokens Scanned: {summary['tokens_scanned']}")
        print(f"Candidates Found: {summary['candidates_found']}")
        print("="*50)

# Global logger instance
sniper_logger = SniperLogger()

# Legacy functions for backward compatibility
def log_trade(symbol, action, amount, price):
    token_data = {'symbol': symbol, 'price': price}
    trade_data = {'sol_amount': amount}
    sniper_logger.log_trade(action, token_data, trade_data)

def log_error(msg):
    sniper_logger.log_error(msg)

def log_info(msg):
    sniper_logger.log_info(msg)
