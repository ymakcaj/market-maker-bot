"""Async connector for communicating with the matcher service."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import aiohttp
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.legacy.client import connect



_ORDER_TYPE_MAP = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "STOP_LIMIT": "STOP_LIMIT",
    "STOP": "STOP_MARKET",
}

_TIF_MAP = {
    "GTC": "GTC",
    "DAY": "DAY",
    "IOC": "IOC",
    "FOK": "FOK",
    # "GTD": "GTD",  # Not present in Java enum, remove or leave unmapped
}



class MatcherConnector:
    """Handle REST and websocket communication with the matcher service."""

    def __init__(
        self,
        api_key: str,
        api_base_url: str,
        ws_public_url: str,
        ws_private_url: str,
        public_queue: asyncio.Queue[dict[str, Any]],
        private_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.ws_public_url = ws_public_url
        self.ws_private_url = ws_private_url
        self.public_queue = public_queue
        self.private_queue = private_queue
        self._listener_tasks: list[asyncio.Task[None]] = []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession = aiohttp.ClientSession(
            base_url=self.api_base_url,
            headers=headers,
        )

    async def get_market_state(self, ticker: str) -> dict[str, Any]:
        """
        Fetch the current market state (order book snapshot) for the given symbol.
        Adjust the endpoint and response parsing to match your matcher API.
        """
        path = f"/api/market/{ticker}/book"
        try:
            state = await self._request("GET", path)
            return state  # Should match the format expected by on_market_data
        except Exception as exc:
            print(f"Error fetching market state for {ticker}: {exc}")
            return {}

    async def connect(self) -> None:
        """Start both websocket listeners and keep them alive."""

        if self._listener_tasks:
            raise RuntimeError("MatcherConnector already connected.")

        self._listener_tasks = [
            asyncio.create_task(
                self._listen_public(),
                name="matcher-public-ws",
            ),
            asyncio.create_task(
                self._listen_private(),
                name="matcher-private-ws",
            ),
        ]

        try:
            await asyncio.gather(*self._listener_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._listener_tasks.clear()

    async def close(self) -> None:
        """Close the underlying HTTP session."""

        for task in self._listener_tasks:
            task.cancel()

        if self._listener_tasks:
            await asyncio.gather(*self._listener_tasks, return_exceptions=True)
            self._listener_tasks.clear()

        if not self._session.closed:
            await self._session.close()

    async def _listen_public(self) -> None:
        """Consume public websocket messages and dispatch them via queue."""

        while True:
            try:
                async with connect(self.ws_public_url) as ws:
                    async for message in ws:
                        data = json.loads(message)
                        await self.public_queue.put(data)
            except asyncio.CancelledError:
                return
            except ConnectionClosed as exc:
                print(f"Public feed closed: {exc}")
            except (WebSocketException, json.JSONDecodeError) as exc:
                print(f"Public feed error: {exc}")
            except OSError as exc:
                print(f"Public feed transport error: {exc}")

            await asyncio.sleep(5)

    async def _listen_private(self) -> None:
        """Consume private websocket messages and dispatch them via queue."""

        while True:
            try:
                url = self._private_ws_url_with_token()
                async with connect(url) as ws:
                    async for message in ws:
                        data = json.loads(message)
                        await self.private_queue.put(data)
            except asyncio.CancelledError:
                return
            except ConnectionClosed as exc:
                print(f"Private feed closed: {exc}")
            except (WebSocketException, json.JSONDecodeError) as exc:
                print(f"Private feed error: {exc}")
            except OSError as exc:
                print(f"Private feed transport error: {exc}")

            await asyncio.sleep(5)

    def _private_ws_url_with_token(self) -> str:
        """Append the API token query parameter if it is missing."""

        if "token=" in self.ws_private_url:
            return self.ws_private_url

        separator = "&" if "?" in self.ws_private_url else "?"
        return f"{self.ws_private_url}{separator}token={self.api_key}"

    async def send_order(
        self,
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
        """Submit an order to the matcher and return the response payload."""

        payload = self._build_order_payload(
            ticker=ticker,
            side=side,
            order_type=order_type,
            tif=tif,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            is_post_only=is_post_only,
            display_quantity=display_quantity,
            client_order_id=client_order_id,
        )

        return await self._request("POST", "/api/order", json=payload)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an order by ID."""

        path = f"/api/order/{order_id}"
        return await self._request("DELETE", path)

    async def get_account_state(self) -> dict[str, Any]:
        """Retrieve the authenticated account snapshot."""

        return await self._request("GET", "/api/account")

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Retrieve the list of open orders."""

        response = await self._request("GET", "/api/orders")
        return list(response)

    def _build_order_payload(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        tif: str,
        quantity: int,
        price: Optional[float],
        trigger_price: Optional[float],
        is_post_only: bool,
        display_quantity: Optional[int],
        client_order_id: Optional[str],
    ) -> dict[str, Any]:
        """Translate client-friendly arguments into matcher payload."""

        normalized_type = order_type.upper()
        if normalized_type not in _ORDER_TYPE_MAP:
            raise ValueError(f"Unsupported order type: {order_type}")

        normalized_tif = tif.upper()
        if normalized_tif not in _TIF_MAP:
            raise ValueError(f"Unsupported TIF: {tif}")

        payload: dict[str, Any] = {
            "ticker": ticker,
            "side": side.upper(),
            "orderType": _ORDER_TYPE_MAP[normalized_type],
            "timeInForce": _TIF_MAP[normalized_tif],
            "quantity": quantity,
            "postOnly": bool(is_post_only),
        }

        if client_order_id is not None:
            payload["orderId"] = client_order_id

        eff_display = (
            display_quantity if display_quantity is not None else quantity
        )
        payload["displayQuantity"] = eff_display

        if price is not None:
            payload["price"] = price
        if trigger_price is not None:
            payload["triggerPrice"] = trigger_price

        return payload

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """Execute an HTTP request against the matcher REST API."""

        session = self._ensure_session()
        async with session.request(method, path, **kwargs) as response:
            response.raise_for_status()
            if response.content_type == "application/json":
                return await response.json()
            return await response.text()

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Recreate the client session if it was closed externally."""

        if self._session.closed:
            headers = self._session.headers.copy()
            self._session = aiohttp.ClientSession(
                base_url=self.api_base_url,
                headers=headers,
            )
        return self._session
