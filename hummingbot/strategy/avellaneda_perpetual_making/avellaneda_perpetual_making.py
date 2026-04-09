"""
Avellaneda Perpetual Market Making Strategy

This strategy implements the Avellaneda-Stoikov market making model for perpetual futures trading.
It combines the theoretical framework of optimal bid/ask spreads with position management
specifically designed for leveraged perpetual contracts.

Key Features:
- Optimal bid/ask spreads based on volatility, liquidity, and risk tolerance
- Dynamic reservation price calculation considering inventory deviation
- Position-aware pricing with leverage considerations
- Adaptive gamma learning for risk parameter optimization
- Integrated profit-taking and stop-loss mechanisms
"""

import logging
from decimal import Decimal
from math import ceil, floor
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from hummingbot.connector.derivative.position import Position
from hummingbot.connector.derivative_base import DerivativeBase
from hummingbot.core.clock import Clock
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    PositionModeChangeEvent,
    SellOrderCompletedEvent,
)
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.utils import map_df_to_str
from hummingbot.strategy.asset_price_delegate import AssetPriceDelegate
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from hummingbot.strategy.order_book_asset_price_delegate import OrderBookAssetPriceDelegate
from hummingbot.strategy.strategy_py_base import StrategyPyBase
from hummingbot.strategy.utils import order_age
from hummingbot.strategy.__utils__.trailing_indicators.instant_volatility import InstantVolatilityIndicator
from hummingbot.strategy.__utils__.trailing_indicators.trading_intensity import TradingIntensityIndicator
from hummingbot.strategy.order_tracker import OrderTracker

# Import Avellaneda adaptive gamma components
try:
    from hummingbot.strategy.avellaneda_perpetual_making.adaptive_gamma_learner import (
        OnlineGammaLearner, 
        SimpleGammaScheduler
    )
    ADAPTIVE_GAMMA_AVAILABLE = True
except ImportError:
    ADAPTIVE_GAMMA_AVAILABLE = False
    OnlineGammaLearner = None
    SimpleGammaScheduler = None

NaN = float("nan")
s_decimal_zero = Decimal(0)
s_decimal_neg_one = Decimal(-1)
s_decimal_one = Decimal(1)


# Data types for Avellaneda strategy
from hummingbot.strategy.data_types import PriceSize, Proposal


