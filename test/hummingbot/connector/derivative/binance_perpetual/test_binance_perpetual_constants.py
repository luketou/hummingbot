import ast
import unittest
from pathlib import Path


class BinancePerpetualConstantsUnitTests(unittest.TestCase):
    def test_order_state_maps_expired_in_match_to_failed(self):
        source_path = (
            Path(__file__).resolve().parents[5]
            / "hummingbot/connector/derivative/binance_perpetual/binance_perpetual_constants.py"
        )
        module_ast = ast.parse(source_path.read_text())
        order_state_assign = next(
            node
            for node in module_ast.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "ORDER_STATE" for target in node.targets)
        )

        order_state_entries = {}
        for key_node, value_node in zip(order_state_assign.value.keys, order_state_assign.value.values):
            if isinstance(key_node, ast.Constant) and isinstance(value_node, ast.Attribute):
                order_state_entries[key_node.value] = value_node.attr

        self.assertEqual("FAILED", order_state_entries["EXPIRED_IN_MATCH"])
