from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from hummingbot.connector.exchange.paper_trade.paper_trade_exchange import (
    QuantizationParams,
)
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import TradeFeeSchema
from hummingbot.strategy.__utils__.trailing_indicators.instant_volatility import (
    InstantVolatilityIndicator,
)
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

    def test_fee_floor_is_used_in_exception_fallback(self):
        self.strategy._min_spread = Decimal("0.00005")
        self.strategy._maker_fee_pct = Decimal("0.0002")
        self.strategy._assumed_exit_fee_pct = Decimal("0.0002")
        self.strategy._fee_floor_buffer_pct = Decimal("0.0002")
        self.strategy._enforce_fee_floor = True

        with patch.object(
            self.strategy,
            "calculate_inventory_deviation",
            side_effect=RuntimeError("boom"),
        ):
            self.strategy.calculate_reservation_price_and_optimal_spread()

        self.assertEqual(Decimal("0.06"), self.strategy._optimal_spread)
        self.assertEqual(Decimal("99.97"), self.strategy._optimal_bid)
        self.assertEqual(Decimal("100.03"), self.strategy._optimal_ask)

    def test_positive_bias_moves_bid_closer_and_ask_farther(self):
        bid, ask = self.strategy._apply_directional_skew(
            reservation_price=Decimal("100"),
            total_spread=Decimal("1"),
            directional_bias=Decimal("0.20"),
        )

        self.assertEqual(Decimal("99.6"), bid)
        self.assertEqual(Decimal("100.6"), ask)

    def test_negative_bias_moves_ask_closer_and_bid_farther(self):
        bid, ask = self.strategy._apply_directional_skew(
            reservation_price=Decimal("100"),
            total_spread=Decimal("1"),
            directional_bias=Decimal("-0.20"),
        )

        self.assertEqual(Decimal("99.4"), bid)
        self.assertEqual(Decimal("100.4"), ask)

    def test_directional_bias_is_applied_to_optimal_bid_and_ask(self):
        self.strategy._directional_skew_enabled = True
        self.strategy._avg_vol = self.ready_vol_indicator()
        self.strategy._alpha = Decimal("0.1")
        self.strategy._kappa = Decimal("100")
        self.strategy._risk_factor = Decimal("0.1")

        with patch.object(
            self.strategy, "_compute_directional_bias", return_value=Decimal("-0.20")
        ):
            self.strategy.calculate_reservation_price_and_optimal_spread()

        self.assertLess(
            self.strategy._optimal_bid,
            self.strategy._reservation_price
            - (self.strategy._optimal_spread / Decimal("2")),
        )
        self.assertLess(
            self.strategy._optimal_ask,
            self.strategy._reservation_price
            + (self.strategy._optimal_spread / Decimal("2")),
        )

    def test_submit_exchange_stop_order_uses_configured_working_type_for_sell(self):
        self.strategy._stop_loss_working_type = "CONTRACT_PRICE"

        with patch.object(
            self.market, "sell", return_value="stop-sell-order-id"
        ) as mock_sell:
            self.strategy._submit_exchange_stop_order(
                side=TradeType.SELL,
                amount=Decimal("1"),
                stop_price=Decimal("95"),
            )

        mock_sell.assert_called_once()
        sell_kwargs = mock_sell.call_args.kwargs
        self.assertEqual("STOP_MARKET", sell_kwargs["binance_order_type"])
        self.assertEqual("CONTRACT_PRICE", sell_kwargs["working_type"])
        self.assertTrue(sell_kwargs["reduce_only"])
        self.assertEqual(
            "stop-sell-order-id",
            self.strategy._exchange_stop_orders[TradeType.SELL]["order_id"],
        )

    def test_submit_exchange_stop_order_uses_configured_working_type_for_buy(self):
        self.strategy._stop_loss_working_type = "MARK_PRICE"

        with patch.object(
            self.market, "buy", return_value="stop-buy-order-id"
        ) as mock_buy:
            self.strategy._submit_exchange_stop_order(
                side=TradeType.BUY,
                amount=Decimal("2"),
                stop_price=Decimal("105"),
            )

        mock_buy.assert_called_once()
        buy_kwargs = mock_buy.call_args.kwargs
        self.assertEqual("STOP_MARKET", buy_kwargs["binance_order_type"])
        self.assertEqual("MARK_PRICE", buy_kwargs["working_type"])
        self.assertTrue(buy_kwargs["reduce_only"])
        self.assertEqual(
            "stop-buy-order-id",
            self.strategy._exchange_stop_orders[TradeType.BUY]["order_id"],
        )
