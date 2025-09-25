"""
Dynamic Exit Strategies - Advanced position exit management

This module implements sophisticated exit strategies for trading positions:

1. TrailingStopStrategy:
   - Adaptive stop-loss that tightens based on token characteristics
   - Liquidity-based threshold adjustments for different token types
   - Trailing activation at configurable profit levels
   - Dynamic trailing distance based on market conditions

2. ScaledExitStrategy:
   - Partial position exits at multiple profit levels
   - Configurable exit ratios (e.g., 25% at +25%, 50% at +50%)
   - Prevents over-holding profitable positions
   - Allows for profit-taking while maintaining upside exposure

3. ExitManager:
   - Coordinates multiple exit strategies
   - Returns the first triggered exit signal
   - Prioritizes safety exits over profit-taking

Key Features:
- Adaptive thresholds based on initial liquidity and volatility
- Position tracking with highest price memory
- Configurable profit targets and exit ratios
- Comprehensive exit reason tracking
"""

from datetime import datetime

# Import config constants
try:
    from config import TARGET_PROFIT, STOP_LOSS
except ImportError:
    # Fallback values if config not available
    TARGET_PROFIT = 0.25  # 25% default target
    STOP_LOSS = 0.15     # 15% default stop loss

class ExitStrategy:
    """Base class for exit strategies"""
    
    def __init__(self, name):
        self.name = name
    
    def should_exit(self, position, current_price):
        """Returns (should_exit: bool, reason: str, partial_exit_ratio: float)"""
        return False, "hold", 0.0

class TrailingStopStrategy(ExitStrategy):
    """Trailing stop-loss that tightens as position becomes profitable with adaptive thresholds"""
    
    def __init__(self, initial_stop_loss=0.1, trailing_distance=0.05, activation_profit=0.1):
        super().__init__("trailing_stop")
        self.initial_stop_loss = initial_stop_loss  # -10% initial stop
        self.trailing_distance = trailing_distance   # 5% trailing distance
        self.activation_profit = activation_profit   # Activate trailing at +10%
    
    def should_exit(self, position, current_price):
        entry_price = position.get('entry_price', 0)
        if not entry_price or current_price <= 0:
            return False, "hold", 0.0
        
        pnl_percent = (current_price - entry_price) / entry_price
        
        # Adaptive thresholds based on token volatility and liquidity
        initial_liquidity = position.get('initial_liquidity', 50000)
        liquidity_factor = min(1.0, initial_liquidity / 50000)  # Scale factor based on liquidity
        
        # Adjust thresholds based on token characteristics
        adaptive_stop_loss = self.initial_stop_loss * (0.5 + 0.5 * liquidity_factor)  # Tighter for low liquidity
        adaptive_trailing = self.trailing_distance * (0.7 + 0.3 * liquidity_factor)   # Tighter trailing for low liquidity
        adaptive_activation = self.activation_profit * (0.8 + 0.2 * liquidity_factor) # Lower activation for low liquidity
        
        # Update highest price seen
        if 'highest_price' not in position:
            position['highest_price'] = max(entry_price, current_price)
        else:
            position['highest_price'] = max(position['highest_price'], current_price)
        
        # Adaptive initial stop-loss
        if pnl_percent <= -adaptive_stop_loss:
            return True, "adaptive_stop_loss", 1.0
        
        # Activate trailing stop with adaptive thresholds
        if pnl_percent >= adaptive_activation:
            highest_price = position['highest_price']
            trailing_stop_price = highest_price * (1 - adaptive_trailing)
            
            if current_price <= trailing_stop_price:
                return True, "adaptive_trailing_stop", 1.0
        
        return False, "hold", 0.0

