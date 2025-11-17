"""Core abstractions for asynchronous trading agents."""

from __future__ import annotations

import abc
import asyncio
import uuid
from typing import Any, Optional

from market_bot_core.matcher import MatcherConnector


class AbstractAgent(abc.ABC):
    """Reusable scaffold for building asynchronous trading agents."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base_url: str,
        ws_public_url: str,
        ws_private_url: str,
        connector: Optional[MatcherConnector] = None,
    ) -> None:
        self.public_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.private_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        self.connector = connector or MatcherConnector(
            api_key=api_key,
            api_base_url=api_base_url,
            ws_public_url=ws_public_url,
            ws_private_url=ws_private_url,
            public_queue=self.public_queue,
            private_queue=self.private_queue,
        )

        self._consumer_tasks: list[asyncio.Task[None]] = []
        self._connector_task: Optional[asyncio.Task[None]] = None
        self._run_lock = asyncio.Lock()
        self._client_order_counter = 0
        self._client_order_prefix = (
            f"{self.__class__.__name__.lower()}-"
            f"{uuid.uuid4().hex[:8]}"
        )

    async def run(self) -> None:
        print("Starting agent...")
        """Start the connector and forward queue events to callbacks."""

        async with self._run_lock:
            if self._connector_task is not None:
                raise RuntimeError("Agent already running")

            self._consumer_tasks = [
                asyncio.create_task(
                    self._consume_public_feed(),
                    name="agent-public-consumer",
                ),
                asyncio.create_task(
                    self._consume_private_feed(),
                    name="agent-private-consumer",
                ),
            ]
            print('Starting connector...')
            self._connector_task = asyncio.create_task(
                self.connector.connect(),
                name="agent-matcher-connector",
            )

            print(self._connector_task.__str__())

        try:
            await asyncio.gather(self._connector_task, *self._consumer_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        """Cancel background tasks and close the underlying connector."""

        tasks: list[asyncio.Task[None]] = []
        if self._connector_task is not None:
            self._connector_task.cancel()
            tasks.append(self._connector_task)
            self._connector_task = None

        if self._consumer_tasks:
            for task in self._consumer_tasks:
                task.cancel()
            tasks.extend(self._consumer_tasks)
            self._consumer_tasks = []

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await self.connector.close()

    @abc.abstractmethod
    async def on_market_data(self, data: dict[str, Any]) -> None:
        """Handle public market data messages."""

    @abc.abstractmethod
    async def on_private_data(self, data: dict[str, Any]) -> None:
        """Handle private account updates."""

    async def send_order(
        self,
        ticker: str,
        side: str,
        order_type: str,
        tif: str,
        quantity: int,
        *,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        is_post_only: bool = False,
        display_quantity: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Forward order submission to the connector."""

        order_token = client_order_id or self._generate_client_order_id()

        return await self.connector.send_order(
            ticker=ticker,
            side=side,
            order_type=order_type,
            tif=tif,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            is_post_only=is_post_only,
            display_quantity=display_quantity,
            client_order_id=order_token,
        )

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Forward cancel requests to the connector."""

        return await self.connector.cancel_order(order_id)

    def _generate_client_order_id(self) -> str:
        """Generate a unique client order identifier for tracking."""

        self._client_order_counter += 1
        return f"{self._client_order_prefix}-{self._client_order_counter:06d}"

    async def get_account_state(self) -> dict[str, Any]:
        """Retrieve the current account snapshot via the connector."""

        return await self.connector.get_account_state()

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Retrieve open orders via the connector."""

        return await self.connector.get_open_orders()

    async def _consume_public_feed(self) -> None:
        while True:
            try:
                message = await self.public_queue.get()
                await self.on_market_data(message)
            except asyncio.CancelledError:
                return
            finally:
                self.public_queue.task_done()

    async def _consume_private_feed(self) -> None:
        while True:
            try:
                message = await self.private_queue.get()
                await self.on_private_data(message)
            except asyncio.CancelledError:
                return
            finally:
                self.private_queue.task_done()
