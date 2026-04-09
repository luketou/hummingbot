import ast
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

from hummingbot.core.data_type.common import PositionSide


def _load_exit_tracking_methods():
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
    target_methods = [
        node
        for node in strategy_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_track_exit_order", "_cancel_sibling_exit_orders"}
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Any": Any,
        "Decimal": Decimal,
        "Dict": Dict,
        "Optional": Optional,
        "PositionSide": PositionSide,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace["_track_exit_order"], namespace["_cancel_sibling_exit_orders"]


TRACK_EXIT_ORDER, CANCEL_SIBLING_EXIT_ORDERS = _load_exit_tracking_methods()


class LegAwareOrderIds(dict):
    def __init__(self):
        super().__init__(
            {
                PositionSide.LONG: set(),
                PositionSide.SHORT: set(),
            }
        )
        self._global_ids = set()

    def add(self, order_id: str):
        self._global_ids.add(order_id)

    def discard(self, order_id: str):
        self._global_ids.discard(order_id)
        for order_ids in self.values():
            order_ids.discard(order_id)

    def clear(self):
        self._global_ids.clear()
        for order_ids in self.values():
            order_ids.clear()

    def seed(self, position_side: PositionSide, order_id: str):
        self[position_side].add(order_id)
        self._global_ids.add(order_id)

    def __contains__(self, order_id: object) -> bool:
        return order_id in self._global_ids


class FakeExitTrackingStrategy:
    _track_exit_order = TRACK_EXIT_ORDER
    _cancel_sibling_exit_orders = CANCEL_SIBLING_EXIT_ORDERS

    def __init__(self):
        self.current_timestamp = 100.0
        self.exchange_orders = []
        self.cancelled_orders = []
        self._exit_orders = {}
        self._exit_order_roles = {}
        self._take_profit_order_ids = LegAwareOrderIds()
        self._stop_loss_order_ids = LegAwareOrderIds()
        self._stop_loss_order_details = {}

    @property
    def exit_order_roles(self):
        return self._exit_order_roles

    @property
    def take_profit_order_ids(self):
        return self._take_profit_order_ids

    def _get_active_orders_from_exchange(self):
        return list(self.exchange_orders)

    def _cancel_orders(self, orders, reason: str):
        self.cancelled_orders.append(
            ([order.client_order_id for order in orders], reason)
        )


class AvellanedaPerpetualMakingHedgeExitTrackingTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.strategy = FakeExitTrackingStrategy()

    def test_cancel_sibling_exit_orders_only_cancels_same_leg_sibling(self):
        self.strategy.exchange_orders = [
            SimpleNamespace(client_order_id="long-sl"),
            SimpleNamespace(client_order_id="short-sl"),
        ]
        self.strategy.exit_order_roles["long-tp"] = ("take_profit", PositionSide.LONG)
        self.strategy._stop_loss_order_ids.seed(PositionSide.LONG, "long-sl")
        self.strategy._stop_loss_order_ids.seed(PositionSide.SHORT, "short-sl")

        self.strategy._cancel_sibling_exit_orders("long-tp")

        self.assertEqual(
            [(["long-sl"], "Canceling sibling exit order")],
            self.strategy.cancelled_orders,
        )

    def test_track_exit_order_stores_long_and_short_orders_separately(self):
        self.strategy._track_exit_order(
            "long-tp",
            "take_profit",
            PositionSide.LONG,
        )
        self.strategy._track_exit_order(
            "short-tp",
            "take_profit",
            PositionSide.SHORT,
        )

        self.assertEqual(
            {"long-tp"},
            self.strategy.take_profit_order_ids[PositionSide.LONG],
        )
        self.assertEqual(
            {"short-tp"},
            self.strategy.take_profit_order_ids[PositionSide.SHORT],
        )
