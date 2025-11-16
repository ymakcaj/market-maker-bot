import asyncio
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

                print("Re-quoting condition met. Updating quotes...")

                # --- 3. Cancel All Old Orders ---
                # Simplest, most robust logic: always reset on any change.
                await self._cancel_all_orders()
                self.last_mid_price = mid_price

                # --- 4. Calculate New "Fair" Price (The Skew) ---
                # This is the DMM's risk management.
                inventory_delta = self.inventory - self.target_inventory

                # Skew = (how many shares we're off) * (skew factor)
                # This skew is in $ terms.
                skew = inventory_delta * self.inventory_skew_factor

                # --- 5. Calculate Final Bid/Ask, Respecting "The Contract" ---
                # Start with max_spread and skew from the current mid_price.
                # If inventory is HIGH (long), skew is positive.
                #   -> bid_price = mid - spread/2 - skew (less attractive bid)
                #   -> ask_price = mid + spread/2 - skew (more attractive ask)
                # Buyers now have an incentive to trade against our ask.

                half_spread = self.max_spread / 2

                bid_price = mid_price - half_spread - skew
                ask_price = mid_price + half_spread - skew

                # Round to the nearest tick size
                bid_price = self.round_to_tick(bid_price)
                ask_price = self.round_to_tick(ask_price)

                # --- 6. Send New "Post-Only" Orders ---
                # DMMs are "makers," so we use postOnly to guarantee
                # we don't accidentally take liquidity.

                quantity = int(self.required_qty)

                print(
                    f"Sending new quotes. Bid: {quantity} @ {bid_price}, "
                    f"Ask: {quantity} @ {ask_price}"
                )

                # Send orders. The on_private_data handler updates open_orders.
                await self.send_order(
                    ticker=self.ticker,
                    side="BUY",
                    order_type="LIMIT",
                    tif="GTC",
                    quantity=quantity,
                    price=float(bid_price),
                    is_post_only=True,
                    display_quantity=quantity,
                )
                await self.send_order(
                    ticker=self.ticker,
                    side="SELL",
                    order_type="LIMIT",
                    tif="GTC",
                    quantity=quantity,
                    price=float(ask_price),
                    is_post_only=True,
                    display_quantity=quantity,
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
        return (
            (price / self.tick_size).quantize(
                decimal.Decimal("1."), rounding=decimal.ROUND_HALF_UP
            )
            * self.tick_size
        )


def _extract_order_id(event: dict[str, Any]) -> Optional[str]:
    """Support both snake and camel case order identifiers."""

    return event.get("order_id") or event.get("orderId")
