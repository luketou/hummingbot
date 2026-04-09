# Avellaneda Hedge Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `avellaneda_perpetual_making` safe to run in `Hedge` mode by preserving opposite-side entry orders, isolating exit-order tracking per leg, and preventing cross-leg cancellation or netting mistakes.

**Architecture:** Keep the connector unchanged and harden the strategy layer only. Add focused regression tests first, then branch core logic on `PositionMode.HEDGE` so entry-fill handling, exit tracking, sibling cancellation, and inventory skew no longer assume a single net position. Use a conservative Hedge model: preserve legs independently and neutralize inventory skew when both long and short legs exist, rather than pretending the current one-way Avellaneda math is fully hedge-aware.

**Tech Stack:** Python, pytest/unittest, Hummingbot strategy framework, Binance perpetual connector, Avellaneda perpetual market making strategy

---

## File Map

- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
  Responsibility: hedge-mode branching, per-leg exit tracking, sibling cancellation, and conservative inventory handling.
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py`
  Responsibility: focused regression coverage for Hedge-mode entry-fill behavior and inventory neutrality.
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py`
  Responsibility: focused unit coverage for per-leg exit tracking and sibling cancellation.
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
  Responsibility: preserve the existing one-way cancellation guard while proving Hedge skips it.

## Preconditions

- The current `master` worktree is dirty. Create and use a dedicated worktree before implementing this plan.
- Preserve existing local edits in:
  - `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
  - `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Do not modify the Binance connector. The fix belongs in the strategy’s assumptions about position mode and exit state.

## Chunk 1: Lock Hedge Regressions With Tests

### Task 1: Prove Hedge mode must not cancel opposite-side entry orders after an open fill

**Files:**
- Modify: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`

- [ ] **Step 1: Write the failing Hedge test**

```python
def test_did_fill_order_in_hedge_mode_keeps_opposite_open_entry_orders(self):
    self.strategy.position_mode = "Hedge"
    self.strategy.exchange_orders = [
        SimpleNamespace(client_order_id="buy-open", is_buy=True, position=PositionAction.OPEN),
        SimpleNamespace(client_order_id="sell-open", is_buy=False, position=PositionAction.OPEN),
    ]
    event = SimpleNamespace(
        trade_type=TradeType.BUY,
        position=PositionAction.OPEN.value,
        price=Decimal("4743.88"),
    )

    self.strategy.did_fill_order(event)

    self.assertEqual([], self.strategy.cancelled_orders)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py -k hedge_mode_keeps_opposite_open_entry_orders -v`
Expected: FAIL because `did_fill_order()` still calls `_cancel_opposite_entry_orders()` for every `OPEN` fill.

- [ ] **Step 3: Write the one-way control test**

```python
def test_did_fill_order_in_one_way_mode_still_cancels_opposite_open_entries(self):
    self.strategy.position_mode = "One-way"
    self.strategy.exchange_orders = [
        SimpleNamespace(client_order_id="buy-open", is_buy=True, position=PositionAction.OPEN),
        SimpleNamespace(client_order_id="sell-open", is_buy=False, position=PositionAction.OPEN),
    ]
    event = SimpleNamespace(
        trade_type=TradeType.BUY,
        position=PositionAction.OPEN.value,
        price=Decimal("4743.88"),
    )

    self.strategy.did_fill_order(event)

    self.assertEqual(
        [(["sell-open"], "Canceling opposite entry order after entry fill")],
        self.strategy.cancelled_orders,
    )
```

- [ ] **Step 4: Run the focused file**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py -v`
Expected: FAIL only on the new Hedge test.

