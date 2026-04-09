import ast
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import List

from hummingbot.core.data_type.common import PositionMode, PositionSide


def _load_inventory_methods():
    source_path = (
        Path(__file__).resolve().parents[4]
        / "hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py"
    )
    module_ast = ast.parse(source_path.read_text())
    strategy_class = next(
        node
        for node in module_ast.body
        if isinstance(node, ast.ClassDef)
        and node.name == "AvellanedaPerpetualMakingStrategy"
    )
    target_names = {
        "calculate_inventory_deviation",
        "calculate_reservation_price_and_optimal_spread",
        "_positions_for_trading_pair",
        "_has_both_hedge_legs",
        "_is_hedge_mode",
        "_log_dual_hedge_inventory_warning",
    }
    target_methods = [
        node
        for node in strategy_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name in target_names
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Decimal": Decimal,
        "List": List,
        "Position": object,
        "PositionMode": PositionMode,
        "PositionSide": PositionSide,
        "s_decimal_zero": Decimal("0"),
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace


LOADED_METHODS = _load_inventory_methods()


class FakeLogger:
    def __init__(self):
        self.warning_messages = []

    def warning(self, message):
        self.warning_messages.append(message)

    def debug(self, message):
        pass

    def error(self, message):
        raise AssertionError(message)

    def info(self, message):
        pass


class FakeMarket:
    def __init__(self, quote_balance: Decimal):
        self._quote_balance = quote_balance

    def get_balance(self, asset: str) -> Decimal:
        return self._quote_balance


class FakeInventoryStrategy:
    OPTION_LOG_STATUS_REPORT = 1 << 5
    calculate_inventory_deviation = LOADED_METHODS["calculate_inventory_deviation"]
    calculate_reservation_price_and_optimal_spread = LOADED_METHODS[
        "calculate_reservation_price_and_optimal_spread"
    ]

    def __init__(self):
        self._market_info = SimpleNamespace(
            trading_pair="XAUT-USDT",
            base_asset="XAUT",
            quote_asset="USDT",
            market=FakeMarket(quote_balance=Decimal("1000")),
        )
        self._position_mode = PositionMode.HEDGE
        self.account_positions = {}
        self._inventory_target_base_pct = Decimal("50")
        self._reservation_price = Decimal("0")
        self._optimal_spread = Decimal("0")
        self._optimal_bid = Decimal("0")
        self._optimal_ask = Decimal("0")
        self._risk_factor = Decimal("1")
        self._order_amount = Decimal("0.01")
        self._alpha = Decimal("1")
        self._kappa = Decimal("100")
        self._min_spread = Decimal("0.001")
        self._force_min_spread = False
        self._logging_options = 0
        self._use_adaptive_gamma = False
        self._gamma_learner = None
        self._hedge_inventory_warning_active = False
        self._logger = FakeLogger()
        self._price = Decimal("4743.88")

    @property
    def active_positions(self):
        return self.account_positions

    @property
    def gamma(self):
        return self._risk_factor

    @property
    def inventory_target_base(self):
        return self._inventory_target_base_pct / Decimal("100")

    def get_price(self) -> Decimal:
        return self._price

    def get_volatility(self) -> Decimal:
        return Decimal("0.02")

    def logger(self):
        return self._logger

    def _positions_for_trading_pair(self):
        return [
            position
            for position in self.active_positions.values()
            if position.trading_pair == self._market_info.trading_pair
        ]

    def _has_both_hedge_legs(self, positions):
        sides = {
            position.position_side
            for position in positions
            if position.position_side in {PositionSide.LONG, PositionSide.SHORT}
            and position.amount != Decimal("0")
        }
        return sides == {PositionSide.LONG, PositionSide.SHORT}

    def _is_hedge_mode(self):
        return self._position_mode == PositionMode.HEDGE

    def _log_dual_hedge_inventory_warning(self, has_dual_hedge_legs: bool):
        if has_dual_hedge_legs and not self._hedge_inventory_warning_active:
            self.logger().warning(
                "Hedge mode has both long and short legs open; using neutral inventory skew."
            )
            self._hedge_inventory_warning_active = True
        elif not has_dual_hedge_legs:
            self._hedge_inventory_warning_active = False


for method_name in (
    "_positions_for_trading_pair",
    "_has_both_hedge_legs",
    "_is_hedge_mode",
    "_log_dual_hedge_inventory_warning",
):
    if method_name in LOADED_METHODS:
        setattr(FakeInventoryStrategy, method_name, LOADED_METHODS[method_name])


class AvellanedaPerpetualMakingHedgeModeTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.strategy = FakeInventoryStrategy()

    def test_calculate_inventory_deviation_returns_zero_when_hedge_has_both_legs(self):
        self.strategy.account_positions = {
            "long": SimpleNamespace(
                trading_pair="XAUT-USDT",
                position_side=PositionSide.LONG,
                amount=Decimal("0.01"),
            ),
            "short": SimpleNamespace(
                trading_pair="XAUT-USDT",
                position_side=PositionSide.SHORT,
                amount=Decimal("-0.01"),
            ),
        }

        deviation = self.strategy.calculate_inventory_deviation()

        self.assertEqual(Decimal("0"), deviation)

    def test_calculate_reservation_price_ignores_position_skew_when_hedge_has_both_legs(self):
        self.strategy.account_positions = {
            "long": SimpleNamespace(
                trading_pair="XAUT-USDT",
                position_side=PositionSide.LONG,
                amount=Decimal("0.02"),
            ),
            "short": SimpleNamespace(
                trading_pair="XAUT-USDT",
                position_side=PositionSide.SHORT,
                amount=Decimal("-0.01"),
            ),
        }

        self.strategy.calculate_reservation_price_and_optimal_spread()

        self.assertEqual(self.strategy.get_price(), self.strategy._reservation_price)
