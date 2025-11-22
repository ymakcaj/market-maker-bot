"""Aggressive price-pushing agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, Mapping, Optional, Sequence, cast

import decimal

from market_bot_core import AbstractAgent


class PusherAgent(AbstractAgent):
    """Force the market toward a scripted price path."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        connector: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(connector=connector, **kwargs)

        self.config = config
        self.ticker = config["ticker"]
        self.strategy_interval_seconds = float(
            config.get("strategy_interval_seconds", 0.5)
        )
        self.price_tolerance = decimal.Decimal(
            str(config.get("price_tolerance", "0.05"))
        )
        self.aggression_qty = int(config.get("aggression_qty", 1))
        self.defense_qty = int(config.get("defense_qty", 1_000))
        self.inventory_limit = decimal.Decimal(
            str(config.get("inventory_limit", "100"))
        )
        self.tick_size = decimal.Decimal(str(config.get("tick_size", "0.01")))

        path_file = config["target_price_path"]
        with open(path_file, "r", encoding="utf-8") as handle:
            raw_path = json.load(handle)
        self.price_path: dict[str, decimal.Decimal] = {
            timestamp: decimal.Decimal(str(price))
            for timestamp, price in raw_path.items()
        }

        self.last_mid_price = decimal.Decimal("0")
        self.current_target_price = decimal.Decimal("0")
        self.inventory = decimal.Decimal("0")
        self.open_orders: dict[str, str] = {}
        self._strategy_task: Optional[asyncio.Task[None]] = None
        self.lock = asyncio.Lock()

    async def run(self) -> None:
        self._strategy_task = asyncio.create_task(self._run_strategy_ticker())
        try:
            await super().run()
        finally:
            if self._strategy_task is not None:
                self._strategy_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._strategy_task

    async def _run_strategy_ticker(self) -> None:
        last_known_target = self.current_target_price
        while True:
            await asyncio.sleep(self.strategy_interval_seconds)
            timestamp = time.strftime("%H:%M:%S")
            target = self.price_path.get(timestamp, last_known_target)
            last_known_target = target
            self.current_target_price = target

            async with self.lock:
                delta = self.current_target_price - self.last_mid_price
                if abs(delta) > self.price_tolerance:
                    await self._push_price(delta)
                else:
                    await self._defend_price()

    async def on_market_data(self, data: dict[str, Any]) -> None:
        if data.get("ticker") != self.ticker:
            return

        bids = cast(Optional[Sequence[Any]], data.get("bids"))
        asks = cast(Optional[Sequence[Any]], data.get("asks"))
        best_bid = self._extract_price(bids[0]) if bids else None
        best_ask = self._extract_price(asks[0]) if asks else None

        if best_bid is not None and best_ask is not None:
            self.last_mid_price = (best_bid + best_ask) / 2
        elif best_bid is not None:
            self.last_mid_price = best_bid
        elif best_ask is not None:
            self.last_mid_price = best_ask

    async def on_private_data(self, data: dict[str, Any]) -> None:
        data_type = data.get("type")
        side = (data.get("side") or "").upper()
        order_id = data.get("order_id") or data.get("orderId")

        if data_type == "ORDER_ACK" and order_id:
            self.open_orders[str(order_id)] = side
        elif data_type == "CANCEL_ACK" and order_id:
            self.open_orders.pop(str(order_id), None)
        elif data_type == "FILL":
            filled_qty = decimal.Decimal(str(data.get("quantity", "0")))
            if side == "BUY":
                self.inventory += filled_qty
            elif side == "SELL":
                self.inventory -= filled_qty

            if order_id:
                self.open_orders.pop(str(order_id), None)

    async def _push_price(self, delta: decimal.Decimal) -> None:
        await self._cancel_all_orders()

        if delta > 0 and self.inventory < self.inventory_limit:
            print(f"PUSHING UP: Sending MARKET BUY {self.aggression_qty}")
            await self.send_order(
                ticker=self.ticker,
                side="BUY",
                order_type="MARKET",
                tif="IOC",
                quantity=self.aggression_qty,
            )
        elif delta < 0 and self.inventory > -self.inventory_limit:
            print(f"PUSHING DOWN: Sending MARKET SELL {self.aggression_qty}")
            await self.send_order(
                ticker=self.ticker,
                side="SELL",
                order_type="MARKET",
                tif="IOC",
                quantity=self.aggression_qty,
            )

    async def _defend_price(self) -> None:
        await self._cancel_all_orders()

        bid_price = self.current_target_price - self.tick_size
        ask_price = self.current_target_price + self.tick_size
        print(f"DEFENDING: Pinning price at {self.current_target_price}")

        buy = await self.send_order(
            ticker=self.ticker,
            side="BUY",
            order_type="LIMIT",
            tif="GTC",
            quantity=self.defense_qty,
            price=float(bid_price),
            is_post_only=True,
            display_quantity=self.defense_qty,
        )
        sell = await self.send_order(
            ticker=self.ticker,
            side="SELL",
            order_type="LIMIT",
            tif="GTC",
            quantity=self.defense_qty,
            price=float(ask_price),
            is_post_only=True,
            display_quantity=self.defense_qty,
        )

        if buy_id := buy.get("orderId"):
            self.open_orders[str(buy_id)] = "BUY"
        if sell_id := sell.get("orderId"):
            self.open_orders[str(sell_id)] = "SELL"

    async def _cancel_all_orders(self) -> None:
        cancel_tasks = [
            self.cancel_order(order_id)
            for order_id in list(self.open_orders)
        ]
        self.open_orders.clear()
        if cancel_tasks:
            await asyncio.gather(*cancel_tasks)

    @staticmethod
    def _extract_price(level: Any) -> Optional[decimal.Decimal]:
        if level is None:
            return None
        if isinstance(level, (list, tuple)):
            level_seq = cast(Sequence[Any], level)
            candidate = level_seq[0] if level_seq else None
        elif isinstance(level, Mapping):
            level_mapping = cast(Mapping[str, Any], level)
            candidate = level_mapping.get("price")
        else:
            candidate = level

        if candidate is None:
            return None

        try:
            return decimal.Decimal(str(candidate))
        except (ValueError, decimal.InvalidOperation):
            return None