- [ ] **Step 5: Commit the red test state**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py
git commit -m "test: lock hedge entry fill cancellation behavior"
```

### Task 2: Prove exit tracking must be isolated by leg

**Files:**
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py`
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`

- [ ] **Step 1: Write the failing sibling-cancel test**

```python
def test_cancel_sibling_exit_orders_only_cancels_same_leg_sibling(self):
    self.strategy.exchange_orders = [
        SimpleNamespace(client_order_id="long-sl", position=PositionAction.CLOSE),
        SimpleNamespace(client_order_id="short-sl", position=PositionAction.CLOSE),
    ]
    self.strategy.exit_order_roles = {
        "long-tp": ("take_profit", PositionSide.LONG),
        "long-sl": ("stop_loss", PositionSide.LONG),
        "short-sl": ("stop_loss", PositionSide.SHORT),
    }

    self.strategy._cancel_sibling_exit_orders("long-tp")

    self.assertEqual(
        [(["long-sl"], "Canceling sibling exit order")],
        self.strategy.cancelled_orders,
    )
```

- [ ] **Step 2: Write the failing tracking test**

```python
def test_track_exit_order_stores_long_and_short_orders_separately(self):
    self.strategy._track_exit_order("long-tp", "take_profit", position_side=PositionSide.LONG)
    self.strategy._track_exit_order("short-tp", "take_profit", position_side=PositionSide.SHORT)

    self.assertEqual({"long-tp"}, self.strategy.take_profit_order_ids[PositionSide.LONG])
    self.assertEqual({"short-tp"}, self.strategy.take_profit_order_ids[PositionSide.SHORT])
```

- [ ] **Step 3: Run the new test file to verify both fail**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py -v`
Expected: FAIL because exit-order state is currently global and sibling cancellation is not leg-aware.

- [ ] **Step 4: Commit the red test state**

```bash
git add test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py
git commit -m "test: lock hedge exit tracking isolation"
```

## Chunk 2: Make Entry Handling and Exit Tracking Hedge-Safe

### Task 3: Branch entry-fill cancellation on position mode

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`

- [ ] **Step 1: Implement a position-mode helper**

```python
def _is_hedge_mode(self) -> bool:
    return self._position_mode == PositionMode.HEDGE
```

- [ ] **Step 2: Guard `did_fill_order()`**

```python
if (
    getattr(order_filled_event, "position", None) in {PositionAction.OPEN, PositionAction.OPEN.value}
    and not self._is_hedge_mode()
):
    self._cancel_opposite_entry_orders(order_filled_event.trade_type)
```

- [ ] **Step 3: Re-run the entry-fill tests**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py
git commit -m "fix: preserve opposite entry orders in hedge mode"
```

### Task 4: Replace global exit-order state with per-leg state

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py`

- [ ] **Step 1: Introduce a leg key helper**

```python
def _position_side_for_order(self, order: Any) -> Optional[PositionSide]:
    side = getattr(order, "position_side", None)
    if side in {PositionSide.LONG, PositionSide.SHORT}:
        return side
    return None
```

- [ ] **Step 2: Convert exit-order collections to per-leg dictionaries**

```python
self._take_profit_order_ids = {
    PositionSide.LONG: set(),
    PositionSide.SHORT: set(),
}
self._stop_loss_order_ids = {
    PositionSide.LONG: set(),
    PositionSide.SHORT: set(),
}
```

Keep `_exit_order_roles` keyed by `order_id`, but store tuples like `(role, position_side)`.

- [ ] **Step 3: Update tracking helpers**

Implement the minimal signature changes:

```python
def _track_exit_order(self, order_id: str, role: str, position_side: PositionSide, trigger_price=None):
    ...

def _remove_exit_order_tracking(self, order_id: str):
    ...

def _clear_exit_order_tracking(self):
    ...
```

Use the stored `position_side` to remove from the correct leg bucket.

- [ ] **Step 4: Update sibling cancellation**

```python
role, position_side = self._exit_order_roles[completed_order_id]
sibling_ids = (
    self._take_profit_order_ids[position_side]
    if role == "stop_loss"
    else self._stop_loss_order_ids[position_side]
)
```

- [ ] **Step 5: Re-run the hedge exit tracking tests**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py
git commit -m "fix: separate avellaneda exit tracking by hedge leg"
```

## Chunk 3: Make Hedge Inventory Handling Conservative

### Task 5: Neutralize inventory skew when both hedge legs exist

**Files:**
- Modify: `hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
- Create: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py`

- [ ] **Step 1: Write the failing inventory test**

