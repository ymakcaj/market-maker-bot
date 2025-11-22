import asyncio
import contextlib
import decimal
from typing import Any, Optional

from market_bot_core import AbstractAgent
from market_maker.strategies.quote_helpers import QuotingBookMixin

# Use decimal for financial calculations to avoid floating point errors
decimal.getcontext().prec = 8


class DesignatedMarketMaker(QuotingBookMixin, AbstractAgent):

    def __init__(
        self,
        *,
        config: dict[str, Any],
        connector: Optional[Any] = None,
        ticker: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(connector=connector, **kwargs)
        self.config = config
        # Initialize other parameters from config as needed
        resolved_ticker = ticker or config.get("ticker")
        if resolved_ticker is None:
            raise ValueError("ticker must be provided via config or argument")
        self.ticker = str(resolved_ticker)

        bootstrap_mid = config.get("bootstrap_mid_price")
        self.bootstrap_mid_price = (
            decimal.Decimal(str(bootstrap_mid))
            if bootstrap_mid is not None
            else None
        )
        self.bootstrap_quantity = int(
            config.get("bootstrap_quantity", config.get("bootstrap_qty", 1))
        )
        self.max_spread = decimal.Decimal(
            str(config.get("max_spread", "0.02"))
        )
        self.tick_size = decimal.Decimal(
            str(config.get("tick_size", "0.01"))
        )
        self.required_qty = int(config.get("required_qty", 1))
        self.target_inventory = decimal.Decimal(
            str(config.get("target_inventory", "0"))
        )
        self.inventory_skew_factor = decimal.Decimal(
            str(config.get("inventory_skew_factor", "0"))
        )
        self.last_mid_price = decimal.Decimal("0")
        self.inventory = decimal.Decimal("0")
        self.open_orders: dict[str, Optional[str]] = {
            "BUY": None,
            "SELL": None,
        }
        self.quoting_in_progress = False
        self.quote_lock = asyncio.Lock()
        self.current_bid_price: Optional[decimal.Decimal] = None
        self.current_ask_price: Optional[decimal.Decimal] = None

    async def _periodic_reset(self, interval: float) -> None:
        """Periodically re-poll for a market snapshot and refresh quotes."""

        while True:
            await asyncio.sleep(interval)

            if not hasattr(self.connector, "get_market_state"):
                continue

            try:
                state = await self.connector.get_market_state(self.ticker)
            except Exception as exc:  # noqa: BLE001
                print(
                    "Error fetching market state during periodic reset: "
                    f"{exc}"
                )
                continue

            bids = state.get("bids") if state else None
            asks = state.get("asks") if state else None

            best_bid, best_ask = self._best_bid_ask(bids, asks)
            if best_bid is not None and best_ask is not None:
                reference_price: Optional[decimal.Decimal] = (
                    (best_bid + best_ask) / 2
                )
            elif best_bid is not None:
                reference_price = best_bid
            elif best_ask is not None:
                reference_price = best_ask
            else:
                reference_price = (
                    self.bootstrap_mid_price or self.last_mid_price
                )

            async with self.quote_lock:
                if self.quoting_in_progress:
                    continue

                await self._refresh_quotes(
                    reference_price,
                    reason="Periodic reset: refreshing quotes...",
                    bids=bids,
                    asks=asks,
                )

    async def run(self) -> None:
        """Run the agent, optionally seeding the first quotes."""

        bootstrap_task: Optional[asyncio.Task[None]] = None
        if self.bootstrap_mid_price is not None:
            bootstrap_task = asyncio.create_task(
                self._bootstrap_initial_quotes()
            )

        # Start periodic reset task (default 30 seconds).
        reset_interval = float(self.config.get("reset_interval", 30.0))
        periodic_task = asyncio.create_task(
            self._periodic_reset(reset_interval)
        )

        try:
            await super().run()
        finally:
            if bootstrap_task is not None:
                bootstrap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bootstrap_task

            periodic_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await periodic_task

    async def on_private_data(self, data: dict[str, Any]) -> None:
        """Handles fills, ACKs, and CANCELED messages."""
        async with self.quote_lock:
            try:
                data_type = data.get("type")

                if data_type == "ORDER_ACK":
                    side = (data.get("side") or "").upper()
                    order_id = _extract_order_id(data)
                    if side in self.open_orders and order_id:
                        self.open_orders[side] = order_id
                        print(f"ACK received: New {side} order {order_id}")

                elif data_type == "CANCEL_ACK":
                    order_id = _extract_order_id(data)
                    if order_id:
                        for side, oid in self.open_orders.items():
                            if oid == order_id:
                                self.open_orders[side] = None
                                print(
                                    "CANCEL_ACK received: "
                                    f"{side} order {order_id} removed."
                                )
                                break

                elif data_type == "FILL":
                    filled_qty = decimal.Decimal(str(data["quantity"]))
                    side = (data.get("side") or "").upper()
                    order_id = _extract_order_id(data)

                    if side == "BUY":
                        self.inventory += filled_qty
                    elif side == "SELL":
                        self.inventory -= filled_qty

                    print(
                        "FILL received: "
                        f"{side} {filled_qty}. New inventory: {self.inventory}"
                    )

                    # Treat missing leaves_qty as full fill.
                    is_full_fill = data.get("leaves_qty", 0) == 0

                    if is_full_fill:
                        if order_id:
                            for s, oid in self.open_orders.items():
                                if oid == order_id:
                                    self.open_orders[s] = None
                                    print(f"Order {order_id} fully filled.")
                                    break
                            # After a fill, refresh using latest snapshot.
                            if hasattr(self.connector, "get_market_state"):
                                try:
                                    connector = self.connector
                                    state = await connector.get_market_state(
                                        self.ticker
                                    )
                                    print(
                                        "Market snapshot after fill for "
                                        f"{self.ticker}: {state}"
                                    )
                                    bids = state.get("bids")
                                    asks = state.get("asks")
                                    await self._refresh_quotes(
                                        None,
                                        reason="Fill: refreshing quotes.",
                                        bids=bids,
                                        asks=asks,
                                    )
                                except Exception as exc:
                                    print(
                                        "Fill snapshot fetch error: "
                                        f"{exc}"
                                    )

            except (KeyError, TypeError, decimal.InvalidOperation) as exc:
                print(f"Error in on_private_data: {exc}")

    async def on_market_data(self, data: dict[str, Any]) -> None:
        """The core logic loop, triggered by LOB updates."""
        # Prevent this from running multiple times if market data is rapid
        if self.quote_lock.locked() or self.quoting_in_progress:
            return

        async with self.quote_lock:
            try:
                if data.get("ticker") != self.ticker:
                    return  # Not our ticker

                bids = data.get("bids")
                asks = data.get("asks")

                # --- Reference Price Selection ---
                best_bid, best_ask = self._best_bid_ask(bids, asks)
                if best_bid is not None and best_ask is not None:
                    reference_price = (best_bid + best_ask) / 2
                elif best_bid is not None and self.open_orders["SELL"] is None:
                    reference_price = best_bid
                elif best_ask is not None and self.open_orders["BUY"] is None:
                    reference_price = best_ask
                else:
                    reference_price = (
                        self.bootstrap_mid_price or self.last_mid_price
                    )

                mid_price_moved = (
                    abs(reference_price - self.last_mid_price) > self.tick_size
                )
                buy_order_missing = self.open_orders["BUY"] is None
                sell_order_missing = self.open_orders["SELL"] is None

                # If our quotes are present and price hasn't moved, do nothing.
                if not (
                    mid_price_moved or buy_order_missing or sell_order_missing
                ):
                    return

                await self._refresh_quotes(
                    reference_price,
                    reason="Re-quoting condition met. Updating quotes...",
                    bids=bids,
                    asks=asks,
                )

            except (
                KeyError,
                IndexError,
                TypeError,
                decimal.InvalidOperation,
                ValueError,
            ) as exc:
                print(f"Error in on_market_data: {exc}")

    async def _cancel_all_orders(self) -> None:
        """Helper to cancel all currently open orders."""
        print("Cancelling all open orders...")

        # Create a list of order IDs to cancel
        orders_to_cancel = [
            oid for oid in self.open_orders.values() if oid is not None
        ]

        # Reset state immediately. The CANCEL_ACKs will just confirm.
        for side in self.open_orders:
            self.open_orders[side] = None
        self.current_bid_price = None
        self.current_ask_price = None

        cancel_tasks = [self.cancel_order(oid) for oid in orders_to_cancel]
        await asyncio.gather(*cancel_tasks)

    async def _refresh_quotes(
        self,
        reference_price: Optional[decimal.Decimal],
        *,
        reason: str,
        quantity_override: Optional[int] = None,
        bids: Optional[list[Any]] = None,
        asks: Optional[list[Any]] = None,
    ) -> None:
        print(reason)
        self.quoting_in_progress = True
        try:
            await self._cancel_all_orders()

            # --- Reference Price Selection ---
            ref_price = None
            half_spread = self.max_spread / 2

            best_bid, best_ask = self._best_bid_ask(bids, asks)
            # Case 1: Full Book
            if best_bid is not None and best_ask is not None:
                ref_price = (best_bid + best_ask) / 2
            # Case 2: One-Sided Book
            elif best_bid is not None and self.open_orders["SELL"] is None:
                ref_price = best_bid
            elif best_ask is not None and self.open_orders["BUY"] is None:
                ref_price = best_ask
            # Case 3: Empty Book
            elif reference_price is not None:
                ref_price = reference_price
            elif self.last_mid_price != decimal.Decimal("0"):
                ref_price = self.last_mid_price
            else:
                ref_price = self.bootstrap_mid_price or decimal.Decimal("0")

            self.last_mid_price = ref_price

            inventory_delta = self.inventory - self.target_inventory
            skew = inventory_delta * self.inventory_skew_factor

            # Always submit both sides
            bid_price = ref_price - half_spread - skew
            ask_price = ref_price + half_spread - skew

            zero = decimal.Decimal("0")
            if best_ask is not None:
                max_passive_bid = best_ask - self.tick_size
                if bid_price >= best_ask:
                    bid_price = max(max_passive_bid, zero)
            if best_bid is not None:
                min_passive_ask = best_bid + self.tick_size
                if ask_price <= best_bid:
                    ask_price = min_passive_ask

            bid_price = max(bid_price, zero)

            bid_price = self.round_to_tick(bid_price)
            ask_price = self.round_to_tick(ask_price)

            if best_ask is not None and bid_price >= best_ask:
                bid_price = self.round_to_tick(best_ask - self.tick_size)
            bid_price = max(bid_price, zero)
            if best_bid is not None and ask_price <= best_bid:
                ask_price = self.round_to_tick(best_bid + self.tick_size)

            if ask_price <= bid_price:
                ask_price = self.round_to_tick(bid_price + self.tick_size)
            if best_bid is not None and ask_price <= best_bid:
                ask_price = self.round_to_tick(best_bid + self.tick_size)

            base_quantity = (
                quantity_override
                if quantity_override is not None
                else int(self.required_qty)
            )
            quantity = max(base_quantity, 1)

            print(
                f"Sending new quotes. Bid: {quantity} @ {bid_price}, "
                f"Ask: {quantity} @ {ask_price}"
            )

            result_buy = await self.send_order(
                ticker=self.ticker,
                side="BUY",
                order_type="LIMIT",
                tif="GTC",
                quantity=quantity,
                price=float(bid_price),
                is_post_only=True,
                display_quantity=quantity,
            )
            result_sell = await self.send_order(
                ticker=self.ticker,
                side="SELL",
                order_type="LIMIT",
                tif="GTC",
                quantity=quantity,
                price=float(ask_price),
                is_post_only=True,
                display_quantity=quantity,
            )
            print("Bootstrap buy result:", result_buy)
            print("Bootstrap sell result:", result_sell)

            buy_order_id = result_buy.get("orderId")
            sell_order_id = result_sell.get("orderId")
            if buy_order_id:
                self.open_orders["BUY"] = str(buy_order_id)
            if sell_order_id:
                self.open_orders["SELL"] = str(sell_order_id)
            self.current_bid_price = bid_price
            self.current_ask_price = ask_price
        finally:
            self.quoting_in_progress = False

    async def _bootstrap_initial_quotes(self) -> None:
        """Seed the order book with an initial quote if configured."""

        await asyncio.sleep(1)
        if self.bootstrap_mid_price is None:
            return

        async with self.quote_lock:
            if self.open_orders["BUY"] or self.open_orders["SELL"]:
                return

            await self._refresh_quotes(
                self.bootstrap_mid_price,
                reason="Bootstrapping initial quotes...",
                quantity_override=self.bootstrap_quantity,
            )


def _extract_order_id(event: dict[str, Any]) -> Optional[str]:
    """Support both snake and camel case order identifiers."""

    return event.get("order_id") or event.get("orderId")
