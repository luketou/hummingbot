# Avellaneda Perpetual Reduce-Only Guard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `avellaneda_perpetual_making` from submitting duplicate `PositionAction.CLOSE` orders that Binance rejects with `{"code":-2022,"msg":"ReduceOnly Order is rejected."}`.

**Architecture:** Keep the Binance connector unchanged and fix the strategy layer. Add regression tests that reproduce the duplicate-close-order path, then make exit-order detection idempotent by recognizing existing close orders across all active-order tracking surfaces before creating a new take-profit or stop-loss order.

**Tech Stack:** Python, pytest/unittest, Hummingbot strategy order tracking, Binance perpetual connector

---

## File Map

- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
  Responsibility: exit-order tracking, active-order discovery, and position-management sequencing.
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
  Responsibility: regression coverage for duplicate-close-order prevention after entry fills.
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py`
  Responsibility: focused unit coverage for existing-close-order detection and active-order source merging.

## Preconditions

- The worktree is already dirty:
  - `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
  - `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Preserve those edits. Build the fix on top of them rather than reverting them.
- Do not modify `hummingbot/connector/derivative/binance_perpetual/binance_perpetual_derivative.py`; the connector is correctly translating `PositionAction.CLOSE` into `reduceOnly=true`.

## Chunk 1: Lock the Bug With Regression Tests

### Task 1: Prove an existing close order blocks duplicate take-profit creation

**Files:**
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py`
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py:803-903`

- [ ] **Step 1: Write the failing test**

```python
def test_manage_take_profit_order_does_not_submit_duplicate_close_order_when_exchange_already_has_matching_close_order(self):
    position = SimpleNamespace(amount=Decimal("0.011"), entry_price=Decimal("4743.88"))
    existing_close_order = SimpleNamespace(
        client_order_id="close-existing",
        is_buy=False,
        position=PositionAction.CLOSE,
        price=Decimal("4753.36"),
        quantity=Decimal("0.011"),
    )
    self.strategy.exchange_orders = [existing_close_order]

    self.strategy._manage_take_profit_order(position)

    self.assertEqual([], self.strategy.sell_calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k matching_close_order -v`
Expected: FAIL because `_manage_take_profit_order()` still submits a new sell order when tracking sets are empty.

- [ ] **Step 3: Write minimal implementation**

```python
def _has_matching_close_order(self, position: Position, price: Decimal, size: Decimal, role: str) -> bool:
    active_orders = self._active_exit_orders_for_role(position, role)
    return any(
        self._order_price(order) == price and self._order_quantity(order) == size
        for order in active_orders
    )
```

Use this helper from `_manage_take_profit_order()` before calling `market.buy()` or `market.sell()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k matching_close_order -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py
git commit -m "test: cover avellaneda duplicate close order guard"
```

### Task 2: Prove active-order discovery must merge multiple tracking surfaces

**Files:**
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py`
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py:1315-1359`

- [ ] **Step 1: Write the failing test**

```python
def test_get_active_orders_from_exchange_merges_inflight_and_strategy_orders_without_duplicates(self):
    inflight_only = SimpleNamespace(client_order_id="close-a", trading_pair="XAUT-USDT", is_done=False, is_cancelled=False, is_failure=False)
    strategy_only = SimpleNamespace(client_order_id="close-b", trading_pair="XAUT-USDT")
    duplicate = SimpleNamespace(client_order_id="close-a", trading_pair="XAUT-USDT")

    self.strategy.market.in_flight_orders = {"close-a": inflight_only}
    self.strategy.strategy_active_orders = [strategy_only, duplicate]

    orders = self.strategy._get_active_orders_from_exchange()

    self.assertEqual(["close-a", "close-b"], [order.client_order_id for order in orders])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k merges_inflight -v`
Expected: FAIL because `_get_active_orders_from_exchange()` currently returns early from the first populated source.

- [ ] **Step 3: Write minimal implementation**

```python
def _get_active_orders_from_exchange(self):
    orders_by_id = {}
    for order in inflight_orders:
        orders_by_id[order.client_order_id] = order
    for order in strategy_orders:
        orders_by_id.setdefault(order.client_order_id, order)
    for order in limit_orders:
        orders_by_id.setdefault(order.client_order_id, order)
    return list(orders_by_id.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k merges_inflight -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py
git commit -m "fix: merge avellaneda active order tracking sources"
```

## Chunk 2: Fix Strategy Idempotency and Sequencing

### Task 3: Make `_active_exit_orders_for_role()` recognize real close orders even when tracking metadata is stale

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py:803-840`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py`

- [ ] **Step 1: Write the failing test**

```python
def test_active_exit_orders_for_role_includes_untracked_matching_close_order(self):
    position = SimpleNamespace(amount=Decimal("0.011"))
    close_order = SimpleNamespace(
        client_order_id="close-existing",
        is_buy=False,
        position=PositionAction.CLOSE,
    )
    self.strategy.exchange_orders = [close_order]
    self.strategy._take_profit_order_ids.clear()

    active_orders = self.strategy._active_exit_orders_for_role(position, "take_profit")

    self.assertEqual(["close-existing"], [order.client_order_id for order in active_orders])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k untracked_matching_close_order -v`
Expected: FAIL because `_active_exit_orders_for_role()` currently filters by tracked order IDs only.

- [ ] **Step 3: Write minimal implementation**

```python
def _active_exit_orders_for_role(self, position: Position, role: str) -> List[Any]:
    tracked_ids = self._take_profit_order_ids if role == "take_profit" else self._stop_loss_order_ids
    active_orders = []
    for order in self._get_active_orders_from_exchange():
        order_id = getattr(order, "client_order_id", None)
        is_tracked = order_id in tracked_ids
        is_matching_close = self._is_close_order(order) and self._is_close_order_for_position(order, position)
        if is_tracked and self._is_close_order_for_position(order, position):
            active_orders.append(order)
        elif is_matching_close:
            active_orders.append(order)
    return active_orders
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py -k untracked_matching_close_order -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py
git commit -m "fix: recognize existing avellaneda close orders"
```

### Task 4: Preserve the existing entry-fill cancellation guard and extend it to the duplicate-close-order path

**Files:**
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py:823-836,1501-1514`

- [ ] **Step 1: Write the failing test**

```python
def test_did_fill_order_cancels_only_opposite_open_entries_and_keeps_close_orders_intact(self):
    self.strategy.exchange_orders = [
        SimpleNamespace(client_order_id="buy-open", is_buy=True, position=PositionAction.OPEN),
        SimpleNamespace(client_order_id="sell-open", is_buy=False, position=PositionAction.OPEN),
        SimpleNamespace(client_order_id="sell-close", is_buy=False, position=PositionAction.CLOSE),
    ]
    event = SimpleNamespace(trade_type=TradeType.BUY, position=PositionAction.OPEN.value, price=Decimal("4743.88"))

    self.strategy.did_fill_order(event)

    self.assertEqual([(["sell-open"], "Canceling opposite entry order after entry fill")], self.strategy.cancelled_orders)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py -v`
Expected: FAIL if the current implementation cancels the wrong side or includes close orders in the cancellation batch.

- [ ] **Step 3: Write minimal implementation**

```python
if getattr(order_filled_event, "position", None) in {PositionAction.OPEN, PositionAction.OPEN.value}:
    self._cancel_opposite_entry_orders(order_filled_event.trade_type)
```

Keep `_cancel_opposite_entry_orders()` restricted to non-close orders only.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py
git commit -m "fix: cancel opposite avellaneda entry orders after fill"
```

### Task 5: Run focused verification, then broader avellaneda regression checks

**Files:**
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_stale_close_orders.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_error_handling.py`

- [ ] **Step 1: Run the new focused tests**

Run:

```bash
pytest \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py \
  -v
```

Expected: all PASS

- [ ] **Step 2: Run the adjacent regression tests**

Run:

```bash
pytest \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_stale_close_orders.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_error_handling.py \
  -v
```

Expected: all PASS

- [ ] **Step 3: Run one broader strategy suite pass**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making -q`
Expected: PASS, or a short list of pre-existing unrelated failures called out explicitly.

- [ ] **Step 4: Inspect the final diff before claiming completion**

Run:

```bash
git diff -- hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py
git diff -- test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py
git diff -- test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py
```

Expected: only strategy-layer idempotency and test coverage changes

- [ ] **Step 5: Commit**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_exit_order_idempotency.py
git commit -m "fix: prevent duplicate avellaneda reduce-only orders"
```
