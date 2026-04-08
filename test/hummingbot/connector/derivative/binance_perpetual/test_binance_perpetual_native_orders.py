import ast
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType


def _load_place_order():
    source_path = (
        Path(__file__).resolve().parents[5]
        / "hummingbot/connector/derivative/binance_perpetual/binance_perpetual_derivative.py"
    )
    module_ast = ast.parse(source_path.read_text())
    connector_class = next(
        node for node in module_ast.body if isinstance(node, ast.ClassDef) and node.name == "BinancePerpetualDerivative"
    )
    method = next(
        node for node in connector_class.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_place_order"
    )
    isolated_module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(isolated_module)
    namespace = {
        "CONSTANTS": SimpleNamespace(
            ORDER_URL="v1/order",
            TIME_IN_FORCE_GTC="GTC",
            TIME_IN_FORCE_GTX="GTX",
        ),
        "Decimal": Decimal,
        "OrderType": OrderType,
        "PositionAction": PositionAction,
        "PositionMode": PositionMode,
        "TradeType": TradeType,
        "Tuple": tuple,
        "time": __import__("time"),
    }
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace["_place_order"]


PLACE_ORDER = _load_place_order()


class BinancePerpetualNativeOrdersUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_place_order_accepts_native_stop_order_parameters(self):
        captured = {}

        class FakeConnector:
            position_mode = PositionMode.ONEWAY

            async def exchange_symbol_associated_to_pair(self, trading_pair: str):
                return "XAUTUSDT"

            async def _api_post(self, path_url, data, is_auth_required):
                captured["path_url"] = path_url
                captured["data"] = data
                captured["is_auth_required"] = is_auth_required
                return {"orderId": 12345, "updateTime": 1710000000000}

        connector = FakeConnector()

        await PLACE_ORDER(
            connector,
            order_id="OID-1",
            trading_pair="XAUT-USDT",
            amount=Decimal("0.03"),
            trade_type=TradeType.SELL,
            order_type=OrderType.LIMIT,
            price=Decimal("4600"),
            position_action=PositionAction.CLOSE,
            binance_order_type="STOP",
            stop_price=Decimal("4641.92"),
            working_type="MARK_PRICE",
        )

        self.assertEqual("v1/order", captured["path_url"])
        self.assertTrue(captured["is_auth_required"])
        self.assertEqual("STOP", captured["data"]["type"])
        self.assertEqual("4641.92", captured["data"]["stopPrice"])
        self.assertEqual("4600", captured["data"]["price"])
        self.assertEqual("MARK_PRICE", captured["data"]["workingType"])
        self.assertEqual("true", captured["data"]["reduceOnly"])

