from decimal import Decimal
from unittest.mock import patch

import pytest

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
            fee_floor_buffer_pct=Decimal("0.02"),
            enforce_fee_floor=True,
        )

        assert config.maker_fee_pct == Decimal("0.02")
        assert config.assumed_exit_fee_pct == Decimal("0.02")
        assert config.fee_floor_buffer_pct == Decimal("0.02")
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
        assert config.order_flow_window == 20
        assert config.funding_rate_bias_enabled is False
        assert config.funding_rate_weight == Decimal("0.10")
        assert config.momentum_weight == Decimal("0.45")
        assert config.order_flow_weight == Decimal("0.45")

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_momentum_window_short_must_be_less_than_long(self, _):
        with pytest.raises(
            ValueError,
            match="momentum_window_short must be less than momentum_window_long",
        ):
            AvellanedaPerpetualMakingConfigMap(
                derivative="binance_perpetual",
                market="XAUT-USDT",
                order_amount=Decimal("0.01"),
                momentum_window_short=20,
                momentum_window_long=20,
            )

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_stop_loss_working_type_defaults_to_mark_price(self, _):
        config = AvellanedaPerpetualMakingConfigMap(
            derivative="binance_perpetual",
            market="XAUT-USDT",
            order_amount=Decimal("0.01"),
        )

        assert config.stop_loss_working_type == "MARK_PRICE"

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_stop_loss_working_type_normalizes_mark_price(self, _):
        config = AvellanedaPerpetualMakingConfigMap(
            derivative="binance_perpetual",
            market="XAUT-USDT",
            order_amount=Decimal("0.01"),
            stop_loss_working_type="mark_price",
        )

        assert config.stop_loss_working_type == "MARK_PRICE"

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_stop_loss_working_type_normalizes_contract_price(self, _):
        config = AvellanedaPerpetualMakingConfigMap(
            derivative="binance_perpetual",
            market="XAUT-USDT",
            order_amount=Decimal("0.01"),
            stop_loss_working_type="contract_price",
        )

        assert config.stop_loss_working_type == "CONTRACT_PRICE"

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_stop_loss_working_type_invalid_value_raises(self, _):
        with pytest.raises(
            ValueError,
            match="Invalid stop_loss_working_type. Choose from: MARK_PRICE, CONTRACT_PRICE",
        ):
            AvellanedaPerpetualMakingConfigMap(
                derivative="binance_perpetual",
                market="XAUT-USDT",
                order_amount=Decimal("0.01"),
                stop_loss_working_type="LAST_PRICE",
            )

    @patch(
        "hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic.validate_market_trading_pair",
        return_value=None,
    )
    def test_stop_loss_working_type_non_string_raises(self, _):
        with pytest.raises(
            ValueError,
            match="stop_loss_working_type must be a string: MARK_PRICE or CONTRACT_PRICE",
        ):
            AvellanedaPerpetualMakingConfigMap(
                derivative="binance_perpetual",
                market="XAUT-USDT",
                order_amount=Decimal("0.01"),
                stop_loss_working_type=123,
            )
