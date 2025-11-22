import asyncio
from decimal import Decimal
from typing import Any, cast

from execution_trader.strategies.pov import PovExecutionAgent

class StubConnector:
    """Minimal async connector stub for exercising the POV strategy."""
    def __init__(self) -> None:
        self.sent_orders: list[dict[str, Any]] = []
        self.closed = False

    async def connect(self) -> None:
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
        client_order_id: str | None = None,
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
            "client_order_id": client_order_id,
        }
        self.sent_orders.append(entry)
        return {"orderId": order_id}

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"orderId": order_id}

    async def get_account_state(self) -> dict[str, Any]:
        return {}

    async def get_open_orders(self) -> list[dict[str, Any]]:
        return []

def _build_strategy() -> PovExecutionAgent:
    connector = StubConnector()
    strategy = PovExecutionAgent(
        config={
            "parent_order": {
                "ticker": "XYZ",
                "side": "BUY",
                "total_qty": "100",
            },
            "participation_rate": "0.5",
            "slice_interval_seconds": 0.1,
        },
        api_key="dummy",
        api_base_url="http://dummy",
        ws_public_url="ws://dummy",
        ws_private_url="ws://dummy",
        connector=connector,
    )
    return strategy

def test_pov_executes_child_orders() -> None:
    async def _run() -> None:
        strategy = _build_strategy()
        connector = cast(StubConnector, strategy.connector)

        # Simulate market trades for two slices
        trade_msgs = [
            {"type": "TRADE", "quantity": "40"},
            {"type": "TRADE", "quantity": "60"},
        ]
        # Simulate fills for the parent order
        fill_msgs = [
            {"type": "FILL", "quantity": "20"},
            {"type": "FILL", "quantity": "30"},
        ]

        # Start the strategy run loop in the background
        run_task = asyncio.create_task(strategy.run())
        await asyncio.sleep(0.05)  # Let the ticker start

        # Feed market data and private data
        for msg in trade_msgs:
            await strategy.on_market_data(msg)
        await asyncio.sleep(0.15)  # Allow slice to trigger
        for msg in fill_msgs:
            await strategy.on_private_data(msg)
        await asyncio.sleep(0.15)  # Allow next slice

        # Stop the strategy
        await strategy.connector.close()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

        print("Sent orders:", connector.sent_orders)
        print("Qty executed:", strategy.qty_executed)
        assert len(connector.sent_orders) >= 1
        assert strategy.qty_executed == Decimal("50")

    asyncio.run(_run())