```python
def test_calculate_inventory_deviation_returns_zero_when_hedge_has_both_legs(self):
    self.strategy.position_mode = "Hedge"
    self.strategy.account_positions = {
        "long": SimpleNamespace(trading_pair="XAUT-USDT", position_side=PositionSide.LONG, amount=Decimal("0.01")),
        "short": SimpleNamespace(trading_pair="XAUT-USDT", position_side=PositionSide.SHORT, amount=Decimal("-0.01")),
    }

    deviation = self.strategy.calculate_inventory_deviation()

    self.assertEqual(Decimal("0"), deviation)
```

- [ ] **Step 2: Write the failing reservation-price test**

```python
def test_calculate_reservation_price_ignores_position_skew_when_hedge_has_both_legs(self):
    self.strategy.position_mode = "Hedge"
    self.strategy.account_positions = {
        "long": SimpleNamespace(trading_pair="XAUT-USDT", position_side=PositionSide.LONG, amount=Decimal("0.01")),
        "short": SimpleNamespace(trading_pair="XAUT-USDT", position_side=PositionSide.SHORT, amount=Decimal("-0.01")),
    }

    self.strategy.calculate_reservation_price_and_optimal_spread()

    self.assertEqual(self.strategy.get_price(), self.strategy._reservation_price)
```

- [ ] **Step 3: Run the new file to verify it fails**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py -v`
Expected: FAIL because the current code nets long and short into one inventory calculation.

- [ ] **Step 4: Implement the conservative Hedge path**

Add helpers:

```python
def _positions_for_trading_pair(self) -> List[Position]:
    ...

def _has_both_hedge_legs(self, positions: List[Position]) -> bool:
    ...
```

Then implement:

```python
if self._is_hedge_mode() and self._has_both_hedge_legs(positions):
    return s_decimal_zero
```

Use the same condition when computing `q` in `calculate_reservation_price_and_optimal_spread()`.

- [ ] **Step 5: Add one runtime warning**

```python
self.logger().warning(
    "Hedge mode has both long and short legs open; using neutral inventory skew."
)
```

Log only once per continuous dual-leg episode.

- [ ] **Step 6: Re-run the hedge-mode file**

Run: `pytest test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py
git commit -m "fix: neutralize avellaneda inventory skew in hedge mode"
```

## Chunk 4: Final Verification

### Task 6: Run the focused Hedge safety suite and confirm one-way behavior still works

**Files:**
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py`
- Test: `test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_stale_close_orders.py`

- [ ] **Step 1: Run the focused Hedge safety tests**

Run:

```bash
pytest \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_stale_close_orders.py -q
```

Expected: PASS

- [ ] **Step 2: Run the existing Docker-focused harness**

Run: `make docker-avellaneda-tests`
Expected: PASS

- [ ] **Step 3: Run a source-level sanity check**

Run: `python -m compileall hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py`
Expected: `Compiling ...` with no syntax errors.

- [ ] **Step 4: Review final diff scope**

Run:

```bash
git diff --stat
git diff -- hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py
```

Expected: only Hedge safety hardening and focused test coverage changes.

- [ ] **Step 5: Commit the final verification checkpoint**

```bash
git add hummingbot/strategy/avellaneda_perpetual_making/avellaneda_perpetual_making.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_reduce_only_guard.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_exit_tracking.py \
  test/hummingbot/strategy/avellaneda_perpetual_making/test_avellaneda_perpetual_making_hedge_mode.py
git commit -m "fix: harden avellaneda hedge mode safety"
```

## Notes for the Implementer

- Use `@superpowers:test-driven-development` discipline for every task: write the failing test first and watch it fail for the right reason.
- Preserve `One-way` behavior unless the task explicitly changes `Hedge` branching.
- Do not attempt a full dual-leg Avellaneda re-quote redesign in this plan. This plan is only for safety hardening.
- Do not modify connector code or Binance API translation.
- If you discover that a required fix needs side-specific exchange metadata not present in current order objects, stop and write down the blocker instead of guessing.

Plan complete and saved to `docs/superpowers/plans/2026-04-09-avellaneda-hedge-safety-hardening.md`. Ready to execute?
