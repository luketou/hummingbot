import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

from hummingbot.core.data_type.common import PositionAction, TradeType


def _load_strategy_methods():
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
        and node.name in {"_cancel_opposite_entry_orders", "did_fill_order"}
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Any": Any,
        "List": List,
        "OrderFilledEvent": object,
        "PositionAction": PositionAction,
        "TradeType": TradeType,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace["_cancel_opposite_entry_orders"], namespace["did_fill_order"]


CANCEL_OPPOSITE_ENTRY_ORDERS, DID_FILL_ORDER = _load_strategy_methods()


class FakeAvellanedaPerpetualMakingStrategy:
    _cancel_opposite_entry_orders = CANCEL_OPPOSITE_ENTRY_ORDERS
    did_fill_order = DID_FILL_ORDER

    def __init__(self):
        self.current_timestamp = 100.0
        self._filled_order_delay = 15.0
        self._cancel_timestamp = 0.0
        self._create_timestamp = 0.0
        self._last_own_trade_price = 0
        self.exchange_orders = []
        self.cancelled_orders = []

    def _get_active_orders_from_exchange(self):
        return list(self.exchange_orders)

    def _cancel_orders(self, orders, reason: str):
        self.cancelled_orders.append(
            ([order.client_order_id for order in orders], reason)
        )


class AvellanedaPerpetualMakingReduceOnlyGuardTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.strategy = FakeAvellanedaPerpetualMakingStrategy()

    def test_did_fill_order_cancels_opposite_open_entries_after_open_sell_fill(self):
        self.strategy.exchange_orders = [
            SimpleNamespace(
                client_order_id="buy-open",
                is_buy=True,
                position=PositionAction.OPEN,
            ),
            SimpleNamespace(
                client_order_id="sell-open",
                is_buy=False,
                position=PositionAction.OPEN,
            ),
            SimpleNamespace(
                client_order_id="buy-close",
                is_buy=True,
                position=PositionAction.CLOSE,
            ),
        ]

        event = SimpleNamespace(
            trade_type=TradeType.SELL,
            position=PositionAction.OPEN.value,
            price=4763.91,
        )

        self.strategy.did_fill_order(event)

        self.assertEqual(
            [(["buy-open"], "Canceling opposite entry order after entry fill")],
            self.strategy.cancelled_orders,
        )
        self.assertEqual(115.0, self.strategy._create_timestamp)

    def test_did_fill_order_does_not_cancel_entries_for_close_fill(self):
        self.strategy.exchange_orders = [
            SimpleNamespace(
                client_order_id="buy-open",
                is_buy=True,
                position=PositionAction.OPEN,
            ),
        ]

        event = SimpleNamespace(
            trade_type=TradeType.BUY,
            position=PositionAction.CLOSE.value,
            price=4763.91,
        )

        self.strategy.did_fill_order(event)

        self.assertEqual([], self.strategy.cancelled_orders)
        self.assertEqual(115.0, self.strategy._create_timestamp)
