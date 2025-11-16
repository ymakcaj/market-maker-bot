import asyncio
import decimal
from typing import Any, cast

from mm_bot.strategies.dmm import DesignatedMarketMaker


class StubConnector:
    """Minimal async connector stub for exercising the DMM strategy."""

    def __init__(self) -> None:
        self.sent_orders: list[dict[str, Any]] = []
        self.cancelled_orders: list[str] = []
        self.closed = False

    async def connect(self) -> None:  # pragma: no cover - not used in tests
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
        price: float | None,
        trigger_price: float | None = None,
        is_post_only: bool = False,
        display_quantity: int | None = None,
    ) -> dict[str, Any]:
        order_id = f"{side.lower()}-{len(self.sent_orders) + 1}"
        entry: dict[str, Any] = {
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
        }
        self.sent_orders.append(entry)
        return {"orderId": order_id}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.cancelled_orders.append(order_id)
        return {"orderId": order_id}

    async def get_account_state(self) -> dict[str, Any]:  # pragma: no cover
        return {}

    async def get_open_orders(self) -> list[dict[str, Any]]:
        return []  # pragma: no cover


def _build_strategy() -> DesignatedMarketMaker:
    connector = StubConnector()
    strategy = DesignatedMarketMaker(
        config={
            "ticker": "XYZ",
            "required_qty": "10",
            "max_spread": "1",
            "tick_size": "0.5",
            "inventory_skew_factor": "0.2",
            "target_inventory": "0",
        },
        api_key="dummy",
        api_base_url="http://dummy",
        ws_public_url="ws://dummy",
        ws_private_url="ws://dummy",
        connector=connector,
    )
    return strategy


def test_dmm_quote_lifecycle_and_inventory_updates() -> None:
    async def _run() -> None:
        strategy = _build_strategy()
        connector = cast(StubConnector, strategy.connector)

        initial_book: dict[str, Any] = {
            "ticker": "XYZ",
            "bids": [("100.0", "5")],
            "asks": [("101.0", "5")],
        }

        await strategy.on_market_data(initial_book)
        assert len(connector.sent_orders) == 2
        print("Initial quotes:", connector.sent_orders)

        buy_order_id = connector.sent_orders[0]["order_id"]
        sell_order_id = connector.sent_orders[1]["order_id"]

        await strategy.on_private_data(
            {
                "type": "ORDER_ACK",
                "side": "buy",
                "order_id": buy_order_id,
            }
        )
        await strategy.on_private_data(
            {
                "type": "ORDER_ACK",
                "side": "sell",
                "order_id": sell_order_id,
            }
        )
        print("Open orders after ACKs:", strategy.open_orders)
        assert strategy.open_orders["BUY"] == buy_order_id
        assert strategy.open_orders["SELL"] == sell_order_id

        await strategy.on_private_data(
            {
                "type": "FILL",
                "side": "sell",
                "quantity": "3",
                "order_id": sell_order_id,
                "leaves_qty": 0,
            }
        )
        print("Inventory after fill:", strategy.inventory)
        assert strategy.inventory == decimal.Decimal("-3")

        moved_book: dict[str, Any] = {
            "ticker": "XYZ",
            "bids": [("99.0", "5")],
            "asks": [("100.0", "5")],
        }
        await strategy.on_market_data(moved_book)
        print("Cancelled orders:", connector.cancelled_orders)
        assert connector.cancelled_orders == [buy_order_id]
        assert len(connector.sent_orders) == 4
        new_buy_order_id = connector.sent_orders[2]["order_id"]
        new_sell_order_id = connector.sent_orders[3]["order_id"]
        print("Requoted orders:", connector.sent_orders[2:])
        assert new_buy_order_id != buy_order_id
        assert new_sell_order_id != sell_order_id

        await strategy.connector.close()

    asyncio.run(_run())
