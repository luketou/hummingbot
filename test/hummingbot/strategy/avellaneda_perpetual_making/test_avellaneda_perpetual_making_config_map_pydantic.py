from decimal import Decimal
from unittest.mock import patch

from hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic import (
    AvellanedaPerpetualMakingConfigMap,
)


class TestAvellanedaPerpetualMakingConfigMapPydantic:
    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_fee_floor_fields_accept_percentage_inputs(self, _):
        config = AvellanedaPerpetualMakingConfigMap(
            derivative="binance_perpetual",
            market="XAUT-USDT",
            order_amount=Decimal("0.01"),
            maker_fee_pct=Decimal("0.02"),
            assumed_exit_fee_pct=Decimal("0.02"),
            enforce_fee_floor=True,
        )

        assert config.maker_fee_pct == Decimal("0.02")
        assert config.assumed_exit_fee_pct == Decimal("0.02")
        assert config.enforce_fee_floor is True

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_directional_skew_fields_have_safe_defaults(self, _):
        config = AvellanedaPerpetualMakingConfigMap(
            derivative="binance_perpetual",
            market="XAUT-USDT",
            order_amount=Decimal("0.01"),
        )

        assert config.directional_skew_enabled is False
        assert config.max_directional_bias == Decimal("0.30")
        assert config.momentum_window_short == 5
        assert config.momentum_window_long == 20
