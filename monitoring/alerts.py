import os
import requests
import json
from datetime import datetime
from pathlib import Path

class AlertSystem:
    def __init__(self):
        self.alerts_enabled = True
        self.alert_thresholds = {
            'profit_target': 50.0,  # Alert when profit > 50%
            'loss_threshold': -20.0,  # Alert when loss > 20%
            'high_volume': 100000,  # Alert when volume > $100k
            'liquidity_drop': 5000,  # Alert when liquidity < $5k
            'position_count': 3  # Alert when positions > 3
        }
        
        # Alert channels (can be extended)
        self.console_alerts = True
        self.file_alerts = True
        self.webhook_url = None  # Discord/Slack webhook
        
        # Alert history
        self.alerts_dir = Path("logs/alerts")
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        self.alert_history_file = self.alerts_dir / "alert_history.json"
        
    def set_webhook(self, webhook_url):
        """Set Discord/Slack webhook URL for notifications"""
        self.webhook_url = webhook_url
    
    def _send_console_alert(self, alert_type, message, priority="INFO"):
        """Send alert to console"""
        if not self.console_alerts:
            return
            
        icons = {
            "CRITICAL": "🚨",
            "WARNING": "⚠️",
            "INFO": "ℹ️",
            "SUCCESS": "✅",
            "PROFIT": "💰",
            "LOSS": "📉"
        }
        
        icon = icons.get(priority, "ℹ️")
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{icon} [{timestamp}] {alert_type.upper()}: {message}")
    
    def _send_file_alert(self, alert_type, message, priority="INFO"):
        """Log alert to file"""
        if not self.file_alerts:
            return
            
        try:
            alert_data = {
                'timestamp': datetime.now().isoformat(),
                'type': alert_type,
                'priority': priority,
                'message': message
            }
            
            # Load existing alerts
            alerts = []
            if self.alert_history_file.exists():
                with open(self.alert_history_file, 'r') as f:
                    alerts = json.load(f)
            
            # Add new alert
            alerts.append(alert_data)
            
            # Keep only last 1000 alerts
            if len(alerts) > 1000:
                alerts = alerts[-1000:]
            
            # Save alerts
            with open(self.alert_history_file, 'w') as f:
                json.dump(alerts, f, indent=2)
                
        except Exception as e:
            print(f"Failed to log alert to file: {e}")
    
    def _send_webhook_alert(self, alert_type, message, priority="INFO"):
        """Send alert via webhook (Discord/Slack)"""
        if not self.webhook_url:
            return
            
        try:
            # Format for Discord
            color_map = {
                "CRITICAL": 0xFF0000,  # Red
                "WARNING": 0xFFA500,   # Orange
                "INFO": 0x0099FF,      # Blue
                "SUCCESS": 0x00FF00,   # Green
                "PROFIT": 0x00FF00,    # Green
                "LOSS": 0xFF0000       # Red
            }
            
            embed = {
                "title": f"Solana Sniper Alert: {alert_type}",
                "description": message,
                "color": color_map.get(priority, 0x0099FF),
                "timestamp": datetime.now().isoformat(),
                "footer": {"text": "Solana Sniper Bot"}
            }
            
            payload = {"embeds": [embed]}
            
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code != 204:
                print(f"Webhook alert failed: {response.status_code}")
                
        except Exception as e:
            print(f"Failed to send webhook alert: {e}")
    
    def send_alert(self, alert_type, message, priority="INFO"):
        """Send alert through all configured channels"""
        if not self.alerts_enabled:
            return
            
        self._send_console_alert(alert_type, message, priority)
        self._send_file_alert(alert_type, message, priority)
        self._send_webhook_alert(alert_type, message, priority)
    
    def alert_trade_executed(self, action, token_symbol, amount, price):
        """Alert when trade is executed"""
        message = f"{action.upper()} {token_symbol} | Amount: {amount} SOL | Price: ${price:.8f}"
        priority = "SUCCESS" if action == "buy" else "INFO"
        self.send_alert("TRADE_EXECUTED", message, priority)
    
    def alert_profit_target_hit(self, token_symbol, pnl_percent, exit_price):
        """Alert when profit target is reached"""
        if pnl_percent >= self.alert_thresholds['profit_target']:
            message = f"{token_symbol} hit profit target! P&L: +{pnl_percent:.2f}% | Exit: ${exit_price:.8f}"
            self.send_alert("PROFIT_TARGET", message, "PROFIT")
    
    def alert_stop_loss_hit(self, token_symbol, pnl_percent, exit_price):
        """Alert when stop loss is triggered"""
        if pnl_percent <= self.alert_thresholds['loss_threshold']:
            message = f"{token_symbol} hit stop loss! P&L: {pnl_percent:.2f}% | Exit: ${exit_price:.8f}"
            self.send_alert("STOP_LOSS", message, "LOSS")
    
    def alert_high_volume_detected(self, token_symbol, volume_24h, price_change):
        """Alert when high volume token is detected"""
        if volume_24h >= self.alert_thresholds['high_volume']:
            message = f"High volume detected: {token_symbol} | Volume: ${volume_24h:,.0f} | Change: {price_change:+.2f}%"
            self.send_alert("HIGH_VOLUME", message, "WARNING")
    
    def alert_liquidity_drop(self, token_symbol, current_liquidity, previous_liquidity):
        """Alert when liquidity drops significantly"""
        if current_liquidity < self.alert_thresholds['liquidity_drop']:
            drop_percent = ((previous_liquidity - current_liquidity) / previous_liquidity * 100) if previous_liquidity > 0 else 0
            message = f"Liquidity drop: {token_symbol} | Current: ${current_liquidity:,.0f} | Drop: {drop_percent:.1f}%"
            self.send_alert("LIQUIDITY_DROP", message, "CRITICAL")
    
    def alert_new_opportunity(self, token_symbol, signal_strength, market_cap, volume):
        """Alert when new sniping opportunity is found"""
        message = f"New opportunity: {token_symbol} | Signal: {signal_strength}/100 | MC: ${market_cap:,.0f} | Vol: ${volume:,.0f}"
        self.send_alert("NEW_OPPORTUNITY", message, "INFO")
    
    def alert_position_limit_reached(self, current_positions, limit):
        """Alert when position limit is reached"""
        if current_positions >= self.alert_thresholds['position_count']:
            message = f"Position limit reached: {current_positions}/{limit} active positions"
            self.send_alert("POSITION_LIMIT", message, "WARNING")
    
    def alert_wallet_balance_low(self, sol_balance, threshold=0.1):
        """Alert when wallet balance is low"""
        if sol_balance < threshold:
            message = f"Low wallet balance: {sol_balance:.4f} SOL remaining"
            self.send_alert("LOW_BALANCE", message, "WARNING")
    
    def alert_api_error(self, api_name, error_message):
        """Alert when API errors occur"""
        message = f"{api_name} API error: {error_message}"
        self.send_alert("API_ERROR", message, "CRITICAL")
    
    def alert_session_summary(self, summary):
        """Alert with session performance summary"""
        message = (
            f"Session complete | "
            f"Trades: {summary['trades_successful']}/{summary['trades_attempted']} | "
            f"Success: {summary['success_rate']:.1f}% | "
            f"P&L: {summary['total_pnl']:+.2f}% | "
            f"Best: {summary['best_trade']:+.2f}%"
        )
        priority = "SUCCESS" if summary['total_pnl'] > 0 else "INFO"
        self.send_alert("SESSION_SUMMARY", message, priority)
    
    def alert_rug_pull_detected(self, token_symbol, indicators):
        """Alert when potential rug pull is detected"""
        message = f"Potential rug pull: {token_symbol} | Indicators: {', '.join(indicators)}"
        self.send_alert("RUG_PULL_DETECTED", message, "CRITICAL")
    
    def alert_system_startup(self, wallet_address, sol_balance):
        """Alert when system starts up"""
        message = f"Sniper bot started | Wallet: {wallet_address[:8]}... | Balance: {sol_balance:.4f} SOL"
        self.send_alert("SYSTEM_STARTUP", message, "SUCCESS")
    
    def alert_system_shutdown(self, reason="manual"):
        """Alert when system shuts down"""
        message = f"Sniper bot stopped | Reason: {reason}"
        self.send_alert("SYSTEM_SHUTDOWN", message, "INFO")
    
    def get_recent_alerts(self, count=10):
        """Get recent alerts from history"""
        try:
            if not self.alert_history_file.exists():
                return []
                
            with open(self.alert_history_file, 'r') as f:
                alerts = json.load(f)
                
            return alerts[-count:] if alerts else []
            
        except Exception as e:
            print(f"Failed to load alert history: {e}")
            return []
    
    def configure_thresholds(self, **kwargs):
        """Configure alert thresholds"""
        for key, value in kwargs.items():
            if key in self.alert_thresholds:
                self.alert_thresholds[key] = value
                print(f"Alert threshold updated: {key} = {value}")
    
    def enable_alerts(self, enabled=True):
        """Enable or disable all alerts"""
        self.alerts_enabled = enabled
        status = "enabled" if enabled else "disabled"
        print(f"Alerts {status}")
    
    def test_alerts(self):
        """Test all alert channels"""
        test_message = "Alert system test - all channels working"
        self.send_alert("SYSTEM_TEST", test_message, "INFO")

# Global alert system instance
alert_system = AlertSystem()

# Convenience functions
def alert_trade(action, token_symbol, amount, price):
    alert_system.alert_trade_executed(action, token_symbol, amount, price)

def alert_profit(token_symbol, pnl_percent, exit_price):
    alert_system.alert_profit_target_hit(token_symbol, pnl_percent, exit_price)

def alert_loss(token_symbol, pnl_percent, exit_price):
    alert_system.alert_stop_loss_hit(token_symbol, pnl_percent, exit_price)

def alert_opportunity(token_symbol, signal_strength, market_cap, volume):
    alert_system.alert_new_opportunity(token_symbol, signal_strength, market_cap, volume)

def alert_error(api_name, error_message):
    alert_system.alert_api_error(api_name, error_message)
