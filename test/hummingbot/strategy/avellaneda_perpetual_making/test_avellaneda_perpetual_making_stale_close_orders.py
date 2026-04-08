import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

from hummingbot.core.data_type.common import PositionAction


def _load_cleanup_methods():
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
        and node.name in {"_is_close_order", "_cleanup_active_close_orders"}
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Any": Any,
        "List": List,
        "PositionAction": PositionAction,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace["_is_close_order"], namespace["_cleanup_active_close_orders"]


IS_CLOSE_ORDER, CLEANUP_ACTIVE_CLOSE_ORDERS = _load_cleanup_methods()


class FakeCleanupStrategy:
    _is_close_order = IS_CLOSE_ORDER
    _cleanup_active_close_orders = CLEANUP_ACTIVE_CLOSE_ORDERS

    def __init__(self):
        self._exit_orders = {}
        self.exchange_orders = []
        self.cancelled_orders = []
        self.clear_exit_order_tracking_called = False

    def _get_active_orders_from_exchange(self):
        return list(self.exchange_orders)

    def _cancel_orders(self, orders, reason: str):
        self.cancelled_orders.append(
            ([order.client_order_id for order in orders], reason)
        )

    def _clear_exit_order_tracking(self):
        self.clear_exit_order_tracking_called = True


class AvellanedaPerpetualMakingStaleCloseOrderTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.strategy = FakeCleanupStrategy()

    def test_cleanup_active_close_orders_cancels_close_orders_before_clearing_tracking(
        self,
    ):
        self.strategy.exchange_orders = [
            SimpleNamespace(client_order_id="close-1", position=PositionAction.CLOSE),
            SimpleNamespace(client_order_id="open-1", position=PositionAction.OPEN),
        ]

        orders_were_cancelled = self.strategy._cleanup_active_close_orders()

        self.assertTrue(orders_were_cancelled)
        self.assertEqual(
            [(["close-1"], "Canceling stale close order after position was closed")],
            self.strategy.cancelled_orders,
        )
        self.assertFalse(self.strategy.clear_exit_order_tracking_called)

    def test_cleanup_active_close_orders_clears_tracking_when_no_close_orders_remain(
        self,
    ):
        self.strategy.exchange_orders = [
            SimpleNamespace(client_order_id="open-1", position=PositionAction.OPEN),
        ]

        orders_were_cancelled = self.strategy._cleanup_active_close_orders()

        self.assertFalse(orders_were_cancelled)
        self.assertEqual([], self.strategy.cancelled_orders)
        self.assertTrue(self.strategy.clear_exit_order_tracking_called)
