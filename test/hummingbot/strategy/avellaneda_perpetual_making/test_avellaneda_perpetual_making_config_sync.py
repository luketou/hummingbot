import ast
import unittest
from pathlib import Path


class AvellanedaPerpetualMakingConfigSyncUnitTests(unittest.TestCase):
    def test_config_map_includes_stop_loss_maker_and_exchange_fields(self):
        source_path = (
            Path(__file__).resolve().parents[4]
            / "hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making_config_map_pydantic.py"
        )
        module_ast = ast.parse(source_path.read_text())
        config_class = next(
            node for node in module_ast.body if isinstance(node, ast.ClassDef) and node.name == "AvellanedaPerpetualMakingConfigMap"
        )
        field_names = {
            node.target.id
            for node in config_class.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }

        self.assertTrue(
            {
                "maker_fee_pct",
                "assumed_exit_fee_pct",
                "fee_floor_buffer_pct",
                "enforce_fee_floor",
                "directional_skew_enabled",
                "max_directional_bias",
                "momentum_window_short",
                "momentum_window_long",
                "order_flow_window",
                "funding_rate_bias_enabled",
                "funding_rate_weight",
                "momentum_weight",
                "order_flow_weight",
                "stop_loss_use_maker_orders",
                "stop_loss_maker_timeout",
                "stop_loss_auto_fallback",
                "stop_loss_working_type",
                "exchange_preplaced_stop_loss",
            }.issubset(field_names)
        )

    def test_start_passes_stop_loss_maker_and_exchange_fields_to_init_params(self):
        source_path = (
            Path(__file__).resolve().parents[4]
            / "hummingbot/strategy/avellaneda_perpetual_making/start.py"
        )
        module_ast = ast.parse(source_path.read_text())
        start_function = next(
            node for node in module_ast.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "start"
        )
        init_call = next(
            node.value
            for node in ast.walk(start_function)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "init_params"
        )
        keyword_names = {keyword.arg for keyword in init_call.keywords if keyword.arg is not None}

        self.assertTrue(
            {
                "force_min_spread",
                "maker_fee_pct",
                "assumed_exit_fee_pct",
                "fee_floor_buffer_pct",
                "enforce_fee_floor",
                "directional_skew_enabled",
                "max_directional_bias",
                "momentum_window_short",
                "momentum_window_long",
                "order_flow_window",
                "funding_rate_bias_enabled",
                "funding_rate_weight",
                "momentum_weight",
                "order_flow_weight",
                "stop_loss_use_maker_orders",
                "stop_loss_maker_timeout",
                "stop_loss_auto_fallback",
                "stop_loss_working_type",
                "exchange_preplaced_stop_loss",
            }.issubset(keyword_names)
        )
