# Avellaneda Stale Close Orders Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale maker exit orders from remaining on the exchange after the position is already closed in `avellaneda_perpetual_making`.

**Architecture:** Add a narrow cleanup path in the no-position branch that inspects active exchange/in-flight orders, cancels any order marked `PositionAction.CLOSE`, and only clears exit-order tracking after no close orders remain. Lock the behavior with focused unit tests first, then implement the minimal strategy changes without changing entry-order behavior.

**Tech Stack:** Python, pytest, Hummingbot strategy framework, Avellaneda perpetual market making strategy

---

## File Structure

- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
  Responsibility: add a helper that identifies stale close orders when there is no position, cancel them, and gate exit-tracking cleanup until those orders are gone.
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py`
  Responsibility: cover the stale-close-order cleanup helper and the no-position control flow.

## Chunk 1: Lock The Regression With Tests

### Task 1: Add failing cleanup tests

**Files:**
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py`

- [ ] **Step 1: Extend the AST-loaded method set for the unit harness**

Add `_is_close_order` and `_cleanup_active_close_orders` to the extracted methods so the test harness can execute the new helper directly.

```python
target_methods = [
    node for node in strategy_class.body
    if isinstance(node, ast.FunctionDef) and node.name in {
        "_create_stop_loss_proposal",
        "_execute_orders_proposal",
        "_is_close_order",
        "_cleanup_active_close_orders",
    }
]
```

- [ ] **Step 2: Add fake strategy helpers needed by the tests**

Provide fake implementations for `_get_active_orders_from_exchange()`, `_cancel_orders()`, and `_clear_exit_order_tracking()` so the tests can observe behavior instead of mocking internal details away.

```python
def _get_active_orders_from_exchange(self):
    return list(self.exchange_orders)

def _cancel_orders(self, orders, reason: str):
    self.cancelled_orders.append(([order.client_order_id for order in orders], reason))

def _clear_exit_order_tracking(self):
    self.clear_exit_order_tracking_called = True
```

- [ ] **Step 3: Write the failing regression test for stale close orders**

Add a test that presents one `PositionAction.CLOSE` order and one `PositionAction.OPEN` order, then asserts only the close order is cancelled and tracking is not cleared yet.

```python
def test_cleanup_active_close_orders_cancels_close_orders_before_clearing_tracking(self):
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
```

- [ ] **Step 4: Write the failing regression test for cleanup completion**

Add a second test that presents only open orders, then asserts no cancel happens and exit tracking is cleared.

```python
def test_cleanup_active_close_orders_clears_tracking_when_no_close_orders_remain(self):
    self.strategy.exchange_orders = [
        SimpleNamespace(client_order_id="open-1", position=PositionAction.OPEN),
    ]

    orders_were_cancelled = self.strategy._cleanup_active_close_orders()

    self.assertFalse(orders_were_cancelled)
    self.assertEqual([], self.strategy.cancelled_orders)
    self.assertTrue(self.strategy.clear_exit_order_tracking_called)
```

- [ ] **Step 5: Run the focused test file and verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py -q`

Expected: FAIL with `AttributeError` or missing-method failures for `_is_close_order` / `_cleanup_active_close_orders`.

- [ ] **Step 6: Commit the red test state**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py
git commit -m "test: lock avellaneda stale close order regression"
```

## Chunk 2: Implement Minimal Strategy Fix

### Task 2: Add close-order cleanup helper to the strategy

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py`

- [ ] **Step 1: Implement `_is_close_order()`**

Add a helper near the existing order-inspection utilities so the strategy can detect exit orders from either in-flight orders or strategy-tracked orders.

```python
def _is_close_order(self, order: Any) -> bool:
    return getattr(order, "position", None) == PositionAction.CLOSE
```

- [ ] **Step 2: Implement `_cleanup_active_close_orders()`**

Add a helper that:
- reads active exchange orders from `_get_active_orders_from_exchange()`
- filters only `PositionAction.CLOSE` orders
- cancels those orders with `_cancel_orders(...)`
- returns `True` if any cancel was sent
- clears exit tracking only when no close orders remain

```python
def _cleanup_active_close_orders(self) -> bool:
    active_close_orders = [
        order for order in self._get_active_orders_from_exchange()
        if self._is_close_order(order)
    ]
    if active_close_orders:
        self._cancel_orders(active_close_orders, "Canceling stale close order after position was closed")
        return True

    self._clear_exit_order_tracking()
    return False
```

- [ ] **Step 3: Run the focused tests and verify they pass**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py -q`

Expected: PASS for the new cleanup tests and existing stop-loss proposal tests.

### Task 3: Integrate cleanup into the no-position branch

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py`

- [ ] **Step 1: Replace eager tracking cleanup in `tick()`**

In the `if not session_positions:` branch, replace the current eager `_clear_exit_order_tracking()` call with `_cleanup_active_close_orders()`.

Current code:

```python
if not session_positions:
    self._clear_exit_order_tracking()
```

Target code:

```python
if not session_positions:
    stale_close_orders_were_cancelled = self._cleanup_active_close_orders()
```

- [ ] **Step 2: Short-circuit the tick if stale close orders were cancelled**

Prevent the strategy from progressing to new entry-order creation on the same tick after issuing stale-close cancels.

```python
if stale_close_orders_were_cancelled:
    self._last_timestamp = timestamp
    return
```

- [ ] **Step 3: Keep the rest of the no-position flow unchanged**

Do not modify:
- `calculate_reservation_price_and_optimal_spread()`
- `create_base_proposal()`
- `cancel_active_orders(proposal)`
- `to_create_orders(proposal)`

This bugfix must only affect stale close-order cleanup, not normal maker entry logic.

- [ ] **Step 4: Re-run the focused strategy tests**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py -q`

Expected: PASS

- [ ] **Step 5: Run adjacent regression tests**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making -q`

Expected: PASS

- [ ] **Step 6: Run one source-level sanity check**

Run: `python -m compileall hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`

Expected: `Compiling ...` with no syntax errors.

- [ ] **Step 7: Commit the implementation**

```bash
git add \
  hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making.py
git commit -m "fix: cancel stale avellaneda close orders after position exit"
```

## Notes For The Implementer

- Use `@superpowers:test-driven-development` discipline even if the scaffolding test edits already exist locally; verify the test fails before depending on it.
- Do not widen cleanup to `cancel_all`; only cancel orders that explicitly carry `PositionAction.CLOSE`.
- Preserve current behavior for active entry maker orders (`PositionAction.OPEN`).
- Preserve current exit-order tracking fields:
  - `_exit_orders`
  - `_exit_order_roles`
  - `_take_profit_order_ids`
  - `_stop_loss_order_ids`
  - `_stop_loss_order_details`

Plan complete and saved to `docs/superpowers/plans/2026-04-08-avellaneda-stale-close-orders.md`. Ready to execute?
