import logging
import unittest.mock
from decimal import Decimal

import hummingbot.strategy.avellaneda_perpetual_making.start as strategy_start
from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.connector.exchange_base import ExchangeBase
from hummingbot.strategy.avellaneda_perpetual_making.avellaneda_perpetual_making_config_map_pydantic import (
    AvellanedaPerpetualMakingConfigMap,
)
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase


class AvellanedaPerpetualStartTest(IsolatedAsyncioWrapperTestCase):
    level = 0

    def setUp(self) -> None:
        super().setUp()
        self.strategy = None
        self.markets = {"binance_perpetual": ExchangeBase()}
        self.notifications = []
        self.log_records = []
        self._logger = None
        self.strategy_config_map = ClientConfigAdapter(
            AvellanedaPerpetualMakingConfigMap(
                derivative="binance_perpetual",
                market="XAUT-USDT",
                order_amount=Decimal("0.01"),
            )
        )

    async def initialize_markets(self, market_names):
        return None

    def notify(self, message):
        self.notifications.append(message)

    def logger(self):
        if self._logger is None:
            self._logger = logging.getLogger(self.__class__.__name__)
            self._logger.addHandler(self)
        return self._logger

    def handle(self, record):
        self.log_records.append(record)

    @unittest.mock.patch(
        "hummingbot.strategy.avellaneda_perpetual_making.start.HummingbotApplication"
    )
    async def test_start_passes_force_min_spread_and_new_fee_fields(self, mock_hbot):
        mock_hbot.main_application().strategy_file_name = "test.yml"
        c_map = self.strategy_config_map
        c_map.force_min_spread = True
        c_map.min_spread = Decimal("0.0006")
        c_map.maker_fee_pct = Decimal("0.02")
        c_map.assumed_exit_fee_pct = Decimal("0.02")
        c_map.fee_floor_buffer_pct = Decimal("0.02")
        c_map.enforce_fee_floor = True
        c_map.directional_skew_enabled = True
        c_map.max_directional_bias = Decimal("0.20")
        c_map.momentum_window_short = 6
        c_map.momentum_window_long = 24
        c_map.order_flow_window = 12
        c_map.funding_rate_bias_enabled = True
        c_map.funding_rate_weight = Decimal("0.15")
        c_map.momentum_weight = Decimal("0.35")
        c_map.order_flow_weight = Decimal("0.50")

        await strategy_start.start(self)

        self.assertTrue(self.strategy._force_min_spread)
        self.assertTrue(self.strategy._enforce_fee_floor)
        self.assertEqual(Decimal("0.0002"), self.strategy._maker_fee_pct)
        self.assertEqual(Decimal("0.0002"), self.strategy._assumed_exit_fee_pct)
        self.assertEqual(Decimal("0.0002"), self.strategy._fee_floor_buffer_pct)
        self.assertTrue(self.strategy._directional_skew_enabled)
        self.assertEqual(Decimal("0.20"), self.strategy._max_directional_bias)
        self.assertEqual(6, self.strategy._momentum_window_short)
        self.assertEqual(24, self.strategy._momentum_window_long)
        self.assertEqual(12, self.strategy._order_flow_window)
        self.assertTrue(self.strategy._funding_rate_bias_enabled)
        self.assertEqual(Decimal("0.15"), self.strategy._funding_rate_weight)
        self.assertEqual(Decimal("0.35"), self.strategy._momentum_weight)
        self.assertEqual(Decimal("0.50"), self.strategy._order_flow_weight)

    @unittest.mock.patch(
        "hummingbot.strategy.avellaneda_perpetual_making.start.HummingbotApplication"
    )
    async def test_start_passes_stop_loss_working_type(self, mock_hbot):
        mock_hbot.main_application().strategy_file_name = "test.yml"
        c_map = self.strategy_config_map
        c_map.exchange_preplaced_stop_loss = True
        c_map.stop_loss_working_type = "CONTRACT_PRICE"

        await strategy_start.start(self)

        self.assertTrue(self.strategy._exchange_preplaced_stop_loss)
        self.assertEqual("CONTRACT_PRICE", self.strategy._stop_loss_working_type)
