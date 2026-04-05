from decimal import Decimal
from unittest import TestCase

from hummingbot.connector.exchange.paper_trade.paper_trade_exchange import QuantizationParams
from hummingbot.core.data_type.trade_fee import TradeFeeSchema
from hummingbot.strategy.__utils__.trailing_indicators.instant_volatility import InstantVolatilityIndicator
from hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making import (
    AvellanedaPerpetualMakingStrategy,
)
from hummingbot.strategy.market_trading_pair_tuple import MarketTradingPairTuple
from test.mock.mock_perp_connector import MockPerpConnector


class AvellanedaPerpetualMakingStrategyTests(TestCase):
    trading_pair = "COINALPHA-HBOT"
    initial_mid_price = Decimal("100")

    def setUp(self):
        super().setUp()
        self.market = MockPerpConnector(
            trade_fee_schema=TradeFeeSchema(
                maker_percent_fee_decimal=Decimal("0.0001"),
                taker_percent_fee_decimal=Decimal("0.0001"),
            )
        )
        self.market.set_quantization_param(
            QuantizationParams(
                self.trading_pair,
                price_precision=6,
                price_decimals=2,
                order_size_precision=6,
                order_size_decimals=2,
            )
        )
        self.market.set_balanced_order_book(
            trading_pair=self.trading_pair,
            mid_price=float(self.initial_mid_price),
            min_price=1,
            max_price=200,
            price_step_size=1,
            volume_step_size=10,
        )
        self.market.set_balance("COINALPHA", Decimal("100"))
        self.market.set_balance("HBOT", Decimal("10000"))
        self.market_info = MarketTradingPairTuple(
            self.market, self.trading_pair, "COINALPHA", "HBOT"
        )
        self.strategy = AvellanedaPerpetualMakingStrategy()
        self.strategy.init_params(
            market_info=self.market_info,
            order_amount=Decimal("1"),
            min_spread=Decimal("0.00005"),
            force_min_spread=False,
        )
        self.strategy._position_mode_ready = True

    def ready_vol_indicator(self):
        indicator = InstantVolatilityIndicator(sampling_length=5, processing_length=1)
        for sample in [100, 100.01, 99.99, 100.02, 99.98, 100]:
            indicator.add_sample(sample)
        return indicator

    def test_fee_floor_overrides_too_small_spread(self):
        self.strategy.init_params(
            market_info=self.market_info,
            min_spread=Decimal("0.00005"),
            force_min_spread=False,
            maker_fee_pct=Decimal("0.0002"),
            assumed_exit_fee_pct=Decimal("0.0002"),
            enforce_fee_floor=True,
            order_amount=Decimal("1"),
        )
        self.strategy._position_mode_ready = True
        self.strategy._avg_vol = self.ready_vol_indicator()
        self.strategy._alpha = Decimal("0.1")
        self.strategy._kappa = Decimal("100")
        self.strategy._risk_factor = Decimal("0.1")

        self.strategy.calculate_reservation_price_and_optimal_spread()

        self.assertGreaterEqual(self.strategy._optimal_spread, Decimal("0.04"))

    def test_force_min_spread_uses_max_of_config_floor_and_fee_floor(self):
        self.strategy._force_min_spread = True
        self.strategy._min_spread = Decimal("0.0006")
        self.strategy._maker_fee_pct = Decimal("0.0002")
        self.strategy._assumed_exit_fee_pct = Decimal("0.0005")
        self.strategy._enforce_fee_floor = True

        floor = self.strategy._effective_min_total_spread(self.initial_mid_price)

        self.assertEqual(Decimal("0.07"), floor)
