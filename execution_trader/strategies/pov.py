"""Percentage-of-volume execution agent implementation."""

from __future__ import annotations

import asyncio
import decimal
from typing import Any

from market_bot_core import AbstractAgent

Decimal = decimal.Decimal
ZERO = Decimal("0")


class PovExecutionAgent(AbstractAgent):
    """Execute a parent order by matching a percentage of observed volume."""

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)

        parent_order = config.get("parent_order", {})
        try:
            total_qty = Decimal(str(parent_order["total_qty"]))
        except (KeyError, decimal.InvalidOperation, TypeError) as exc:
            raise ValueError(
                "parent_order.total_qty must be provided"
            ) from exc

        if total_qty <= 0:
            raise ValueError("parent_order.total_qty must be positive")

        try:
            participation = Decimal(str(config["participation_rate"]))
        except (KeyError, decimal.InvalidOperation, TypeError) as exc:
            raise ValueError("participation_rate must be provided") from exc

        if participation <= ZERO:
            raise ValueError("participation_rate must be positive")

        try:
            interval = float(config["slice_interval_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "slice_interval_seconds must be provided"
            ) from exc

        if interval <= 0:
            raise ValueError("slice_interval_seconds must be positive")

        self.ticker = str(parent_order.get("ticker", "")).upper()
        if not self.ticker:
            raise ValueError("parent_order.ticker must be provided")

        self.side = str(parent_order.get("side", "")).upper()
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("parent_order.side must be BUY or SELL")

        self.total_qty = total_qty
        self.participation_rate = participation
        self.slice_interval_seconds = interval

        self.qty_executed = ZERO
        self.market_volume_this_slice = ZERO
        self.is_complete = False

        self.exec_lock = asyncio.Lock()
        self._ticker_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Start the execution ticker alongside the base event loop."""

        ticker_task = asyncio.create_task(self._run_execution_ticker())
        self._ticker_task = ticker_task

        try:
            await super().run()
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass
            self._ticker_task = None

    async def on_market_data(self, data: dict[str, Any]) -> None:
        """Accumulate observed market volume from public trade messages."""

        if self.is_complete or data.get("type") != "TRADE":
            return

        try:
            traded_qty = Decimal(str(data["quantity"]))
        except (KeyError, decimal.InvalidOperation, TypeError):
            return

        if traded_qty <= ZERO:
            return

        async with self.exec_lock:
            self.market_volume_this_slice += traded_qty

    async def on_private_data(self, data: dict[str, Any]) -> None:
        """Track fills against the parent order."""

        if data.get("type") != "FILL":
            return

        try:
            fill_qty = Decimal(str(data["quantity"]))
        except (KeyError, decimal.InvalidOperation, TypeError):
            return

        if fill_qty <= ZERO:
            return

        async with self.exec_lock:
            self.qty_executed += fill_qty
            progress = self.qty_executed
            print(f"Progress: {progress} / {self.total_qty}")

            if self.qty_executed >= self.total_qty:
                self.is_complete = True
                print("PARENT ORDER COMPLETE.")

    async def _run_execution_ticker(self) -> None:
        """Slice execution into periodic POV child orders."""

        while not self.is_complete:
            await asyncio.sleep(self.slice_interval_seconds)

            async with self.exec_lock:
                if self.is_complete:
                    break

                slice_volume = self.market_volume_this_slice
                self.market_volume_this_slice = ZERO

                remaining_qty = self.total_qty - self.qty_executed
                if remaining_qty <= ZERO:
                    self.is_complete = True
                    break

                my_qty = slice_volume * self.participation_rate
                qty_to_trade = min(my_qty, remaining_qty)

            child_qty = self._normalize_child_qty(qty_to_trade)

            if child_qty < 1:
                continue

            try:
                await self.send_order(
                    ticker=self.ticker,
                    side=self.side,
                    order_type="MARKET",
                    tif="IOC",
                    quantity=child_qty,
                    price=None,
                    trigger_price=None,
                    is_post_only=False,
                    display_quantity=None,
                )
            except Exception as exc:  # pragma: no cover - protective logging
                print(f"Error sending market order: {exc}")

    @staticmethod
    def _normalize_child_qty(requested_qty: Decimal) -> int:
        """Convert a Decimal child quantity into an integer order amount."""

        if requested_qty <= ZERO:
            return 0

        try:
            integral_qty = requested_qty.to_integral_value(
                rounding=decimal.ROUND_FLOOR
            )
        except decimal.InvalidOperation:
            return 0

        quantity = int(integral_qty)
        return quantity if quantity > 0 else 0


__all__ = ["PovExecutionAgent"]
