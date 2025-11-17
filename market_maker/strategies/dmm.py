import asyncio
import contextlib
import decimal
from typing import Any, Optional

from market_bot_core import AbstractAgent

# Use decimal for financial calculations to avoid floating point errors
decimal.getcontext().prec = 4


class DesignatedMarketMaker(AbstractAgent):
    """
    A Market Making strategy that is *contractually obligated* to provide
    liquidity, as per the definition of a Designated Market Maker (DMM).

    - It *must* maintain a two-sided quote at all times.
    - It must adhere to a `required_qty` and `max_spread`.
    - It manages risk by *skewing* quotes, not by pulling them.
    """

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # --- Contractual Obligations ---
        self.ticker = config["ticker"]
        self.required_qty = decimal.Decimal(str(config["required_qty"]))
        self.max_spread = decimal.Decimal(str(config["max_spread"]))
        self.tick_size = decimal.Decimal(str(config["tick_size"]))

        # --- Risk & Skew Parameters ---
        self.inventory_skew_factor = decimal.Decimal(
            str(config["inventory_skew_factor"])
        )
        self.target_inventory = decimal.Decimal(
            str(config.get("target_inventory", "0"))
        )

        bootstrap_qty_raw = config.get("bootstrap_qty")
        if bootstrap_qty_raw is not None:
            self.bootstrap_quantity = int(
                decimal.Decimal(str(bootstrap_qty_raw))
            )
            if self.bootstrap_quantity <= 0:
                raise ValueError("bootstrap_qty must be positive")
        else:
            self.bootstrap_quantity = int(self.required_qty)

        bootstrap_mid_raw = config.get("bootstrap_mid_price")
        self.bootstrap_mid_price: Optional[decimal.Decimal]
        if bootstrap_mid_raw is not None:
            mid_price = decimal.Decimal(str(bootstrap_mid_raw))
            if mid_price <= 0:
                raise ValueError("bootstrap_mid_price must be positive")
            self.bootstrap_mid_price = mid_price
        else:
            self.bootstrap_mid_price = None

        # --- Live State ---
        self.inventory = decimal.Decimal("0")
        self.last_mid_price = decimal.Decimal("0")

        # Tracks our open orders. We only want one buy and one sell.
        # Format: {"BUY": "order_id_123", "SELL": "order_id_456"}
        self.open_orders: dict[str, Optional[str]] = {
            "BUY": None,
            "SELL": None,
        }

        # A lock to prevent race conditions during re-quoting
        self.quote_lock = asyncio.Lock()

    async def run(self) -> None:
        """Run the agent, optionally seeding the first quotes."""

        bootstrap_task: Optional[asyncio.Task[None]] = None
        if self.bootstrap_mid_price is not None:
            bootstrap_task = asyncio.create_task(
                self._bootstrap_initial_quotes()
            )

        try:
            await super().run()
        finally:
            if bootstrap_task is not None:
                bootstrap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bootstrap_task

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

                    # Check if our order was fully or partially filled
                    # Assume 'leaves_qty'; otherwise treat this as full fill.
                    is_full_fill = data.get("leaves_qty", 0) == 0

                    if is_full_fill:
                        if order_id:
                            for s, oid in self.open_orders.items():
                                if oid == order_id:
                                    self.open_orders[s] = None
                                    print(f"Order {order_id} fully filled.")
                                    break

            except (KeyError, TypeError, decimal.InvalidOperation) as exc:
                print(f"Error in on_private_data: {exc}")

    async def on_market_data(self, data: dict[str, Any]) -> None:
        """The core logic loop, triggered by LOB updates."""
        # Prevent this from running multiple times if market data is rapid
        if self.quote_lock.locked():
            return

        async with self.quote_lock:
            try:
                # --- 1. Parse Data ---
                if (
                    data.get("ticker") != self.ticker
                    or not data.get("bids")
                    or not data.get("asks")
                ):
                    return  # Not our ticker or empty book

                best_bid = decimal.Decimal(str(data["bids"][0][0]))
                best_ask = decimal.Decimal(str(data["asks"][0][0]))
                mid_price = (best_bid + best_ask) / 2

                # --- 2. Check Re-quoting Conditions ---
                mid_price_moved = (
                    abs(mid_price - self.last_mid_price) > self.tick_size
                )
                buy_order_missing = self.open_orders["BUY"] is None
                sell_order_missing = self.open_orders["SELL"] is None

                # If our quotes are present and price hasn't moved, do nothing.
                if not (
                    mid_price_moved or buy_order_missing or sell_order_missing
                ):
                    return

                await self._refresh_quotes(
                    mid_price,
                    reason="Re-quoting condition met. Updating quotes...",
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
        self.open_orders = {"BUY": None, "SELL": None}

        cancel_tasks = [self.cancel_order(oid) for oid in orders_to_cancel]
        await asyncio.gather(*cancel_tasks)

    def round_to_tick(self, price: decimal.Decimal) -> decimal.Decimal:
        """Rounds a price to the nearest valid tick."""
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")

        with decimal.localcontext() as ctx:
            ctx.prec = max(decimal.getcontext().prec, 28)
            scaled = (price / self.tick_size).quantize(
                decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
            )
            return scaled * self.tick_size

    async def _refresh_quotes(
        self,
        mid_price: decimal.Decimal,
        *,
        reason: str,
        quantity_override: Optional[int] = None,
    ) -> None:
        print(reason)

        await self._cancel_all_orders()
        self.last_mid_price = mid_price

        inventory_delta = self.inventory - self.target_inventory
        skew = inventory_delta * self.inventory_skew_factor

        half_spread = self.max_spread / 2

        bid_price = mid_price - half_spread - skew
        ask_price = mid_price + half_spread - skew

        bid_price = self.round_to_tick(bid_price)
        ask_price = self.round_to_tick(ask_price)

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