class ScaledExitStrategy(ExitStrategy):
    """Scale out partially at different profit levels"""
    
    def __init__(self, exit_levels=None):
        super().__init__("scaled_exit")
        # Default scaling: 25% at +20%, 50% at +50%, 100% at +100%
        self.exit_levels = exit_levels or [
            (0.2, 0.25),   # 25% at +20%
            (0.5, 0.5),    # 50% at +50%
            (1.0, 1.0),    # 100% at +100%
        ]
        
    def should_exit(self, position, current_price):
        entry_price = position.get('entry_price', 0)
        if not entry_price or current_price <= 0:
            return False, "hold", 0.0
        
        pnl_percent = (current_price - entry_price) / entry_price
        
        # Track what's already been sold
        if 'total_sold_ratio' not in position:
            position['total_sold_ratio'] = 0.0
        
        # Check each exit level
        for profit_threshold, exit_ratio in self.exit_levels:
            if pnl_percent >= profit_threshold:
                remaining_ratio = exit_ratio - position['total_sold_ratio']
                if remaining_ratio > 0.01:  # At least 1% to make it worthwhile
                    return True, f"scaled_exit_{profit_threshold*100:.0f}%", remaining_ratio
        
        # Stop-loss
        if pnl_percent <= -STOP_LOSS:
            return True, "stop_loss", 1.0 - position['total_sold_ratio']
        
        return False, "hold", 0.0

class TimeBasedExitStrategy(ExitStrategy):
    """Time-based exits with dynamic stop-loss tightening"""
    
    def __init__(self, max_hold_minutes=30, profit_tightening=True):
        super().__init__("time_based")
        self.max_hold_minutes = max_hold_minutes
        self.profit_tightening = profit_tightening
    
    def should_exit(self, position, current_price):
        entry_price = position.get('entry_price', 0)
        entry_time = position.get('entry_time')
        
        if not entry_price or not entry_time or current_price <= 0:
            return False, "hold", 0.0
        
        pnl_percent = (current_price - entry_price) / entry_price
        time_held_minutes = (datetime.now() - entry_time).total_seconds() / 60
        
        # Take profit
        if pnl_percent >= TARGET_PROFIT:
            return True, "take_profit", 1.0
        
        # Time-based tightening of stop-loss
        if self.profit_tightening and time_held_minutes > 5:
            # Tighten stop-loss over time if profitable
            if pnl_percent > 0:
                # After 5 minutes, tighten stop to -5% if profitable
                if time_held_minutes > 5 and pnl_percent <= -0.05:
                    return True, "tightened_stop", 1.0
                # After 15 minutes, tighten to -2% if profitable
                elif time_held_minutes > 15 and pnl_percent <= -0.02:
                    return True, "very_tight_stop", 1.0
        
        # Regular stop-loss
        if pnl_percent <= -STOP_LOSS:
            return True, "stop_loss", 1.0
        
        # Max hold time
        if time_held_minutes > self.max_hold_minutes:
            return True, "time_limit", 1.0
        
        return False, "hold", 0.0

class AdaptiveExitStrategy(ExitStrategy):
    """Combines multiple strategies for adaptive exits"""
    
    def __init__(self):
        super().__init__("adaptive")
        self.trailing_stop = TrailingStopStrategy(
            initial_stop_loss=0.1,
            trailing_distance=0.03,  # Tighter 3% trailing
            activation_profit=0.15   # Activate at +15%
        )
        self.time_based = TimeBasedExitStrategy(max_hold_minutes=25)
        
    def should_exit(self, position, current_price):
        # Check trailing stop first
        should_exit, reason, ratio = self.trailing_stop.should_exit(position, current_price)
        if should_exit:
            return should_exit, reason, ratio
        
        # Then check time-based
        return self.time_based.should_exit(position, current_price)

class ExitStrategyManager:
    """Manages different exit strategies per position"""
    
    def __init__(self):
        self.strategies = {
            'trailing': TrailingStopStrategy(),
            'scaled': ScaledExitStrategy(),
            'time_based': TimeBasedExitStrategy(),
            'adaptive': AdaptiveExitStrategy()
        }
        self.default_strategy = 'adaptive'
    
    def get_exit_decision(self, position, current_price, strategy_name=None):
        """Get exit decision for a position"""
        strategy_name = strategy_name or position.get('exit_strategy', self.default_strategy)
        strategy = self.strategies.get(strategy_name, self.strategies[self.default_strategy])
        
        return strategy.should_exit(position, current_price)
    
    def set_position_strategy(self, position, strategy_name):
        """Set exit strategy for a position"""
        if strategy_name in self.strategies:
            position['exit_strategy'] = strategy_name
            return True
        return False

# Global exit strategy manager
exit_manager = ExitStrategyManager()
