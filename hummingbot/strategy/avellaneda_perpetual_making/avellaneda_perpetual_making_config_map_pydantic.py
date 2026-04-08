from decimal import Decimal
from typing import Optional, Union

from pydantic import ConfigDict, Field, field_validator, model_validator

from hummingbot.client.config.config_validators import (
    validate_bool,
    validate_decimal,
    validate_int,
    validate_exchange,
    validate_market_trading_pair,
)
from hummingbot.client.config.strategy_config_data_types import BaseStrategyConfigMap
from hummingbot.client.settings import required_exchanges
from hummingbot.connector.utils import split_hb_trading_pair


class AvellanedaPerpetualMakingConfigMap(BaseStrategyConfigMap):
    strategy: str = Field(default="avellaneda_perpetual_making")
    
    derivative: str = Field(
        default=...,
        description="The derivative exchange name (e.g., binance_perpetual, bybit_perpetual)",
        json_schema_extra={
            "prompt": "Enter the derivative exchange name",
            "prompt_on_new": True,
        }
    )
    
    market: str = Field(
        default=...,
        description="The derivative market trading pair (e.g., BTC-USDT, ETH-USDT)",
        json_schema_extra={
            "prompt": lambda mi: AvellanedaPerpetualMakingConfigMap.market_prompt(mi),
            "prompt_on_new": True,
        }
    )
    
    leverage: int = Field(
        default=10,
        description="Leverage for the position (1-125)",
        ge=1,
        le=125,
        json_schema_extra={
            "prompt": "How much leverage would you like to use? (1-125)",
            "prompt_on_new": True,
        }
    )
    
    position_mode: str = Field(
        default="One-way",
        description="Position mode: One-way or Hedge",
        json_schema_extra={
            "prompt": "Which position mode would you like to use? (One-way/Hedge)",
        }
    )
    
    # Core Avellaneda Parameters
    risk_factor: Union[Decimal, str] = Field(
        default=Decimal("1.0"),
        description="Risk aversion factor (gamma) or adaptive method ('adaptive', 'simple_adaptive')",
        json_schema_extra={
            "prompt": "Enter risk factor (γ - gamma) or 'adaptive' for automatic learning",
            "prompt_on_new": True,
        }
    )
    
    order_amount: Decimal = Field(
        default=Decimal("1.0"),
        description="Order amount for each trade",
        gt=0,
        json_schema_extra={
            "prompt": lambda mi: AvellanedaPerpetualMakingConfigMap.order_amount_prompt(mi),
            "prompt_on_new": True,
        }
    )
    
    order_amount_shape_factor: Decimal = Field(
        default=Decimal("1.0"),
        description="Order amount shape factor (η - eta)",
        ge=0,
        le=2,
        json_schema_extra={
            "prompt": "Enter order amount shape factor (η - eta)",
        }
    )
    
    min_spread: Decimal = Field(
        default=Decimal("0.00005"),  # 降低預設值到 0.005% 適合高頻
        description="Minimum spread percentage (0.005% = 0.00005)",
        ge=0,
        json_schema_extra={
            "prompt": "Enter minimum spread percentage (0.005% = 0.00005)",
        }
    )
    
    # 新增：強制使用最小 spread (用於刷量場景)
    force_min_spread: bool = Field(
        default=False,
        description="Force use minimum spread instead of Avellaneda calculation (for volume farming)",
        json_schema_extra={
            "prompt": "Force use minimum spread for high-frequency trading? (Yes/No)",
        }
    )

    maker_fee_pct: Decimal = Field(
        default=Decimal("0.02"),
        description="Maker fee percentage charged on entry orders",
        ge=0,
        json_schema_extra={
            "prompt": "Enter maker fee percentage charged on entry orders",
        }
    )

    assumed_exit_fee_pct: Decimal = Field(
        default=Decimal("0.02"),
        description="Assumed fee percentage charged when exiting inventory",
        ge=0,
        json_schema_extra={
            "prompt": "Enter assumed exit fee percentage charged when exiting inventory",
        }
    )

    fee_floor_buffer_pct: Decimal = Field(
        default=Decimal("0.0"),
        description="Additional spread buffer percentage added on top of fee floor",
        ge=0,
        json_schema_extra={
            "prompt": "Enter additional spread buffer percentage added on top of fee floor",
        }
    )

    enforce_fee_floor: bool = Field(
        default=False,
        description="Enforce a fee-aware minimum spread floor",
        json_schema_extra={
            "prompt": "Enforce a fee-aware minimum spread floor? (Yes/No)",
        }
    )

    directional_skew_enabled: bool = Field(
        default=False,
        description="Enable directional quote skew",
        json_schema_extra={
            "prompt": "Enable directional quote skew? (Yes/No)",
        }
    )

    max_directional_bias: Decimal = Field(
        default=Decimal("0.3"),
        description="Maximum absolute directional bias applied to quotes",
        ge=0,
        json_schema_extra={
            "prompt": "Enter maximum absolute directional bias applied to quotes",
        }
    )

    momentum_window_short: int = Field(
        default=5,
        description="Short lookback window for momentum bias",
        gt=0,
        json_schema_extra={
            "prompt": "Enter short lookback window for momentum bias",
        }
    )

    momentum_window_long: int = Field(
        default=20,
        description="Long lookback window for momentum bias",
        gt=0,
        json_schema_extra={
            "prompt": "Enter long lookback window for momentum bias",
        }
    )

    order_flow_window: int = Field(
        default=20,
        description="Depth levels to inspect for order flow imbalance",
        gt=0,
        json_schema_extra={
            "prompt": "Enter depth levels to inspect for order flow imbalance",
        }
    )

    funding_rate_bias_enabled: bool = Field(
        default=False,
        description="Enable funding-rate contribution to directional bias",
        json_schema_extra={
            "prompt": "Enable funding-rate contribution to directional bias? (Yes/No)",
        }
    )

    funding_rate_weight: Decimal = Field(
        default=Decimal("0.1"),
        description="Weight of funding-rate directional bias",
        ge=0,
        json_schema_extra={
            "prompt": "Enter weight of funding-rate directional bias",
        }
    )

    momentum_weight: Decimal = Field(
        default=Decimal("0.45"),
        description="Weight of momentum directional bias",
        ge=0,
        json_schema_extra={
            "prompt": "Enter weight of momentum directional bias",
        }
    )

    order_flow_weight: Decimal = Field(
        default=Decimal("0.45"),
        description="Weight of order-flow directional bias",
        ge=0,
        json_schema_extra={
            "prompt": "Enter weight of order-flow directional bias",
        }
    )
    
    inventory_target_base_pct: Decimal = Field(
        default=Decimal("50"),
        description="Target base asset percentage (0-100%)",
        ge=0,
        le=100,
        json_schema_extra={
            "prompt": "Enter target base asset percentage (0-100%)",
        }
    )
    
    # Market Data Parameters
    volatility_buffer_size: int = Field(
        default=200,
        description="Volatility buffer size (number of price ticks)",
        ge=10,
        le=1000,
        json_schema_extra={
            "prompt": "Enter volatility buffer size (number of price ticks)",
        }
    )
    
    trading_intensity_buffer_size: int = Field(
        default=200,
        description="Trading intensity buffer size (number of ticks)",
        ge=10,
        le=1000,
        json_schema_extra={
            "prompt": "Enter trading intensity buffer size (number of ticks)",
        }
    )
    
    # Order Management
    order_refresh_time: float = Field(
        default=30.0,
        description="Order refresh time in seconds",
        gt=0,
        json_schema_extra={
            "prompt": "Enter order refresh time in seconds",
        }
    )
    
    order_refresh_tolerance_pct: Decimal = Field(
        default=Decimal("1.0"),
        description="Order refresh tolerance percentage",
        ge=-10,
        le=10,
        json_schema_extra={
            "prompt": "Enter order refresh tolerance percentage",
        }
    )
    
    filled_order_delay: float = Field(
        default=15.0,
        description="Filled order delay in seconds",
        ge=0,
        json_schema_extra={
            "prompt": "Enter filled order delay in seconds",
        }
    )
    
    # Position Management
    long_profit_taking_spread: Decimal = Field(
        default=Decimal("3.0"),
        description="Profit taking spread for long positions (percentage)",
        ge=0,
        json_schema_extra={
            "prompt": "Enter profit taking spread for long positions (percentage)",
        }
    )
    
    short_profit_taking_spread: Decimal = Field(
        default=Decimal("3.0"),
        description="Profit taking spread for short positions (percentage)",
        ge=0,
        json_schema_extra={
            "prompt": "Enter profit taking spread for short positions (percentage)",
        }
    )
    
    stop_loss_spread: Decimal = Field(
        default=Decimal("10.0"),
        description="Stop loss spread (percentage)",
        ge=0,
        json_schema_extra={
            "prompt": "Enter stop loss spread (percentage)",
        }
    )
    
    time_between_stop_loss_orders: float = Field(
        default=60.0,
        description="Time between stop loss orders in seconds",
        gt=0,
        json_schema_extra={
            "prompt": "Enter time between stop loss orders in seconds",
        }
    )
    
    stop_loss_slippage_buffer: Decimal = Field(
        default=Decimal("0.5"),
        description="Stop loss slippage buffer (percentage)",
        ge=0,
        json_schema_extra={
            "prompt": "Enter stop loss slippage buffer (percentage)",
        }
    )

    stop_loss_use_maker_orders: bool = Field(
        default=False,
        description="Use maker orders for stop loss before falling back to taker mode",
        json_schema_extra={
            "prompt": "Do you want to use maker orders for stop loss? (Yes/No)",
        }
    )

    stop_loss_maker_timeout: float = Field(
        default=60.0,
        description="How long maker stop loss orders should wait before switching to taker mode",
        gt=0,
        json_schema_extra={
            "prompt": "How long should maker stop loss orders wait before switching to taker mode? (in seconds)",
        }
    )

    stop_loss_auto_fallback: bool = Field(
        default=True,
        description="Automatically fallback to taker stop loss orders if maker stop loss orders timeout",
        json_schema_extra={
            "prompt": "Should the strategy automatically fallback to taker orders if maker orders timeout? (Yes/No)",
        }
    )

    stop_loss_working_type: str = Field(
        default="CONTRACT_PRICE",
        description="Trigger source for exchange stop-loss order (MARK_PRICE or CONTRACT_PRICE)",
        json_schema_extra={
            "prompt": "Enter trigger source for exchange stop-loss order (MARK_PRICE/CONTRACT_PRICE)",
        }
    )

    exchange_preplaced_stop_loss: bool = Field(
        default=False,
        description="Place exchange-visible stop loss orders when the position opens",
        json_schema_extra={
            "prompt": "Place exchange-visible stop loss orders when the position opens? (Yes/No)",
        }
    )
    
    # Adaptive Gamma Parameters
    adaptive_gamma_enabled: bool = Field(
        default=False,
        description="Enable adaptive gamma learning",
        json_schema_extra={
            "prompt": "Enable adaptive gamma learning? (Yes/No)",
        }
    )
    
    adaptive_gamma_initial: Decimal = Field(
        default=Decimal("0.01"),  # 降低初始值，適合高頻交易
        description="Initial gamma value for adaptive learning",
        gt=0,
        json_schema_extra={
            "prompt": "Enter initial gamma value for adaptive learning",
        }
    )
    
    adaptive_gamma_learning_rate: Decimal = Field(
        default=Decimal("0.01"),
        description="Learning rate for adaptive gamma (0.001-0.1)",
        gt=0,
        le=1,
        json_schema_extra={
            "prompt": "Enter learning rate for adaptive gamma (0.001-0.1)",
        }
    )
    
    adaptive_gamma_min: Decimal = Field(
        default=Decimal("0.001"),  # 降低預設最小值到 0.001
        description="Minimum gamma value",
        gt=0,
        json_schema_extra={
            "prompt": "Enter minimum gamma value",
        }
    )
    
    adaptive_gamma_max: Decimal = Field(
        default=Decimal("10.0"),
        description="Maximum gamma value",
        gt=0,
        json_schema_extra={
            "prompt": "Enter maximum gamma value",
        }
    )
    
    adaptive_gamma_reward_window: int = Field(
        default=100,
        description="Reward window size for adaptive learning",
        gt=0,
        json_schema_extra={
            "prompt": "Enter reward window size for adaptive learning",
        }
    )
    
    adaptive_gamma_update_frequency: int = Field(
        default=10,
        description="Update frequency for adaptive gamma",
        gt=0,
        json_schema_extra={
            "prompt": "Enter update frequency for adaptive gamma",
        }
    )

    model_config = ConfigDict(title="avellaneda_perpetual_making")

    # === prompts ===

    @classmethod
    def market_prompt(cls, model_instance: 'AvellanedaPerpetualMakingConfigMap') -> str:
        derivative = model_instance.derivative
        return f"Enter the token trading pair you would like to trade on {derivative}"

    @classmethod
    def order_amount_prompt(cls, model_instance: 'AvellanedaPerpetualMakingConfigMap') -> str:
        trading_pair = model_instance.market
        base_asset, quote_asset = split_hb_trading_pair(trading_pair)
        return f"What is the amount of {base_asset} per order?"

    # === validations ===

    @field_validator("derivative", mode="before")
    @classmethod
    def validate_derivative(cls, v: str):
        """Validate derivative exchange"""
        from hummingbot.client.config.config_validators import validate_derivative
        ret = validate_derivative(v)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator("market", mode="before")
    @classmethod
    def validate_market(cls, v: str, info):
        """Validate market trading pair"""
        # Get derivative from the context
        if hasattr(info, 'data') and 'derivative' in info.data:
            derivative = info.data['derivative']
            ret = validate_market_trading_pair(derivative, v)
            if ret is not None:
                raise ValueError(ret)
        # If derivative not available yet, just validate format
        else:
            if '-' not in v:
                raise ValueError("Trading pair must be in BASE-QUOTE format (e.g., BTC-USDT)")
            base, quote = v.split('-', 1)
            if not base or not quote:
                raise ValueError("Invalid trading pair format")
        return v

    @field_validator("position_mode", mode="before")
    @classmethod
    def validate_position_mode(cls, v: str):
        """Validate position mode"""
        valid_modes = ["One-way", "Hedge"]
        if v not in valid_modes:
            raise ValueError(f"Invalid position mode. Choose from: {valid_modes}")
        return v

    @field_validator("risk_factor", mode="before")
    @classmethod
    def validate_risk_factor(cls, v):
        """Validate risk factor - can be decimal or adaptive method string"""
        if isinstance(v, str):
            valid_methods = ["adaptive", "simple_adaptive"]
            if v.lower() in valid_methods:
                return v.lower()
            else:
                # Try to parse as decimal
                try:
                    decimal_v = Decimal(v)
                    if decimal_v <= 0:
                        raise ValueError("Risk factor must be greater than 0")
                    return decimal_v
                except:
                    raise ValueError(f"Invalid risk factor. Use a positive number or one of: {valid_methods}")
        else:
            # Handle Decimal or numeric input
            if isinstance(v, (int, float)):
                v = Decimal(str(v))
            if v <= 0:
                raise ValueError("Risk factor must be greater than 0")
            return v

    @field_validator(
        "order_amount",
        "order_amount_shape_factor",
        "min_spread",
        "maker_fee_pct",
        "assumed_exit_fee_pct",
        "fee_floor_buffer_pct",
        "max_directional_bias",
        "inventory_target_base_pct",
        "order_refresh_tolerance_pct",
        "long_profit_taking_spread",
        "short_profit_taking_spread", 
        "stop_loss_spread",
        "stop_loss_slippage_buffer",
        "funding_rate_weight",
        "momentum_weight",
        "order_flow_weight",
        "adaptive_gamma_initial",
        "adaptive_gamma_learning_rate",
        "adaptive_gamma_min",
        "adaptive_gamma_max",
        mode="before"
    )
    @classmethod
    def validate_decimal_fields(cls, v):
        """Used for client-friendly error output."""
        # Convert to string for validation if it's not already
        v_str = str(v)
        ret = validate_decimal(v_str, min_value=Decimal("0"), inclusive=True)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "leverage",
        "volatility_buffer_size",
        "trading_intensity_buffer_size",
        "momentum_window_short",
        "momentum_window_long",
        "order_flow_window",
        "adaptive_gamma_reward_window",
        "adaptive_gamma_update_frequency",
        mode="before"
    )
    @classmethod
    def validate_int_fields(cls, v):
        """Used for client-friendly error output."""
        # Convert to string for validation if it's not already
        v_str = str(v)
        ret = validate_int(v_str, min_value=1)
        if ret is not None:
            raise ValueError(ret)
        return v

    @field_validator(
        "order_refresh_time",
        "filled_order_delay",
        "time_between_stop_loss_orders",
        mode="before"
    )
    @classmethod
    def validate_float_fields(cls, v):
        """Used for client-friendly error output."""
        try:
            float_val = float(v)
            if float_val < 0:
                raise ValueError("Value must be non-negative")
            return float_val
        except Exception:
            raise ValueError("Must be a valid number")

    @field_validator(
        "adaptive_gamma_enabled",
        "enforce_fee_floor",
        "directional_skew_enabled",
        "funding_rate_bias_enabled",
        "stop_loss_use_maker_orders",
        "stop_loss_auto_fallback",
        "exchange_preplaced_stop_loss",
        mode="before"
    )
    @classmethod
    def validate_bool_field(cls, v):
        """Used for client-friendly error output."""
        if isinstance(v, str):
            ret = validate_bool(v)
            if ret is not None:
                raise ValueError(ret)
        return v

    @field_validator("stop_loss_working_type", mode="before")
    @classmethod
    def validate_stop_loss_working_type(cls, v: str):
        valid_values = ["MARK_PRICE", "CONTRACT_PRICE"]
        if v not in valid_values:
            raise ValueError(f"Invalid stop loss working type. Choose from: {valid_values}")
        return v

    # === post-validations ===

    @model_validator(mode="after")
    def post_validations(self):
        required_exchanges.add(self.derivative)
        
        # Validate gamma parameter relationships when adaptive is enabled
        if self.adaptive_gamma_enabled:
            if self.adaptive_gamma_min >= self.adaptive_gamma_max:
                raise ValueError("adaptive_gamma_min must be less than adaptive_gamma_max")
            
            if not (self.adaptive_gamma_min <= self.adaptive_gamma_initial <= self.adaptive_gamma_max):
                raise ValueError("adaptive_gamma_initial must be between adaptive_gamma_min and adaptive_gamma_max")
        
        # Validate spread relationships
        if self.long_profit_taking_spread <= self.min_spread:
            raise ValueError("Long profit taking spread should be greater than min spread")
        
        if self.short_profit_taking_spread <= self.min_spread:
            raise ValueError("Short profit taking spread should be greater than min spread")
        
        if self.stop_loss_spread <= max(self.long_profit_taking_spread, self.short_profit_taking_spread):
            raise ValueError("Stop loss spread should be greater than profit taking spreads")
        
        return self
