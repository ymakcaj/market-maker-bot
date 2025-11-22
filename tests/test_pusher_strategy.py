import asyncio
import decimal
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional, cast

from market_maker.strategies.pusher import PusherAgent


class StubConnector:
    """Lightweight connector stub for exercising PusherAgent."""

    def __init__(self) -> None:
        self.sent_orders: list[dict[str, Any]] = []
        self.cancelled_orders: list[str] = []
        self.closed = False

    async def connect(self) -> None:  # pragma: no cover - unused in tests
        return

    async def close(self) -> None:
        self.closed = True

    async def send_order(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        tif: str,
        quantity: int,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        is_post_only: bool = False,
        display_quantity: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        order_id = f"{side.lower()}-{len(self.sent_orders) + 1}"
        payload: dict[str, Any] = {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "order_type": order_type,
            "tif": tif,
            "quantity": quantity,
            "price": price,
            "trigger_price": trigger_price,
            "is_post_only": is_post_only,
            "display_quantity": display_quantity,
            "client_order_id": client_order_id,
        }
        self.sent_orders.append(payload)
        response: dict[str, Any] = {"orderId": order_id}
        if client_order_id is not None:
            response["clientOrderId"] = client_order_id
        return response

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.cancelled_orders.append(order_id)
        return {"orderId": order_id}


def _build_strategy() -> tuple[PusherAgent, StubConnector, Callable[[], None]]:
    temp_dir = tempfile.mkdtemp(prefix="pusher-test-")
    price_path = Path(temp_dir) / "path.json"
    price_path.write_text(
        json.dumps(
            {
                "00:00:01": "100.00",
                "00:00:02": "101.00",
            }
        ),
        encoding="utf-8",
    )

    connector = StubConnector()
    strategy = PusherAgent(
        config={
            "ticker": "XYZ",
            "target_price_path": str(price_path),
            "strategy_interval_seconds": 0.5,
            "price_tolerance": "0.05",
            "aggression_qty": 5,
            "defense_qty": 250,
            "inventory_limit": "50",
            "tick_size": "0.01",
        },
        api_key="dummy",
        api_base_url="http://dummy",
        ws_public_url="ws://dummy",
        ws_private_url="ws://dummy",
        connector=connector,
    )

    def _cleanup() -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return strategy, connector, _cleanup


def test_pusher_updates_mid_price_and_defends() -> None:
    async def _run() -> None:
        strategy, connector, cleanup = _build_strategy()
        try:
            order_id = "legacy-order"
            strategy.open_orders[order_id] = "BUY"
            strategy.current_target_price = decimal.Decimal("101.50")

            book = {
                "ticker": "XYZ",
                "bids": [("101.00", "10")],
                "asks": [("102.00", "12")],
            }
            await strategy.on_market_data(cast(dict[str, Any], book))
            assert strategy.last_mid_price == decimal.Decimal("101.5")

            await strategy._defend_price()  # noqa: SLF001
            assert connector.cancelled_orders == [order_id]
            assert len(connector.sent_orders) == 2

            bid_order, ask_order = connector.sent_orders
            assert bid_order["order_type"] == "LIMIT"
            assert ask_order["order_type"] == "LIMIT"
            expected_bid = float(
                strategy.current_target_price - strategy.tick_size
            )
            expected_ask = float(
                strategy.current_target_price + strategy.tick_size
            )
            assert bid_order["price"] == expected_bid
            assert ask_order["price"] == expected_ask

            assert strategy.open_orders[bid_order["order_id"]] == "BUY"
            assert strategy.open_orders[ask_order["order_id"]] == "SELL"
        finally:
            await strategy.connector.close()
            cleanup()

    asyncio.run(_run())


def test_pusher_push_logic_respects_inventory_bounds() -> None:
    async def _run() -> None:
        strategy, connector, cleanup = _build_strategy()
        try:
            strategy.inventory = decimal.Decimal("0")
            strategy.inventory_limit = decimal.Decimal("10")

            strategy.open_orders["legacy-buy"] = "BUY"
            await strategy._push_price(decimal.Decimal("1"))  # noqa: SLF001
            assert connector.cancelled_orders == ["legacy-buy"]
            assert len(connector.sent_orders) == 1

            push_order = connector.sent_orders[0]
            assert push_order["order_type"] == "MARKET"
            assert push_order["side"] == "BUY"
            assert push_order["quantity"] == 5

            strategy.inventory = strategy.inventory_limit
            await strategy._push_price(decimal.Decimal("1"))  # noqa: SLF001
            assert len(connector.sent_orders) == 1

            strategy.inventory = decimal.Decimal("-5")
            await strategy._push_price(decimal.Decimal("-1"))  # noqa: SLF001
            assert len(connector.sent_orders) == 2
            sell_order = connector.sent_orders[1]
            assert sell_order["side"] == "SELL"
        finally:
            await strategy.connector.close()
            cleanup()

    asyncio.run(_run())