class AvellanedaPerpetualMakingStrategy(StrategyPyBase):
    """
    Avellaneda-Stoikov Market Making Strategy for Perpetual Futures
    
    This strategy implements the mathematical framework from the Avellaneda-Stoikov paper
    "High-frequency trading in a limit order book" adapted for perpetual futures trading.
    
    The strategy calculates optimal bid and ask spreads based on:
    1. Market volatility (σ)
    2. Order book liquidity parameters (α, κ)  
    3. Risk aversion parameter (γ)
    4. Inventory deviation from target
    5. Time horizon considerations
    """
    
    OPTION_LOG_CREATE_ORDER = 1 << 3
    OPTION_LOG_MAKER_ORDER_FILLED = 1 << 4
    OPTION_LOG_STATUS_REPORT = 1 << 5
    OPTION_LOG_ALL = 0x7fffffffffffffff
    _logger = None

    @classmethod
    def logger(cls):
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self):
        super().__init__()
        self._market_info = None
        self._all_markets_ready = False
        self._sb_order_tracker = OrderTracker()
        
        # Avellaneda model parameters
        self._risk_factor = Decimal("1.0")  # γ (gamma) - risk aversion
        self._order_amount_shape_factor = Decimal("1.0")  # η (eta) - order shape factor
        self._min_spread = Decimal("0.001")  # minimum spread percentage (0.1%)
        self._volatility_buffer_size = 200  # number of ticks for volatility calculation
        self._trading_intensity_buffer_size = 200  # number of ticks for liquidity calculation
        
        # Trading parameters
        self._order_amount = Decimal("1.0")
        self._force_min_spread = False
        self._maker_fee_pct = Decimal("0.02")
        self._assumed_exit_fee_pct = Decimal("0.02")
        self._fee_floor_buffer_pct = Decimal("0.0")
        self._enforce_fee_floor = False
        self._directional_skew_enabled = False
        self._max_directional_bias = Decimal("0.3")
        self._momentum_window_short = 5
        self._momentum_window_long = 20
        self._order_flow_window = 20
        self._funding_rate_bias_enabled = False
        self._funding_rate_weight = Decimal("0.1")
        self._momentum_weight = Decimal("0.45")
        self._order_flow_weight = Decimal("0.45")
        self._inventory_target_base_pct = Decimal("50")  # 50% target allocation
        self._order_refresh_time = 30.0
        self._order_refresh_tolerance_pct = Decimal("1.0")
        self._filled_order_delay = 15.0  # Default value, will be overridden in init_params
        
        # Position management for perpetual futures
        self._leverage = 10
        self._position_mode = PositionMode.ONEWAY
        self._long_profit_taking_spread = Decimal("0.03")  # 3%
        self._short_profit_taking_spread = Decimal("0.03")  # 3%
        self._stop_loss_spread = Decimal("0.10")  # 10%
        self._time_between_stop_loss_orders = 60.0
        self._stop_loss_slippage_buffer = Decimal("0.005")  # 0.5%
        self._stop_loss_use_maker_orders = False
        self._stop_loss_maker_timeout = 60.0
        self._stop_loss_auto_fallback = True
        self._stop_loss_working_type = "CONTRACT_PRICE"
        self._exchange_preplaced_stop_loss = False
        
        # Avellaneda model state
        self._avg_vol: Optional[InstantVolatilityIndicator] = None
        self._trading_intensity: Optional[TradingIntensityIndicator] = None
        self._alpha = None  # order book intensity factor
        self._kappa = None  # order book depth factor
        self._reservation_price = s_decimal_zero
        self._optimal_spread = s_decimal_zero
        self._optimal_ask = s_decimal_zero
        self._optimal_bid = s_decimal_zero
        
        # Adaptive gamma learning
        self._gamma_learner = None
        self._use_adaptive_gamma = False
        self._last_pnl = Decimal("0")
        self._total_pnl = Decimal("0")
        
        # Tracking and status
        self._last_timestamp = 0
        self._status_report_interval = 900
        self._logging_options = self.OPTION_LOG_ALL
        self._cancel_timestamp = 0
        self._create_timestamp = 0
        self._ticks_to_be_ready = 0
        
        # Position tracking for exit orders
        self._exit_orders = {}
        self._exit_order_roles: Dict[str, Tuple[str, Optional[PositionSide]]] = {}
        self._take_profit_order_ids = {
            PositionSide.LONG: set(),
            PositionSide.SHORT: set(),
        }
        self._stop_loss_order_ids = {
            PositionSide.LONG: set(),
            PositionSide.SHORT: set(),
        }
        self._stop_loss_order_details: Dict[str, Dict[str, Any]] = {}
        self._position_mode_ready = False
        self._position_mode_not_ready_counter = 0
        self._last_own_trade_price = Decimal("0")
        self._hedge_inventory_warning_active = False
        
        # Error handling state
        self._last_error_timestamp = 0.0
        self._consecutive_error_count = 0
        self._error_cooldown_seconds = 30.0
        self._max_consecutive_errors = 3
        
        # Error handling state
        self._last_error_timestamp = 0.0
        self._consecutive_error_count = 0
        self._error_cooldown_seconds = 30.0
        self._max_consecutive_errors = 3
        

    def init_params(self,
                    market_info: MarketTradingPairTuple,
                    risk_factor: Decimal = Decimal("1.0"),
                    order_amount_shape_factor: Decimal = Decimal("1.0"),
                    min_spread: Decimal = Decimal("0.01"),
                    order_amount: Decimal = Decimal("1.0"),
                    force_min_spread: bool = False,
                    maker_fee_pct: Decimal = Decimal("0.02"),
                    assumed_exit_fee_pct: Decimal = Decimal("0.02"),
                    fee_floor_buffer_pct: Decimal = Decimal("0.0"),
                    enforce_fee_floor: bool = False,
                    directional_skew_enabled: bool = False,
                    max_directional_bias: Decimal = Decimal("0.3"),
                    momentum_window_short: int = 5,
                    momentum_window_long: int = 20,
                    order_flow_window: int = 20,
                    funding_rate_bias_enabled: bool = False,
                    funding_rate_weight: Decimal = Decimal("0.1"),
                    momentum_weight: Decimal = Decimal("0.45"),
                    order_flow_weight: Decimal = Decimal("0.45"),
                    inventory_target_base_pct: Decimal = Decimal("50"),
                    volatility_buffer_size: int = 200,
                    trading_intensity_buffer_size: int = 200,
                    order_refresh_time: float = 30.0,
                    order_refresh_tolerance_pct: Decimal = Decimal("1.0"),
                    filled_order_delay: float = 15.0,
                    leverage: int = 10,
                    position_mode: str = "One-way",
                    long_profit_taking_spread: Decimal = Decimal("0.03"),
                    short_profit_taking_spread: Decimal = Decimal("0.03"),
                    stop_loss_spread: Decimal = Decimal("0.10"),
                    time_between_stop_loss_orders: float = 60.0,
                    stop_loss_slippage_buffer: Decimal = Decimal("0.005"),
                    stop_loss_use_maker_orders: bool = False,
                    stop_loss_maker_timeout: float = 60.0,
                    stop_loss_auto_fallback: bool = True,
                    stop_loss_working_type: str = "CONTRACT_PRICE",
                    exchange_preplaced_stop_loss: bool = False,
                    adaptive_gamma_enabled: bool = False,
                    adaptive_gamma_initial: Decimal = Decimal("1.0"),
                    adaptive_gamma_learning_rate: Decimal = Decimal("0.01"),
                    adaptive_gamma_min: Decimal = Decimal("0.1"),
                    adaptive_gamma_max: Decimal = Decimal("10.0"),
                    adaptive_gamma_reward_window: int = 100,
                    adaptive_gamma_update_frequency: int = 10,
                    logging_options: int = None,
                    status_report_interval: float = 900,
                    asset_price_delegate: AssetPriceDelegate = None,
                    hb_app_notification: bool = False):
        """
        Initialize the Avellaneda Perpetual Market Making Strategy
        
        Parameters:
        - market_info: Market and trading pair information
        - risk_factor: γ (gamma) - risk aversion parameter
        - order_amount_shape_factor: η (eta) - controls order size distribution
        - min_spread: Minimum spread to maintain (percentage)
        - order_amount: Base order amount
        - inventory_target_base_pct: Target base asset percentage (0-100)
        - volatility_buffer_size: Number of price ticks for volatility calculation
        - trading_intensity_buffer_size: Number of ticks for liquidity calculation
        - leverage: Leverage for perpetual futures
        - position_mode: "One-way" or "Hedge" mode
        - adaptive_gamma_enabled: Enable adaptive risk parameter learning
        """
        
        self._market_info = market_info
        self._risk_factor = risk_factor
        self._order_amount_shape_factor = order_amount_shape_factor
        self._min_spread = min_spread
        self._force_min_spread = force_min_spread  # 新增：強制使用最小spread
        self._maker_fee_pct = maker_fee_pct
        self._assumed_exit_fee_pct = assumed_exit_fee_pct
        self._fee_floor_buffer_pct = fee_floor_buffer_pct
        self._enforce_fee_floor = enforce_fee_floor
        self._directional_skew_enabled = directional_skew_enabled
        self._max_directional_bias = max_directional_bias
        self._momentum_window_short = momentum_window_short
        self._momentum_window_long = momentum_window_long
        self._order_flow_window = order_flow_window
        self._funding_rate_bias_enabled = funding_rate_bias_enabled
        self._funding_rate_weight = funding_rate_weight
        self._momentum_weight = momentum_weight
        self._order_flow_weight = order_flow_weight
        self._order_amount = order_amount
        self._inventory_target_base_pct = inventory_target_base_pct
        self._volatility_buffer_size = volatility_buffer_size
        self._trading_intensity_buffer_size = trading_intensity_buffer_size
        self._order_refresh_time = order_refresh_time
        self._order_refresh_tolerance_pct = order_refresh_tolerance_pct
        self._filled_order_delay = filled_order_delay
        
        # Perpetual futures specific
        self._leverage = leverage
        self._position_mode = PositionMode.HEDGE if position_mode == "Hedge" else PositionMode.ONEWAY
        self._long_profit_taking_spread = long_profit_taking_spread
        self._short_profit_taking_spread = short_profit_taking_spread
        self._stop_loss_spread = stop_loss_spread
        self._time_between_stop_loss_orders = time_between_stop_loss_orders
        self._stop_loss_slippage_buffer = stop_loss_slippage_buffer
        self._stop_loss_use_maker_orders = stop_loss_use_maker_orders
        self._stop_loss_maker_timeout = stop_loss_maker_timeout
        self._stop_loss_auto_fallback = stop_loss_auto_fallback
        self._stop_loss_working_type = stop_loss_working_type
        self._exchange_preplaced_stop_loss = exchange_preplaced_stop_loss
        
        # System settings
        self._logging_options = logging_options or self.OPTION_LOG_ALL
        self._status_report_interval = status_report_interval
        self._asset_price_delegate = asset_price_delegate
        self._hb_app_notification = hb_app_notification
        
        # Initialize indicators
        self._avg_vol = InstantVolatilityIndicator(sampling_length=volatility_buffer_size)
        self._ticks_to_be_ready = max(volatility_buffer_size, trading_intensity_buffer_size)
        
        # Ensure minimum buffer sizes for stability
        if volatility_buffer_size < 50:
            self.logger().warning(f"⚠️  volatility_buffer_size ({volatility_buffer_size}) is too small for stable calculations. Recommended: ≥50")
        if trading_intensity_buffer_size < 50:
            self.logger().warning(f"⚠️  trading_intensity_buffer_size ({trading_intensity_buffer_size}) is too small for stable calculations. Recommended: ≥50")
        
        # Initialize adaptive gamma if requested and available
        if adaptive_gamma_enabled and ADAPTIVE_GAMMA_AVAILABLE:
            self._initialize_adaptive_gamma(
                adaptive_gamma_initial,
                adaptive_gamma_learning_rate,
                adaptive_gamma_min,
                adaptive_gamma_max,
                adaptive_gamma_reward_window,
                adaptive_gamma_update_frequency
            )
        
        self.add_markets([market_info.market])
        
        # Version banner for identification
        self.logger().info("=" * 70)
        self.logger().info("🚀 AVELLANEDA PERPETUAL MAKING STRATEGY - ENHANCED VERSION")
        self.logger().info("   🔧 ORDER MANAGEMENT FIX v2.0 - WITH CONFIRMATION MECHANISM")
        self.logger().info("   ✅ Multi-order accumulation prevention ACTIVE")
        self.logger().info("   ✅ Real-time order confirmation system ENABLED")
        self.logger().info("   ✅ State-driven cancel-then-create logic IMPLEMENTED")
        self.logger().info("=" * 70)
        
        self.logger().info("✅ Avellaneda Perpetual Making Strategy initialized")
        self.logger().info(f"   📊 Risk Factor (γ): {self._risk_factor}")
        self.logger().info(f"   🎯 Target Inventory: {self._inventory_target_base_pct}%")
        self.logger().info(f"   📈 Leverage: {self._leverage}x")
        self.logger().info(f"   🔄 Position Mode: {position_mode}")
        self.logger().info(f"   ⏰ Order Refresh Time: {self._order_refresh_time}s")
        self.logger().info(f"   🛡️ Order Management: Enhanced with confirmation mechanism")
        self.logger().info(f"   📏 Min Spread: {self._min_spread*100:.4f}% {'(FORCED MODE - Volume Farming)' if self._force_min_spread else '(Normal)'}")
        if self._use_adaptive_gamma:
            self.logger().info(f"   🧠 Adaptive Gamma: Enabled")

    def _initialize_adaptive_gamma(self,
                                   initial_gamma: Decimal,
                                   learning_rate: Decimal,
                                   gamma_min: Decimal,
                                   gamma_max: Decimal,
                                   reward_window: int,
                                   update_frequency: int):
        """Initialize adaptive gamma learning"""
        try:
            self._gamma_learner = OnlineGammaLearner(
                initial_gamma=float(initial_gamma),
                learning_rate=float(learning_rate),
                gamma_min=float(gamma_min),
                gamma_max=float(gamma_max),
                reward_window=reward_window,
                update_frequency=update_frequency
            )
            self._use_adaptive_gamma = True
            self.logger().info(f"🧠 Adaptive gamma learning enabled - "
                             f"initial: {initial_gamma}, "
                             f"lr: {learning_rate}, "
                             f"range: [{gamma_min}, {gamma_max}]")
        except Exception as e:
            self.logger().error(f"❌ Error initializing adaptive gamma: {e}")
            self._use_adaptive_gamma = False

    @property
    def gamma(self) -> Decimal:
        """Get current risk factor (γ)"""
        if self._use_adaptive_gamma and self._gamma_learner is not None:
            return self._gamma_learner.get_current_gamma()
        return self._risk_factor

    @property
    def inventory_target_base(self) -> Decimal:
        """Target base asset ratio (0-1)"""
        return self._inventory_target_base_pct / Decimal('100')

    @property
    def active_orders(self) -> List[LimitOrder]:
        """Get active limit orders"""
        if self._market_info not in self._sb_order_tracker.market_pair_to_active_orders:
            return []
        return self._sb_order_tracker.market_pair_to_active_orders[self._market_info]

    @property
    def active_positions(self) -> Dict[str, Position]:
        """Get active positions for perpetual trading"""
        return self._market_info.market.account_positions

    def get_price(self) -> Decimal:
        """Get current reference price"""
        if self._asset_price_delegate is not None:
            price = self._asset_price_delegate.get_mid_price()
        else:
            price = self._market_info.get_mid_price()
        return price

    def calculate_inventory_deviation(self) -> Decimal:
        """
        Calculate inventory deviation from target
        
        NOTE: This implementation uses VALUE-BASED inventory calculation for perpetual futures,
        which differs from the traditional Avellaneda model that uses base asset quantities.
        
        Rationale for VALUE-BASED approach:
        - Perpetual futures use leverage, making absolute position sizes less meaningful
        - Total portfolio value better represents actual risk exposure
        - Accounts for margin requirements and leverage effects
        - More appropriate for leveraged derivative trading
        
        Formula: inventory_ratio = (quote_balance + position_value) / total_portfolio_value
        
        Returns:
            Decimal: Current inventory deviation from target (absolute difference)
        """
        try:
            market = self._market_info.market
            base_asset = self._market_info.base_asset
            quote_asset = self._market_info.quote_asset
            current_price = self.get_price()
            
            # For perpetual futures, we need to consider positions instead of balances
            quote_balance = market.get_balance(quote_asset)
            
            # Get position value in quote currency
            positions = self._positions_for_trading_pair()
            has_dual_hedge_legs = self._is_hedge_mode() and self._has_both_hedge_legs(positions)
            self._log_dual_hedge_inventory_warning(has_dual_hedge_legs)
            if has_dual_hedge_legs:
                return s_decimal_zero
            
            position_value = s_decimal_zero
            for position in positions:
                position_value += position.amount * current_price
            
            total_value = quote_balance + abs(position_value)
            
            if total_value > 0:
                base_ratio = (quote_balance + position_value) / total_value
                return abs(base_ratio - self.inventory_target_base)
            else:
                return s_decimal_zero
                
        except Exception as e:
            self.logger().error(f"❌ Error calculating inventory deviation: {e}")
            return s_decimal_zero

    def calculate_reservation_price_and_optimal_spread(self):
        """
        Calculate reservation price and optimal spread using Avellaneda-Stoikov model
        
        Mathematical formulation:
        - r = S - q*γ*σ*√T  (reservation price)
        - δ = γ*σ*√T + (2/γ)*ln(1 + γ/κ)  (optimal spread)
        
        Where:
        - S: current mid price
        - q: inventory deviation from target
        - γ: risk aversion parameter  
        - σ: volatility
        - T: time horizon
        - κ: order book depth parameter
        """
        try:
            current_price = self.get_price()
            
            # Calculate inventory deviation (q)
            inventory_deviation = self.calculate_inventory_deviation()
            
            # Use current inventory level relative to target
            positions = self._positions_for_trading_pair()
            has_dual_hedge_legs = self._is_hedge_mode() and self._has_both_hedge_legs(positions)
            
            q = s_decimal_zero
            if positions and not has_dual_hedge_legs:
                # Normalize position size by typical order size
                total_position = sum(p.amount for p in positions)
                q = total_position / (self._order_amount * 10)  # Scale by typical position size
            
            # Get volatility (σ)
            volatility = self.get_volatility()
            if volatility <= 0:
                return  # Cannot calculate without volatility
            
            # Time horizon - for perpetual futures, use order refresh time normalized to annual basis
            # CRITICAL FIX: Use same time calculation as avellaneda_market_making
            # For infinite timespan, use fixed time_left_fraction = 1 (from line 929 in market making)
            time_left_fraction = Decimal("1.0")
            
            # Risk factor (γ)
            gamma = self.gamma
            
            # Order book parameters (α, κ) 
            if self._alpha is None or self._kappa is None or self._kappa <= 0:
                # CRITICAL FIX: Use reasonable default values for kappa
                # kappa represents order book depth - small values cause huge spreads
                # Reasonable range: 50-200 for most markets
                alpha = Decimal("0.1")
                kappa = Decimal("100.0")  # Much larger default for reasonable spreads
                
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    self.logger().debug(f"📊 Using default liquidity parameters: α={alpha}, κ={kappa}")
            else:
                alpha = self._alpha
                kappa = max(self._kappa, Decimal("50.0"))  # Minimum kappa to prevent spread explosion
                
                if kappa != self._kappa:
                    self.logger().warning(f"⚠️ Adjusted kappa from {self._kappa} to {kappa} to prevent spread explosion")
            
            # Calculate reservation price: r = S - q*γ*σ*√T
            vol_term = gamma * volatility * time_left_fraction
            self._reservation_price = current_price - (q * vol_term)
            
            # CRITICAL FIX: Use the correct Avellaneda-Stoikov formula from the market making version
            # The original perpetual version had the wrong formula!
            
            # Correct formula from avellaneda_market_making.pyx lines 941-942:
            # optimal_spread = γ * σ * √T + (2 * ln(1 + γ/κ)) / γ
            
            self._optimal_spread = vol_term  # γ * σ * √T
            
            # Add liquidity term: (2 * ln(1 + γ/κ)) / γ
            if kappa > 0:
                try:
                    liquidity_term = 2 * (Decimal("1") + gamma / kappa).ln() / gamma
                    self._optimal_spread += liquidity_term
                    
                    if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                        self.logger().debug(f"📊 Spread components:")
                        self.logger().debug(f"   Vol term (γσ√T): {vol_term:.8f}")
                        self.logger().debug(f"   Liquidity term (2ln(1+γ/κ)/γ): {liquidity_term:.8f}")
                        self.logger().debug(f"   Total spread: {self._optimal_spread:.8f}")
                        
                except Exception as e:
                    self.logger().warning(f"⚠️ Error in liquidity term calculation: {e}")
                    # Use only volatility term if liquidity calculation fails
                    pass
            
            # CRITICAL FIX: Apply minimum spread constraint with correct unit interpretation
            # min_spread is already in decimal form (0.001 = 0.1%), no need to divide by 100
            min_spread_abs = current_price * self._min_spread
            
            if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                calculated_spread_pct = (self._optimal_spread / current_price) * 100
                min_spread_pct = self._min_spread * 100
                self.logger().debug(f"📏 Spread constraint check:")
                self.logger().debug(f"   Calculated spread: {self._optimal_spread:.8f} ({calculated_spread_pct:.4f}%)")
                self.logger().debug(f"   Minimum spread: {min_spread_abs:.8f} ({min_spread_pct:.4f}%)")
            
            # CRITICAL NEW: Check if force min spread is enabled for volume farming
            if self._force_min_spread:
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    avellaneda_spread_pct = (self._optimal_spread / current_price) * 100
                    self.logger().info(f"🚀 FORCE MIN SPREAD MODE - Volume farming activated")
                    self.logger().info(f"   Avellaneda calculated: {self._optimal_spread:.8f} ({avellaneda_spread_pct:.4f}%)")
                    self.logger().info(f"   Forcing to minimum: {min_spread_abs:.8f} ({min_spread_pct:.4f}%)")
                
                self._optimal_spread = min_spread_abs  # Always use minimum spread
            elif self._optimal_spread < min_spread_abs:
                self.logger().warning(f"⚠️ Calculated spread {self._optimal_spread:.8f} below minimum {min_spread_abs:.8f}, applying minimum")
                self._optimal_spread = min_spread_abs
            
            # Calculate optimal bid and ask
            half_spread = self._optimal_spread / Decimal("2")
            self._optimal_bid = self._reservation_price - half_spread
            self._optimal_ask = self._reservation_price + half_spread
            
            # Ensure positive prices
            if self._optimal_bid <= 0:
                self._optimal_bid = current_price * Decimal("0.999")
            if self._optimal_ask <= 0:
                self._optimal_ask = current_price * Decimal("1.001")
            
            if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                spread_pct = (self._optimal_spread / current_price) * 100
                self.logger().info(f"💰 Avellaneda Calculation:")
                self.logger().info(f"   Current Price: {current_price:.6f}")
                self.logger().info(f"   Inventory (q): {q:.6f}")
                self.logger().info(f"   Volatility (σ): {volatility:.6f}")
                self.logger().info(f"   Risk Factor (γ): {gamma:.6f} {'(adaptive)' if self._use_adaptive_gamma else '(fixed)'}")
                self.logger().info(f"   Reservation Price: {self._reservation_price:.6f}")
                self.logger().info(f"   Optimal Spread: {self._optimal_spread:.6f} ({spread_pct:.2f}%)")
                self.logger().info(f"   Optimal Bid: {self._optimal_bid:.6f} ({((self._optimal_bid/current_price-1)*100):+.2f}%)")
                self.logger().info(f"   Optimal Ask: {self._optimal_ask:.6f} ({((self._optimal_ask/current_price-1)*100):+.2f}%)")
                
        except Exception as e:
            self.logger().error(f"❌ Error calculating Avellaneda prices: {e}")
            # Fallback to simple mid-price based pricing
            current_price = self.get_price()
            self._reservation_price = current_price
            
            # CRITICAL FIX: Correct unit interpretation for fallback pricing too
            self._optimal_spread = current_price * self._min_spread
            half_spread_ratio = self._min_spread / Decimal("2")
            self._optimal_bid = current_price * (Decimal("1") - half_spread_ratio)
            self._optimal_ask = current_price * (Decimal("1") + half_spread_ratio)

    def get_volatility(self) -> Decimal:
        """Get current volatility estimate"""
        if self._avg_vol and self._avg_vol.is_sampling_buffer_full:
            return Decimal(str(self._avg_vol.current_value))
        return Decimal("0.01")  # Default 1% volatility

    def update_adaptive_gamma(self):
        """Update adaptive gamma based on performance"""
        if not self._use_adaptive_gamma or not self._gamma_learner:
            return
            
        try:
            # Calculate current PnL
            current_pnl = self._calculate_current_pnl()
            
            # Calculate inventory deviation  
            inventory_deviation = self.calculate_inventory_deviation()
            
            # Get market metrics
            volatility = float(self.get_volatility())
            spread = float(self._optimal_spread / self.get_price() if self._optimal_spread > 0 else Decimal("0.01"))
            
            # Update learner
            updated_gamma = self._gamma_learner.update(
                current_pnl=float(current_pnl),
                inventory_deviation=float(inventory_deviation),
                volatility=volatility,
                spread=spread
            )
            
            if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                self.logger().debug(f"🧠 Adaptive Gamma Update:")
                self.logger().debug(f"   New Gamma: {updated_gamma:.6f}")
                self.logger().debug(f"   PnL: {current_pnl:.6f}")
                self.logger().debug(f"   Inventory Deviation: {inventory_deviation:.6f}")
                
        except Exception as e:
            self.logger().error(f"❌ Error updating adaptive gamma: {e}")

    def _calculate_current_pnl(self) -> Decimal:
        """Calculate unrealized PnL from current positions"""
        try:
            total_pnl = s_decimal_zero
            current_price = self.get_price()
            
            for position in self.active_positions.values():
                if position.trading_pair == self._market_info.trading_pair:
                    # Calculate unrealized PnL
                    pnl = (current_price - position.entry_price) * position.amount
                    total_pnl += pnl
            
            return total_pnl
        except Exception as e:
            self.logger().error(f"❌ Error calculating PnL: {e}")
            return s_decimal_zero

    def create_base_proposal(self) -> Proposal:
        """
        Create base order proposal using Avellaneda optimal prices
        """
        market: DerivativeBase = self._market_info.market
        buys = []
        sells = []
        
        # Ensure we have calculated optimal prices
        if self._optimal_bid <= 0 or self._optimal_ask <= 0:
            self.calculate_reservation_price_and_optimal_spread()
        
        # Quantize prices and amounts
        bid_price = market.quantize_order_price(self._market_info.trading_pair, self._optimal_bid)
        ask_price = market.quantize_order_price(self._market_info.trading_pair, self._optimal_ask)
        order_size = market.quantize_order_amount(self._market_info.trading_pair, self._order_amount)
        
        if bid_price > 0 and order_size > 0:
            buys.append(PriceSize(bid_price, order_size))
        
        if ask_price > 0 and order_size > 0:
            sells.append(PriceSize(ask_price, order_size))
        
        return Proposal(buys, sells)

    def _is_hedge_mode(self) -> bool:
        return self._position_mode == PositionMode.HEDGE

    def _positions_for_trading_pair(self) -> List[Position]:
        return [
            position
            for position in self.active_positions.values()
            if position.trading_pair == self._market_info.trading_pair
        ]

    def _has_both_hedge_legs(self, positions: List[Position]) -> bool:
        sides = {
            position.position_side
            for position in positions
            if position.position_side in {PositionSide.LONG, PositionSide.SHORT}
            and position.amount != s_decimal_zero
        }
        return sides == {PositionSide.LONG, PositionSide.SHORT}

    def _log_dual_hedge_inventory_warning(self, has_dual_hedge_legs: bool):
        if has_dual_hedge_legs:
            if not self._hedge_inventory_warning_active:
                self.logger().warning(
                    "Hedge mode has both long and short legs open; using neutral inventory skew."
                )
                self._hedge_inventory_warning_active = True
        else:
            self._hedge_inventory_warning_active = False

    def apply_budget_constraint(self, proposal: Proposal):
        """Apply budget constraints to order proposal"""
        checker = self._market_info.market.budget_checker
        order_candidates = self._create_order_candidates_for_budget_check(proposal)
        adjusted_candidates = checker.adjust_candidates(order_candidates, all_or_none=True)
        self._apply_adjusted_candidates_to_proposal(adjusted_candidates, proposal)

    def _create_order_candidates_for_budget_check(self, proposal: Proposal):
        """Create order candidates for budget checking"""
        candidates = []
        
        for buy in proposal.buys:
            candidates.append(PerpetualOrderCandidate(
                self._market_info.trading_pair,
                True,  # is_maker
                OrderType.LIMIT,
                TradeType.BUY,
                buy.size,
                buy.price,
                leverage=Decimal(self._leverage),
            ))
        
        for sell in proposal.sells:
            candidates.append(PerpetualOrderCandidate(
                self._market_info.trading_pair,
                True,  # is_maker
                OrderType.LIMIT,
                TradeType.SELL,
                sell.size,
                sell.price,
                leverage=Decimal(self._leverage),
            ))
        
        return candidates

    def _apply_adjusted_candidates_to_proposal(self, adjusted_candidates, proposal: Proposal):
        """Apply budget-adjusted candidates back to proposal"""
        proposal.buys = []
        proposal.sells = []
        
        for candidate in adjusted_candidates:
            price_size = PriceSize(candidate.price, candidate.amount)
            if candidate.order_side == TradeType.BUY:
                proposal.buys.append(price_size)
            else:
                proposal.sells.append(price_size)

    def manage_positions(self, session_positions: List[Position]):
        """
        Manage existing positions with profit taking and stop loss
        """
        if self._supports_exchange_preplaced_stop_loss():
            for position in session_positions:
                self._manage_take_profit_order(position)
                self._manage_exchange_stop_loss(position)
            return

        profit_proposal = self._create_profit_taking_proposal(session_positions)
        if profit_proposal and (profit_proposal.buys or profit_proposal.sells):
            self._execute_orders_proposal(profit_proposal, PositionAction.CLOSE, exit_order_role="take_profit")

        stop_loss_proposal = self._create_stop_loss_proposal(session_positions)
        if stop_loss_proposal and (stop_loss_proposal.buys or stop_loss_proposal.sells):
            self._execute_orders_proposal(stop_loss_proposal, PositionAction.CLOSE, exit_order_role="stop_loss")

    def _supports_exchange_preplaced_stop_loss(self) -> bool:
        market_name = getattr(self._market_info.market, "name", "")
        return (
            self._exchange_preplaced_stop_loss
            and self._stop_loss_use_maker_orders
            and market_name == "binance_perpetual"
        )

    def _track_exit_order(
        self,
        order_id: str,
        role: str,
        position_side: PositionSide,
        trigger_price: Optional[Decimal] = None,
    ):
        self._exit_orders[order_id] = self.current_timestamp
        self._exit_order_roles[order_id] = (role, position_side)
        if role == "take_profit":
            self._take_profit_order_ids[position_side].add(order_id)
        elif role == "stop_loss":
            self._stop_loss_order_ids[position_side].add(order_id)
            self._stop_loss_order_details[order_id] = {
                "trigger_price": trigger_price,
                "triggered_at": None,
            }

    def _remove_exit_order_tracking(self, order_id: str):
        self._exit_orders.pop(order_id, None)
        exit_order_role = self._exit_order_roles.pop(order_id, None)
        if exit_order_role is None:
            self._stop_loss_order_details.pop(order_id, None)
            return
        role, position_side = exit_order_role
        if role == "take_profit":
            if position_side in self._take_profit_order_ids:
                self._take_profit_order_ids[position_side].discard(order_id)
        elif role == "stop_loss":
            if position_side in self._stop_loss_order_ids:
                self._stop_loss_order_ids[position_side].discard(order_id)
            self._stop_loss_order_details.pop(order_id, None)

    def _clear_exit_order_tracking(self):
        self._exit_orders.clear()
        self._exit_order_roles.clear()
        for tracked_ids in self._take_profit_order_ids.values():
            tracked_ids.clear()
        for tracked_ids in self._stop_loss_order_ids.values():
            tracked_ids.clear()
        self._stop_loss_order_details.clear()

    def _is_buy_order(self, order: Any) -> bool:
        if hasattr(order, "is_buy"):
            return order.is_buy
        return getattr(order, "trade_type", None) == TradeType.BUY

    def _order_price(self, order: Any) -> Decimal:
        return Decimal(str(getattr(order, "price", "0")))

    def _order_quantity(self, order: Any) -> Decimal:
        quantity = getattr(order, "quantity", None)
        if quantity is None:
            quantity = getattr(order, "amount", Decimal("0"))
        return Decimal(str(quantity))

    def _is_close_order_for_position(self, order: Any, position: Position) -> bool:
        is_buy = self._is_buy_order(order)
        return (position.amount > 0 and not is_buy) or (position.amount < 0 and is_buy)

    def _position_side_for_order(self, order: Any) -> Optional[PositionSide]:
        side = getattr(order, "position_side", None)
        if side in {PositionSide.LONG, PositionSide.SHORT}:
            return side
        return None

    def _active_exit_orders_for_role(self, position: Position, role: str) -> List[Any]:
        tracked_ids_by_side = self._take_profit_order_ids if role == "take_profit" else self._stop_loss_order_ids
        tracked_ids = tracked_ids_by_side.get(position.position_side, set())
        return [
            order for order in self._get_active_orders_from_exchange()
            if getattr(order, "client_order_id", None) in tracked_ids
            and self._is_close_order_for_position(order, position)
        ]

    def _conflicting_take_profit_orders_for_stop_loss(self, position: Position) -> List[Any]:
        return self._active_exit_orders_for_role(position, "take_profit")

    def _cancel_orders(self, orders: List[Any], reason: str):
        for order in orders:
            order_id = getattr(order, "client_order_id", None)
            if order_id is None:
                continue
            self._market_info.market.cancel(self._market_info.trading_pair, order_id)
            self.logger().info(f"{reason}: {order_id}")

    def _cancel_opposite_entry_orders(self, filled_trade_type: TradeType):
        cancel_buy_orders = filled_trade_type == TradeType.SELL
        opposite_entry_orders = [
            order
            for order in self._get_active_orders_from_exchange()
            if getattr(order, "is_buy", False) == cancel_buy_orders
            and getattr(order, "position", None)
            not in {PositionAction.CLOSE, PositionAction.CLOSE.value}
        ]
        if opposite_entry_orders:
            self._cancel_orders(
                opposite_entry_orders,
                "Canceling opposite entry order after entry fill",
            )

    def _is_close_order(self, order: Any) -> bool:
        position = getattr(order, "position", None)
        return position in {PositionAction.CLOSE, PositionAction.CLOSE.value}

    def _cleanup_active_close_orders(self) -> bool:
        active_close_orders = [
            order for order in self._get_active_orders_from_exchange()
            if self._is_close_order(order)
        ]
        if active_close_orders:
            self._cancel_orders(active_close_orders, "Canceling stale close order after position was closed")
            return True

        self._clear_exit_order_tracking()
        return False

    def _manage_take_profit_order(self, position: Position):
        market: DerivativeBase = self._market_info.market
        ask_price = market.get_price(self._market_info.trading_pair, True)
        bid_price = market.get_price(self._market_info.trading_pair, False)

        if position.amount > 0:
            if ask_price <= position.entry_price:
                return
            trigger_price = position.entry_price * (Decimal("1") + self._long_profit_taking_spread)
            trade_type = TradeType.SELL
        else:
            if bid_price >= position.entry_price:
                return
            trigger_price = position.entry_price * (Decimal("1") - self._short_profit_taking_spread)
            trade_type = TradeType.BUY

        price = market.quantize_order_price(self._market_info.trading_pair, trigger_price)
        size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
        if size <= 0 or price <= 0:
            return

        active_orders = self._active_exit_orders_for_role(position, "take_profit")
        matching_orders = [
            order for order in active_orders
            if self._order_price(order) == price and self._order_quantity(order) == size
        ]
        stale_orders = [order for order in active_orders if order not in matching_orders]
        if stale_orders:
            self._cancel_orders(stale_orders, "Canceling stale take profit order")
            return
        if matching_orders:
            return

        if trade_type == TradeType.BUY:
            order_id = self._market_info.market.buy(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.LIMIT,
                price=price,
                position_action=PositionAction.CLOSE,
            )
        else:
            order_id = self._market_info.market.sell(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.LIMIT,
                price=price,
                position_action=PositionAction.CLOSE,
            )
        self._track_exit_order(
            order_id=order_id,
            role="take_profit",
            position_side=position.position_side,
        )

    def _manage_exchange_stop_loss(self, position: Position):
        market: DerivativeBase = self._market_info.market
        stop_trigger_price = (
            position.entry_price * (Decimal("1") + self._stop_loss_spread)
            if position.amount < 0
            else position.entry_price * (Decimal("1") - self._stop_loss_spread)
        )
        stop_limit_price = market.quantize_order_price(self._market_info.trading_pair, stop_trigger_price)
        size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
        if size <= 0 or stop_limit_price <= 0:
            return

        active_orders = self._active_exit_orders_for_role(position, "stop_loss")
        matching_orders = [
            order for order in active_orders
            if self._order_price(order) == stop_limit_price and self._order_quantity(order) == size
        ]
        stale_orders = [order for order in active_orders if order not in matching_orders]
        if stale_orders:
            self._cancel_orders(stale_orders, "Canceling stale stop loss order")
            return

        if matching_orders:
            self._update_stop_loss_trigger_state(position, matching_orders, stop_trigger_price)
            if self._should_fallback_to_taker(position, matching_orders):
                self._cancel_orders(matching_orders, "Canceling timed out stop loss maker order")
                self._cancel_sibling_exit_orders(getattr(matching_orders[0], "client_order_id", ""))
                self._submit_stop_loss_fallback_order(position)
            return

        if position.amount < 0:
            order_id = self._market_info.market.buy(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.LIMIT,
                price=stop_limit_price,
                position_action=PositionAction.CLOSE,
                binance_order_type="STOP",
                stop_price=stop_trigger_price,
                working_type=self._stop_loss_working_type,
            )
        else:
            order_id = self._market_info.market.sell(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.LIMIT,
                price=stop_limit_price,
                position_action=PositionAction.CLOSE,
                binance_order_type="STOP",
                stop_price=stop_trigger_price,
                working_type=self._stop_loss_working_type,
            )
        self._track_exit_order(
            order_id=order_id,
            role="stop_loss",
            position_side=position.position_side,
            trigger_price=stop_trigger_price,
        )

    def _update_stop_loss_trigger_state(self, position: Position, stop_loss_orders: List[Any], trigger_price: Decimal):
        market: DerivativeBase = self._market_info.market
        current_bid = market.get_price(self._market_info.trading_pair, False)
        current_ask = market.get_price(self._market_info.trading_pair, True)
        is_triggered = (
            current_bid <= trigger_price if position.amount > 0 else current_ask >= trigger_price
        )
        for order in stop_loss_orders:
            order_id = getattr(order, "client_order_id", None)
            if order_id is None:
                continue
            details = self._stop_loss_order_details.setdefault(
                order_id, {"trigger_price": trigger_price, "triggered_at": None}
            )
            details["trigger_price"] = trigger_price
            if is_triggered:
                details["triggered_at"] = details["triggered_at"] or self.current_timestamp
            else:
                details["triggered_at"] = None

    def _should_fallback_to_taker(self, position: Position, stop_loss_orders: List[Any]) -> bool:
        if not self._stop_loss_auto_fallback or not self._stop_loss_use_maker_orders:
            return False

        for order in stop_loss_orders:
            order_id = getattr(order, "client_order_id", None)
            details = self._stop_loss_order_details.get(order_id, {})
            triggered_at = details.get("triggered_at")
            if triggered_at is not None and self.current_timestamp - triggered_at > self._stop_loss_maker_timeout:
                self.logger().info(
                    f"Stop loss maker order {order_id} timeout "
                    f"({self.current_timestamp - triggered_at:.1f}s > {self._stop_loss_maker_timeout}s), "
                    "switching to taker mode"
                )
                return True
        return False

    def _submit_stop_loss_fallback_order(self, position: Position):
        market: DerivativeBase = self._market_info.market
        size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
        if size <= 0:
            return
        if position.amount < 0:
            order_id = self._market_info.market.buy(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.MARKET,
                position_action=PositionAction.CLOSE,
            )
        else:
            order_id = self._market_info.market.sell(
                trading_pair=self._market_info.trading_pair,
                amount=size,
                order_type=OrderType.MARKET,
                position_action=PositionAction.CLOSE,
            )
        self._track_exit_order(
            order_id=order_id,
            role="stop_loss",
            position_side=position.position_side,
        )

    def _cancel_sibling_exit_orders(self, completed_order_id: str):
        completed_exit_order = self._exit_order_roles.get(completed_order_id)
        if completed_exit_order is None:
            return
        completed_role, position_side = completed_exit_order
        sibling_ids = (
            self._take_profit_order_ids[position_side]
            if completed_role == "stop_loss"
            else self._stop_loss_order_ids[position_side]
        )
        sibling_orders = [
            order for order in self._get_active_orders_from_exchange()
            if getattr(order, "client_order_id", None) in sibling_ids
        ]
        self._cancel_orders(sibling_orders, "Canceling sibling exit order")

    def _create_profit_taking_proposal(self, positions: List[Position]) -> Proposal:
        """Create profit taking orders for profitable positions"""
        market: DerivativeBase = self._market_info.market
        ask_price = market.get_price(self._market_info.trading_pair, True)
        bid_price = market.get_price(self._market_info.trading_pair, False)
        buys = []
        sells = []
        
        for position in positions:
            if position.amount > 0:  # Long position
                if ask_price > position.entry_price:  # Profitable
                    profit_price = position.entry_price * (Decimal("1") + self._long_profit_taking_spread)
                    price = market.quantize_order_price(self._market_info.trading_pair, profit_price)
                    size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
                    if price > 0 and size > 0:
                        sells.append(PriceSize(price, size))
            
            elif position.amount < 0:  # Short position
                if bid_price < position.entry_price:  # Profitable
                    profit_price = position.entry_price * (Decimal("1") - self._short_profit_taking_spread)
                    price = market.quantize_order_price(self._market_info.trading_pair, profit_price)
                    size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
                    if price > 0 and size > 0:
                        buys.append(PriceSize(price, size))
        
        return Proposal(buys, sells)

    def _create_stop_loss_proposal(self, positions: List[Position]) -> Proposal:
        """Create stop loss orders for losing positions"""
        market: DerivativeBase = self._market_info.market
        ask_price = market.get_price(self._market_info.trading_pair, True)
        bid_price = market.get_price(self._market_info.trading_pair, False)
        buys = []
        sells = []
        
        for position in positions:
            stop_loss_price = None
            
            if position.amount > 0:  # Long position
                stop_loss_price = position.entry_price * (Decimal("1") - self._stop_loss_spread)
                if bid_price <= stop_loss_price:  # Stop loss triggered
                    size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
                    price = market.quantize_order_price(
                        self._market_info.trading_pair,
                        stop_loss_price * (Decimal("1") - self._stop_loss_slippage_buffer),
                    )
                    if size > 0 and price > 0:
                        sells.append(PriceSize(price, size))
            
            elif position.amount < 0:  # Short position  
                stop_loss_price = position.entry_price * (Decimal("1") + self._stop_loss_spread)
                if ask_price >= stop_loss_price:  # Stop loss triggered
                    size = market.quantize_order_amount(self._market_info.trading_pair, abs(position.amount))
                    price = market.quantize_order_price(
                        self._market_info.trading_pair,
                        stop_loss_price * (Decimal("1") + self._stop_loss_slippage_buffer),
                    )
                    if size > 0 and price > 0:
                        buys.append(PriceSize(price, size))
        
        return Proposal(buys, sells)

    def _execute_orders_proposal(
        self,
        proposal: Proposal,
        position_action: PositionAction,
        exit_order_role: Optional[str] = None,
    ):
        """Execute order proposals - simplified following perpetual_market_making pattern"""
        order_type = OrderType.LIMIT
        
        for buy in proposal.buys:
            order_id = self._market_info.market.buy(
                trading_pair=self._market_info.trading_pair,
                amount=buy.size,
                order_type=order_type,
                price=buy.price,
                position_action=position_action
            )
            if position_action == PositionAction.CLOSE:
                self._track_exit_order(
                    order_id=order_id,
                    role=exit_order_role or "take_profit",
                    position_side=PositionSide.SHORT,
                )
        
        for sell in proposal.sells:
            order_id = self._market_info.market.sell(
                trading_pair=self._market_info.trading_pair,
                amount=sell.size,
                order_type=order_type,
                price=sell.price,
                position_action=position_action
            )
            if position_action == PositionAction.CLOSE:
                self._track_exit_order(
                    order_id=order_id,
                    role=exit_order_role or "take_profit",
                    position_side=PositionSide.LONG,
                )
        
        # CRITICAL: Update create timestamp after order execution (like perpetual_market_making)
        if position_action == PositionAction.OPEN and (proposal.buys or proposal.sells):
            next_cycle = self.current_timestamp + self._order_refresh_time
            self._create_timestamp = next_cycle

    def cancel_active_orders(self, proposal: Proposal = None):
        """FIXED: Cancel orders that need refreshing or have stale prices"""
        orders_to_cancel = []
        
        # CRITICAL FIX: Log current state for debugging
        if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
            buy_orders = [o for o in self.active_orders if o.is_buy]
            sell_orders = [o for o in self.active_orders if not o.is_buy]
            self.logger().debug(f"📋 Checking {len(buy_orders)} buy orders and {len(sell_orders)} sell orders for cancellation")
        
        for order in self.active_orders[:]:
            should_cancel = False
            cancel_reason = ""
            
            # 1. Cancel by age (primary reason)
            age = self.current_timestamp - order.creation_timestamp
            # CRITICAL FIX: Use a slightly smaller threshold to ensure orders are cancelled BEFORE refresh time
            # This prevents the case where _create_timestamp expires but orders aren't cancelled yet
            effective_refresh_time = self._order_refresh_time - 1.0  # Cancel 1 second before refresh
            if age >= effective_refresh_time:
                should_cancel = True
                cancel_reason = f"age {age:.1f}s >= {effective_refresh_time:.1f}s (refresh at {self._order_refresh_time}s)"
            
            # 2. Cancel by price deviation to prevent stale quotes
            elif proposal is not None and self._optimal_bid > 0 and self._optimal_ask > 0:
                if order.is_buy:
                    # For buy orders, check against optimal bid
                    price_deviation_pct = abs(order.price - self._optimal_bid) / self._optimal_bid * 100
                    if price_deviation_pct > float(self._order_refresh_tolerance_pct):
                        should_cancel = True
                        cancel_reason = f"buy price deviation {price_deviation_pct:.2f}% > {self._order_refresh_tolerance_pct}%"
                else:
                    # For sell orders, check against optimal ask
                    price_deviation_pct = abs(order.price - self._optimal_ask) / self._optimal_ask * 100
                    if price_deviation_pct > float(self._order_refresh_tolerance_pct):
                        should_cancel = True
                        cancel_reason = f"sell price deviation {price_deviation_pct:.2f}% > {self._order_refresh_tolerance_pct}%"
            
            if should_cancel:
                orders_to_cancel.append(order)
                if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
                    side = "BUY" if order.is_buy else "SELL"
                    self.logger().info(f"🔄 Cancelling {side} order {order.client_order_id[:8]}... - Reason: {cancel_reason}")
        
        # Cancel all orders that need cancelling
        cancelled_count = 0
        for order in orders_to_cancel:
            try:
                self._market_info.market.cancel(self._market_info.trading_pair, order.client_order_id)
                cancelled_count += 1
            except Exception as e:
                self.logger().warning(f"⚠️ Failed to cancel order {order.client_order_id}: {e}")
        
        # Update cancel timestamp if orders were cancelled
        # NOTE: We no longer need a fixed delay since to_create_orders() now confirms no active orders exist
        if cancelled_count > 0:
            if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
                self.logger().info(f"📤 Cancelled {cancelled_count} orders, waiting for exchange confirmation...")
        
        # Return whether any orders were cancelled
        return cancelled_count > 0

    def start(self, clock: Clock, timestamp: float):
        """Strategy start"""
        self._market_info.market.set_leverage(self._market_info.trading_pair, self._leverage)
        self._market_info.market.set_position_mode(self._position_mode)

    def tick(self, timestamp: float):
        """Main strategy tick"""
        if not self._position_mode_ready:
            self._position_mode_not_ready_counter += 1
            if self._position_mode_not_ready_counter == 10:
                market: DerivativeBase = self._market_info.market
                if market.ready:
                    market.set_leverage(self._market_info.trading_pair, self._leverage)
                    market.set_position_mode(self._position_mode)
                self._position_mode_not_ready_counter = 0
            return

        # Check market readiness
        if not self._all_markets_ready:
            self._all_markets_ready = all([market.ready for market in self.active_markets])
            if not self._all_markets_ready:
                return
        
        # Error cooldown: if recent order errors occurred, pause new trading cycles
        if self._last_error_timestamp > 0:
            elapsed_since_error = self.current_timestamp - self._last_error_timestamp
            if elapsed_since_error < self._error_cooldown_seconds:
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    self.logger().info(
                        f"⏸ Error cooldown active ({elapsed_since_error:.1f}s < {self._error_cooldown_seconds}s), "
                        f"skipping order creation this tick."
                    )
                return
            else:
                # Cooldown finished, reset error counter
                self._last_error_timestamp = 0.0
                self._consecutive_error_count = 0

        # Update market data
        self._collect_market_variables(timestamp)
        
        # Check if algorithm is ready (enough data collected)
        if not self.is_algorithm_ready():
            if self._ticks_to_be_ready > 0:
                self._ticks_to_be_ready -= 1
                if self._ticks_to_be_ready % 10 == 0:
                    self.logger().info(f"📊 Collecting market data... {self._ticks_to_be_ready} ticks remaining")
            return

        # Update adaptive gamma
        self.update_adaptive_gamma()

        # Check positions
        session_positions = [p for p in self.active_positions.values() 
                           if p.trading_pair == self._market_info.trading_pair]

        if not session_positions:
            # No positions - normal market making
            stale_close_orders_were_cancelled = self._cleanup_active_close_orders()
            if stale_close_orders_were_cancelled:
                self._last_timestamp = timestamp
                return
            
            # Calculate optimal prices using Avellaneda model
            self.calculate_reservation_price_and_optimal_spread()
            
            # 2. Create base proposal 
            proposal = self.create_base_proposal()
            
            # CRITICAL FIX: Cancel and create logic with proper sequencing
            # 
            # Problem: Original logic had race condition between cancel and create
            # Solution: 
            #   1. Cancel old orders if needed
            #   2. If orders were cancelled, WAIT for next tick to create new ones
            #   3. Only create new orders if no cancellation happened this tick
            
            # 3. Cancel active orders if needed (based on timing and age)
            orders_were_cancelled = self.cancel_active_orders(proposal)
            
            # CRITICAL FIX: Force cancellation if create timestamp has expired and we have orders
            # This ensures we always cancel before creating new ones when refresh time is up
            if not orders_were_cancelled and self.active_orders and self._create_timestamp <= timestamp:
                # Create timestamp has expired, force cancel all active orders
                if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
                    self.logger().info(f"🔄 Create timestamp expired, forcing cancellation of {len(self.active_orders)} active orders")
                
                for order in self.active_orders[:]:
                    try:
                        self._market_info.market.cancel(self._market_info.trading_pair, order.client_order_id)
                        orders_were_cancelled = True
                    except Exception as e:
                        self.logger().warning(f"⚠️ Failed to force cancel order {order.client_order_id}: {e}")
                
                # Orders were force cancelled - confirmation mechanism in to_create_orders() will handle the wait
            
            # 4. Create new orders following perpetual_market_making pattern
            if self.to_create_orders(proposal):
                self.apply_budget_constraint(proposal)
                self._execute_orders_proposal(proposal, PositionAction.OPEN)
        else:
            # Have positions - manage them (with exit order protection)
            if self._supports_exchange_preplaced_stop_loss() or not self._has_pending_exit_orders():
                self.manage_positions(session_positions)

        self._last_timestamp = timestamp

    def _collect_market_variables(self, timestamp: float):
        """Collect market data for volatility and liquidity calculations"""
        price = self.get_price()
        self._avg_vol.add_sample(float(price))
        
        # Initialize trading intensity if not done yet
        if self._trading_intensity is None and self._market_info.market.ready:
            self._trading_intensity = TradingIntensityIndicator(
                order_book=self._market_info.order_book,
                price_delegate=OrderBookAssetPriceDelegate(self._market_info.market, self._market_info.trading_pair),
                sampling_length=self._trading_intensity_buffer_size,
            )
        
        if self._trading_intensity:
            self._trading_intensity.calculate(timestamp)
            if self._trading_intensity.is_sampling_buffer_full:
                self._alpha, self._kappa = self._trading_intensity.current_value
                self._alpha = Decimal(str(self._alpha)) if self._alpha else Decimal("0.1")
                self._kappa = Decimal(str(self._kappa)) if self._kappa else Decimal("1.0")

    def _get_active_orders_from_exchange(self):
        """
        CRITICAL: Get active orders using the correct Hummingbot API
        
        This function uses the standard Hummingbot order tracking system to get current orders.
        Unlike get_open_orders() which doesn't exist in all connectors, these properties are
        available in all exchange connectors inheriting from ExchangePyBase.
        """
        try:
            market = self._market_info.market
            trading_pair = self._market_info.trading_pair
            
            # Method 1: Use in_flight_orders (most reliable for tracking order states)
            active_orders = []
            if hasattr(market, 'in_flight_orders'):
                for order_id, in_flight_order in market.in_flight_orders.items():
                    if (in_flight_order.trading_pair == trading_pair and 
                        not in_flight_order.is_done and 
                        not in_flight_order.is_cancelled and
                        not in_flight_order.is_failure):
                        active_orders.append(in_flight_order)
                        
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    self.logger().debug(f"📊 Found {len(active_orders)} active in-flight orders for {trading_pair}")
                return active_orders
            
            # Method 2: Use limit_orders as fallback
            elif hasattr(market, 'limit_orders'):
                limit_orders = [order for order in market.limit_orders 
                              if order.trading_pair == trading_pair]
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    self.logger().debug(f"📊 Found {len(limit_orders)} limit orders for {trading_pair}")
                return limit_orders
            
            # Method 3: Fallback to strategy's active_orders
            else:
                strategy_orders = self.active_orders
                if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                    self.logger().debug(f"📊 Using strategy tracking: {len(strategy_orders)} active orders")
                return strategy_orders
                
        except Exception as e:
            self.logger().error(f"❌ Error getting active orders: {e}")
            # Always fallback to strategy's tracking
            return self.active_orders


    def to_create_orders(self, proposal: Proposal) -> bool:
        """
        ENHANCED: Add confirmation mechanism using reliable exchange order checking
        """
        # Basic timing and proposal checks
        if not (self._create_timestamp <= self.current_timestamp and
                proposal is not None and len(proposal.buys + proposal.sells) > 0):
            return False
        
        # CRITICAL: Use reliable exchange order checking to prevent duplicate orders
        # This checks both strategy tracking AND exchange state
        strategy_orders = len(self.active_orders)
        exchange_orders = self._get_active_orders_from_exchange()
        exchange_order_count = len(exchange_orders) if exchange_orders else 0
        
        # If either source shows active orders, wait
        if strategy_orders > 0:
            if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
                self.logger().debug(f"⏳ Strategy tracking shows {strategy_orders} active orders, waiting...")
            return False
            
        if exchange_order_count > 0:
            if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
                self.logger().info(f"⏳ Exchange shows {exchange_order_count} active orders, waiting for cancellation...")
                # Log order details for debugging
                for i, order in enumerate(exchange_orders[:3]):  # Show first 3 orders
                    if hasattr(order, 'client_order_id'):
                        order_id = order.client_order_id[:8] + "..."
                    elif hasattr(order, 'order_id'):
                        order_id = order.order_id[:8] + "..."
                    else:
                        order_id = f"order_{i}"
                    self.logger().debug(f"   📋 Active order: {order_id}")
            return False
            
        # All clear - no active orders from any source
        if self._logging_options & self.OPTION_LOG_CREATE_ORDER:
            self.logger().info(f"✅ No active orders detected (strategy: {strategy_orders}, exchange: {exchange_order_count}), proceeding with creation")
        
        return True
    
    def is_algorithm_ready(self) -> bool:
        """Public wrapper to avoid AttributeError in environments calling is_algorithm_ready()."""
        try:
            # Check if algorithm has enough data to make decisions
            return (self._avg_vol is not None and 
                    self._avg_vol.is_sampling_buffer_full and 
                    self._ticks_to_be_ready <= 0)
        except AttributeError:
            # Fallback: derive readiness from volatility buffer only
            buffers_ready = (self._avg_vol is not None and getattr(self._avg_vol, 'is_sampling_buffer_full', False))
            if buffers_ready and getattr(self._avg_vol, 'current_value', None) is not None:
                volatility = self._avg_vol.current_value
                if volatility < 0.00001 or volatility > 0.5:
                    return False
            return buffers_ready
    
    def _has_pending_exit_orders(self) -> bool:
        """
        CRITICAL FIX: Check if there are pending exit orders to prevent double spending
        
        Fixed Logic: deeply trusts self._exit_orders. If we sent an order recently, 
        we assume it's pending regardless of whether it appears in active_orders yet.
        
        This prevents race condition where WebSocket hasn't updated active_orders yet
        but we've already sent an exit order.
        """
        current_time = self.current_timestamp
        
        # 1. Clean up expired exit order records (older than 10 seconds is enough for market orders)
        # Market orders should fill instantly; if they linger > 10s, something is wrong, but we should clear the lock.
        expired_orders = [order_id for order_id, timestamp in self._exit_orders.items() 
                         if current_time - timestamp > 10.0]
        
        for order_id in expired_orders:
            del self._exit_orders[order_id]
            
        # 2. Strict Check: If we have ANY record in _exit_orders, we block new exit proposals.
        # We do NOT filter by active_orders because active_orders has latency.
        if len(self._exit_orders) > 0:
            return True
        
        return False
    
    def set_timers(self, next_cycle: float):
        """Set timing for next order cycle (following spot strategy pattern)"""
        if self._create_timestamp <= self.current_timestamp:
            self._create_timestamp = next_cycle
        if self._cancel_timestamp <= self.current_timestamp:
            self._cancel_timestamp = min(self._create_timestamp, next_cycle)

    def format_status(self) -> str:
        """Format strategy status display"""
        if not self._all_markets_ready:
            return "Market connectors are not ready."
            
        lines = []
        
        # Market info
        lines.append("  📊 Avellaneda Perpetual Market Making")
        lines.append(f"  Trading Pair: {self._market_info.trading_pair}")
        lines.append(f"  Current Price: {self.get_price():.6f}")
        
        # Avellaneda model parameters
        if self.is_algorithm_ready():
            volatility_pct = float(self.get_volatility()) * 100
            lines.append(f"  🎯 Strategy Parameters:")
            lines.append(f"    Risk Factor (γ): {self.gamma:.6f}")
            if self._use_adaptive_gamma:
                lines.append(f"    (Adaptive Learning Enabled)")
            lines.append(f"    Volatility: {volatility_pct:.3f}%")
            if self._alpha and self._kappa:
                lines.append(f"    Order Book Intensity (α): {self._alpha:.6f}")
                lines.append(f"    Order Book Depth (κ): {self._kappa:.6f}")
            
            lines.append(f"  💰 Optimal Pricing:")
            lines.append(f"    Reservation Price: {self._reservation_price:.6f}")
            lines.append(f"    Optimal Spread: {self._optimal_spread:.6f}")
            lines.append(f"    Optimal Bid: {self._optimal_bid:.6f}")
            lines.append(f"    Optimal Ask: {self._optimal_ask:.6f}")
        else:
            lines.append(f"  ⏳ Collecting market data... {self._ticks_to_be_ready} ticks remaining")
        
        # Positions
        positions = [p for p in self.active_positions.values() 
                    if p.trading_pair == self._market_info.trading_pair]
        if positions:
            lines.append(f"  📈 Active Positions:")
            for pos in positions:
                pnl = (self.get_price() - pos.entry_price) * pos.amount
                lines.append(f"    {pos.position_side.name}: {pos.amount:.6f} @ {pos.entry_price:.6f} (PnL: {pnl:.4f})")
        
        # Active orders
        if self.active_orders:
            lines.append(f"  📋 Active Orders: {len(self.active_orders)}")
        
        return "\n".join(lines)

    # Event handlers
    def did_fill_order(self, order_filled_event: OrderFilledEvent):
        """Handle order fill events and update timing (following spot strategy pattern)"""
        self._last_own_trade_price = order_filled_event.price

        if (
            getattr(order_filled_event, "position", None) in {
                PositionAction.OPEN,
                PositionAction.OPEN.value,
            }
            and not self._is_hedge_mode()
        ):
            self._cancel_opposite_entry_orders(order_filled_event.trade_type)
        
        # Set timing for next order creation after fill (following spot strategy pattern)
        next_cycle = self.current_timestamp + self._filled_order_delay
        self._create_timestamp = next_cycle
        self._cancel_timestamp = min(self._cancel_timestamp, self._create_timestamp)

    def _is_stop_order_switch_algo_error(self, order_failed_event: Any) -> bool:
        error_message = str(getattr(order_failed_event, "error_message", "") or "")
        return (
            '"code":-4120' in error_message
            or "STOP_ORDER_SWITCH_ALGO" in error_message
            or "Algo Order API endpoints" in error_message
        )

    def did_fail_order(self, order_filled_event: OrderFilledEvent):
        """Handle order failure events and activate cooldown"""
        order_id = getattr(order_filled_event, "order_id", None)
        exit_order_role = self._exit_order_roles.get(order_id) if order_id is not None else None
        order_role = exit_order_role[0] if exit_order_role is not None else None

        if order_id is not None:
            self._remove_exit_order_tracking(order_id)

        is_binance_stop_order_switch_algo_error = (
            order_role == "stop_loss"
            and self._supports_exchange_preplaced_stop_loss()
            and self._is_stop_order_switch_algo_error(order_filled_event)
        )

        if is_binance_stop_order_switch_algo_error:
            self._exchange_preplaced_stop_loss = False
            if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
                self.logger().warning(
                    "⚠️ Binance stop-loss order endpoint switched to Algo API (-4120). "
                    "Disabling exchange_preplaced_stop_loss and falling back to local stop-loss logic."
                )
            return

        self._consecutive_error_count += 1
        self._last_error_timestamp = self.current_timestamp
        if self._logging_options & self.OPTION_LOG_STATUS_REPORT:
            self.logger().warning(
                f"⚠️ Order error detected. Consecutive errors: {self._consecutive_error_count}. "
                f"Entering {self._error_cooldown_seconds}s cooldown."
            )

        if self._consecutive_error_count >= self._max_consecutive_errors:
            self.logger().error(
                "❌ Max consecutive order errors reached. Consider checking balance, leverage, or connector settings."
            )

    def did_complete_buy_order(self, buy_order_completed_event: BuyOrderCompletedEvent):
        """Handle buy order completion"""
        self._cancel_sibling_exit_orders(buy_order_completed_event.order_id)
        self._remove_exit_order_tracking(buy_order_completed_event.order_id)
        if self._logging_options & self.OPTION_LOG_MAKER_ORDER_FILLED:
            self.logger().info(f"✅ Buy order completed: {buy_order_completed_event.order_id}")

    def did_complete_sell_order(self, sell_order_completed_event: SellOrderCompletedEvent):
        """Handle sell order completion"""
        self._cancel_sibling_exit_orders(sell_order_completed_event.order_id)
        self._remove_exit_order_tracking(sell_order_completed_event.order_id)
        if self._logging_options & self.OPTION_LOG_MAKER_ORDER_FILLED:
            self.logger().info(f"✅ Sell order completed: {sell_order_completed_event.order_id}")

    def did_cancel_order(self, order_cancelled_event: OrderCancelledEvent):
        self._remove_exit_order_tracking(order_cancelled_event.order_id)

    def did_change_position_mode_succeed(self, position_mode_changed_event: PositionModeChangeEvent):
        """Handle successful position mode change"""
        self._position_mode_ready = True
        self.logger().info(f"✅ Position mode changed to {position_mode_changed_event.position_mode.name}")

    def did_change_position_mode_fail(self, position_mode_changed_event: PositionModeChangeEvent):
        """Handle failed position mode change"""
        self.logger().error(f"❌ Failed to change position mode: {position_mode_changed_event}")
