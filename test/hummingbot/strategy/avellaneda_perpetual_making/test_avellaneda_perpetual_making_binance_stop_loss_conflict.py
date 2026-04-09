import ast
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionSide, TradeType


def _load_stop_loss_methods():
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
        and node.name in {
            "_active_exit_orders_for_role",
            "_is_buy_order",
            "_is_close_order_for_position",
            "_manage_exchange_stop_loss",
            "_order_price",
            "_order_quantity",
            "_track_exit_order",
        }
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {
        "Any": Any,
        "Decimal": Decimal,
        "DerivativeBase": object,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "OrderType": OrderType,
        "Position": object,
        "PositionAction": PositionAction,
        "PositionSide": PositionSide,
        "TradeType": TradeType,
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return (
        namespace["_active_exit_orders_for_role"],
        namespace["_is_buy_order"],
        namespace["_is_close_order_for_position"],
        namespace["_manage_exchange_stop_loss"],
        namespace["_order_price"],
        namespace["_order_quantity"],
        namespace["_track_exit_order"],
    )


(
    ACTIVE_EXIT_ORDERS_FOR_ROLE,
    IS_BUY_ORDER,
    IS_CLOSE_ORDER_FOR_POSITION,
    MANAGE_EXCHANGE_STOP_LOSS,
    ORDER_PRICE,
    ORDER_QUANTITY,
    TRACK_EXIT_ORDER,
) = _load_stop_loss_methods()


class FakeBinancePerpetualMarket:
    def __init__(self, created_orders):
        self.created_orders = created_orders

    def quantize_order_price(self, trading_pair: str, price: Decimal) -> Decimal:
        return price

    def quantize_order_amount(self, trading_pair: str, amount: Decimal) -> Decimal:
        return amount

    def buy(self, **kwargs):
        self.created_orders.append(("buy", kwargs))
        return "created-buy-stop"

    def sell(self, **kwargs):
        self.created_orders.append(("sell", kwargs))
        return "created-sell-stop"


class FakeBinanceStopLossStrategy:
    _active_exit_orders_for_role = ACTIVE_EXIT_ORDERS_FOR_ROLE
    _is_buy_order = IS_BUY_ORDER
    _is_close_order_for_position = IS_CLOSE_ORDER_FOR_POSITION
    _manage_exchange_stop_loss = MANAGE_EXCHANGE_STOP_LOSS
    _order_price = ORDER_PRICE
    _order_quantity = ORDER_QUANTITY
    _track_exit_order = TRACK_EXIT_ORDER

    def __init__(self):
        self.current_timestamp = 100.0
        self.cancelled_orders = []
        self.created_orders = []
        self.exchange_orders = []
        self._exit_orders = {}
        self._exit_order_roles = {}
        self._take_profit_order_ids = {
            PositionSide.LONG: set(),
            PositionSide.SHORT: set(),
        }
        self._stop_loss_order_ids = {
            PositionSide.LONG: set(),
            PositionSide.SHORT: set(),
        }
        self._stop_loss_order_details = {}
        self._stop_loss_spread = Decimal("0.01")
        self._stop_loss_working_type = "MARK_PRICE"
        self._market_info = SimpleNamespace(
            market=FakeBinancePerpetualMarket(self.created_orders),
            trading_pair="ETH-USDT",
        )

    def _get_active_orders_from_exchange(self):
        return list(self.exchange_orders)

    def _cancel_orders(self, orders, reason: str):
        self.cancelled_orders.append(
            ([order.client_order_id for order in orders], reason)
        )


class AvellanedaPerpetualMakingBinanceStopLossConflictTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.strategy = FakeBinanceStopLossStrategy()

    def test_manage_exchange_stop_loss_cancels_same_leg_take_profit_before_creating_stop(
        self,
    ):
        position = SimpleNamespace(
            amount=Decimal("0.03"),
            entry_price=Decimal("4700"),
            position_side=PositionSide.LONG,
        )
        self.strategy.exchange_orders = [
            SimpleNamespace(
                client_order_id="tp-long",
                is_buy=False,
                price=Decimal("4710"),
                quantity=Decimal("0.03"),
            ),
        ]
        self.strategy._take_profit_order_ids[PositionSide.LONG].add("tp-long")

        self.strategy._manage_exchange_stop_loss(position)

        self.assertEqual(
            [
                (
                    ["tp-long"],
                    "Canceling conflicting take profit order before stop loss placement",
                )
            ],
            self.strategy.cancelled_orders,
        )
        self.assertEqual([], self.strategy.created_orders)

    def test_manage_exchange_stop_loss_ignores_other_leg_take_profit_orders(self):
        position = SimpleNamespace(
            amount=Decimal("-0.03"),
            entry_price=Decimal("4700"),
            position_side=PositionSide.SHORT,
        )
        self.strategy.exchange_orders = [
            SimpleNamespace(
                client_order_id="tp-long",
                is_buy=False,
                price=Decimal("4710"),
                quantity=Decimal("0.03"),
            ),
        ]
        self.strategy._take_profit_order_ids[PositionSide.LONG].add("tp-long")

        self.strategy._manage_exchange_stop_loss(position)

        expected_stop_price = position.entry_price * (
            Decimal("1") + self.strategy._stop_loss_spread
        )
        self.assertEqual([], self.strategy.cancelled_orders)
        self.assertEqual(
            [
                (
                    "buy",
                    {
                        "trading_pair": "ETH-USDT",
                        "amount": Decimal("0.03"),
                        "order_type": OrderType.LIMIT,
                        "price": expected_stop_price,
                        "position_action": PositionAction.CLOSE,
                        "binance_order_type": "STOP",
                        "stop_price": expected_stop_price,
                        "working_type": "MARK_PRICE",
                    },
                )
            ],
            self.strategy.created_orders,
        )
