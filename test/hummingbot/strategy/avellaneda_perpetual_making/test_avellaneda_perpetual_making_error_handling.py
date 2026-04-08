import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def _load_error_handling_methods():
    source_path = Path(__file__).resolve().parents[4] / "hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py"
    module_ast = ast.parse(source_path.read_text())
    strategy_class = next(
        node for node in module_ast.body if isinstance(node, ast.ClassDef) and node.name == "AvellanedaPerpetualMakingStrategy"
    )
    target_methods = [
        node
        for node in strategy_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_is_stop_order_switch_algo_error", "did_fail_order"}
    ]
    isolated_module = ast.Module(body=target_methods, type_ignores=[])
    ast.fix_missing_locations(isolated_module)

    namespace = {"Any": Any, "OrderFilledEvent": object}
    exec(compile(isolated_module, str(source_path), "exec"), namespace)
    return namespace["_is_stop_order_switch_algo_error"], namespace["did_fail_order"]


IS_STOP_ORDER_SWITCH_ALGO_ERROR, DID_FAIL_ORDER = _load_error_handling_methods()


class FakeAvellanedaPerpetualMakingStrategy:
    _is_stop_order_switch_algo_error = IS_STOP_ORDER_SWITCH_ALGO_ERROR
    did_fail_order = DID_FAIL_ORDER

    OPTION_LOG_STATUS_REPORT = 1 << 5

    def __init__(self):
        self._exchange_preplaced_stop_loss = True
        self._stop_loss_use_maker_orders = True
        self._market_info = SimpleNamespace(market=SimpleNamespace(name="binance_perpetual"))
        self._exit_order_roles = {}
        self._consecutive_error_count = 0
        self._last_error_timestamp = 0.0
        self.current_timestamp = 100.0
        self._error_cooldown_seconds = 60.0
        self._max_consecutive_errors = 3
        self._logging_options = self.OPTION_LOG_STATUS_REPORT
        self._logger = MagicMock()

    def logger(self):
        return self._logger

    def _remove_exit_order_tracking(self, order_id: str):
        self._exit_order_roles.pop(order_id, None)

    def _supports_exchange_preplaced_stop_loss(self):
        return (
            self._exchange_preplaced_stop_loss
            and self._stop_loss_use_maker_orders
            and getattr(self._market_info.market, "name", "") == "binance_perpetual"
        )


class AvellanedaPerpetualMakingErrorHandlingUnitTests(unittest.TestCase):
    def test_did_fail_order_disables_exchange_preplaced_stop_loss_on_binance_4120(self):
        strategy = FakeAvellanedaPerpetualMakingStrategy()
        strategy._exit_order_roles["oid-stop"] = "stop_loss"

        event = SimpleNamespace(
            order_id="oid-stop",
            error_message=(
                "Error executing request POST https://fapi.binance.com/fapi/v1/order. "
                "HTTP status is 400. Error: {\"code\":-4120,\"msg\":"
                "\"Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.\"}"
            ),
        )

        strategy.did_fail_order(event)

        self.assertFalse(strategy._exchange_preplaced_stop_loss)
        self.assertEqual(0, strategy._consecutive_error_count)
        self.assertEqual(0.0, strategy._last_error_timestamp)

    def test_did_fail_order_keeps_default_cooldown_for_other_errors(self):
        strategy = FakeAvellanedaPerpetualMakingStrategy()

        event = SimpleNamespace(
            order_id="oid-normal",
            error_message="Error executing request ... {\"code\":-2019,\"msg\":\"Margin is insufficient.\"}",
        )

        strategy.did_fail_order(event)

        self.assertTrue(strategy._exchange_preplaced_stop_loss)
        self.assertEqual(1, strategy._consecutive_error_count)
        self.assertEqual(100.0, strategy._last_error_timestamp)
