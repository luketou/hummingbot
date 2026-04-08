import ast
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

from hummingbot.connector.derivative.position import Position
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionSide
from hummingbot.strategy.data_types import PriceSize, Proposal


def _load_strategy_methods():
    source_path = Path(__file__).resolve().parents[4] / "hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py"
    module_ast = ast.parse(source_path.read_text())
    strategy_class = next(
        node for node in module_ast.body if isinstance(node, ast.ClassDef) and node.name == "AvellanedaPerpetualMakingStrategy"
    )
    target_methods = [
        node for node in strategy_class.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_create_stop_loss_proposal",
            "_execute_orders_proposal",
            "_is_close_order",
            "_cleanup_active_close_orders",
        }
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Any": Any,
        "Decimal": Decimal,
        "DerivativeBase": object,
        "List": List,
        "OrderType": OrderType,
        "Optional": Optional,
        "Position": Position,
        "PositionAction": PositionAction,
        "PriceSize": PriceSize,
        "Proposal": Proposal,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return (
        namespace["_create_stop_loss_proposal"],
        namespace["_execute_orders_proposal"],
        namespace["_is_close_order"],
        namespace["_cleanup_active_close_orders"],
    )


(
    CREATE_STOP_LOSS_PROPOSAL,
    EXECUTE_ORDERS_PROPOSAL,
    IS_CLOSE_ORDER,
    CLEANUP_ACTIVE_CLOSE_ORDERS,
) = _load_strategy_methods()


class FakeAvellanedaPerpetualMakingStrategy:
    _create_stop_loss_proposal = CREATE_STOP_LOSS_PROPOSAL
    _execute_orders_proposal = EXECUTE_ORDERS_PROPOSAL
    _is_close_order = IS_CLOSE_ORDER
    _cleanup_active_close_orders = CLEANUP_ACTIVE_CLOSE_ORDERS

    def __init__(self, market, trading_pair: str):
        self._market_info = SimpleNamespace(market=market, trading_pair=trading_pair)
        self._stop_loss_spread = Decimal("0.10")
        self._stop_loss_slippage_buffer = Decimal("0.005")
        self._exit_orders = {}
        self.current_timestamp = 1234.0
        self._order_refresh_time = 30.0
        self._create_timestamp = 0.0
        self.tracked_exit_orders = []
        self.exchange_orders = []
        self.cancelled_orders = []
        self.clear_exit_order_tracking_called = False

    def _track_exit_order(self, order_id: str, role: str, trigger_price=None):
        self.tracked_exit_orders.append((order_id, role, trigger_price))

    def _get_active_orders_from_exchange(self):
        return list(self.exchange_orders)

    def _cancel_orders(self, orders, reason: str):
        self.cancelled_orders.append(([order.client_order_id for order in orders], reason))

    def _clear_exit_order_tracking(self):
        self.clear_exit_order_tracking_called = True


class AvellanedaPerpetualMakingStrategyUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.trading_pair = "XAUT-USDT"
        self.market = MagicMock()
        self.market.get_price.side_effect = lambda trading_pair, is_buy: Decimal("95") if is_buy else Decimal("89")
        self.market.quantize_order_price.side_effect = lambda trading_pair, price: price
        self.market.quantize_order_amount.side_effect = lambda trading_pair, amount: amount
        self.market.sell.return_value = "sell-order-id"

        self.strategy = FakeAvellanedaPerpetualMakingStrategy(self.market, self.trading_pair)

    def test_create_stop_loss_proposal_for_long_position_uses_buffered_limit_price(self):
        position = Position(
            trading_pair=self.trading_pair,
            position_side=PositionSide.LONG,
            unrealized_pnl=Decimal("-11"),
            entry_price=Decimal("100"),
            amount=Decimal("1"),
            leverage=Decimal("5"),
        )

        proposal = self.strategy._create_stop_loss_proposal([position])

        self.assertEqual(0, len(proposal.buys))
        self.assertEqual(1, len(proposal.sells))
        self.assertEqual(Decimal("89.55"), proposal.sells[0].price)
        self.assertEqual(Decimal("1"), proposal.sells[0].size)

    def test_execute_orders_proposal_for_close_uses_limit_order(self):
        proposal = Proposal(buys=[], sells=[PriceSize(Decimal("89.55"), Decimal("1"))])

        self.strategy._execute_orders_proposal(proposal, PositionAction.CLOSE)

        self.market.sell.assert_called_once_with(
            trading_pair=self.trading_pair,
            amount=Decimal("1"),
            order_type=OrderType.LIMIT,
            price=Decimal("89.55"),
            position_action=PositionAction.CLOSE,
        )

    def test_cleanup_active_close_orders_cancels_close_orders_before_clearing_tracking(self):
        self.strategy.exchange_orders = [
            SimpleNamespace(client_order_id="close-1", position=PositionAction.CLOSE),
            SimpleNamespace(client_order_id="open-1", position=PositionAction.OPEN),
        ]

        orders_were_cancelled = self.strategy._cleanup_active_close_orders()

        self.assertTrue(orders_were_cancelled)
        self.assertEqual([(["close-1"], "Canceling stale close order after position was closed")], self.strategy.cancelled_orders)
        self.assertFalse(self.strategy.clear_exit_order_tracking_called)

    def test_cleanup_active_close_orders_clears_tracking_when_no_close_orders_remain(self):
        self.strategy.exchange_orders = [
            SimpleNamespace(client_order_id="open-1", position=PositionAction.OPEN),
        ]

        orders_were_cancelled = self.strategy._cleanup_active_close_orders()

        self.assertFalse(orders_were_cancelled)
        self.assertEqual([], self.strategy.cancelled_orders)
        self.assertTrue(self.strategy.clear_exit_order_tracking_called)
