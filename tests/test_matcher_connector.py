import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from market_bot_core import MatcherConnector


@contextmanager
def expect_raises(expected_exception: type[BaseException]) -> Iterator[None]:
    try:
        yield
    except expected_exception:
        return
    raise AssertionError(
        f"Expected exception {expected_exception.__name__} to be raised",
    )


class ExposedMatcherConnector(MatcherConnector):
    def build_payload(
        self,
        *,
        ticker: str,
        side: str,
        order_type: str,
        tif: str,
        quantity: int,
        price: float | None,
        trigger_price: float | None,
        is_post_only: bool,
        display_quantity: int | None,
    ) -> dict[str, Any]:
        return self._build_order_payload(
            ticker=ticker,
            side=side,
            order_type=order_type,
            tif=tif,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            is_post_only=is_post_only,
            display_quantity=display_quantity,
        )


def _build_connector() -> ExposedMatcherConnector:
    return ExposedMatcherConnector(
        api_key="test-key",
        api_base_url="http://localhost:7070",
        ws_public_url="ws://localhost:7070/ws/public",
        ws_private_url="ws://localhost:7070/ws/private",
        public_queue=asyncio.Queue(),
        private_queue=asyncio.Queue(),
    )


def test_build_order_payload_maps_enums_and_fields() -> None:
    async def _run() -> None:
        connector = _build_connector()
        try:
            payload = connector.build_payload(
                ticker="TEST",
                side="buy",
                order_type="limit",
                tif="gtc",
                quantity=10,
                price=101.5,
                trigger_price=None,
                is_post_only=True,
                display_quantity=5,
            )

            assert payload["orderType"] == "Limit"
            assert payload["timeInForce"] == "GoodTillCancel"
            assert payload["postOnly"] is True
            assert payload["displayQuantity"] == 5
            assert payload["price"] == 101.5
            assert "triggerPrice" not in payload
        finally:
            await connector.close()

    asyncio.run(_run())


def test_build_order_payload_defaults_display_quantity() -> None:
    async def _run() -> None:
        connector = _build_connector()
        try:
            payload = connector.build_payload(
                ticker="TEST",
                side="sell",
                order_type="market",
                tif="ioc",
                quantity=7,
                price=None,
                trigger_price=None,
                is_post_only=False,
                display_quantity=None,
            )

            assert payload["displayQuantity"] == 7
            assert payload["quantity"] == 7
            assert payload["postOnly"] is False
        finally:
            await connector.close()

    asyncio.run(_run())


def test_build_order_payload_validates_inputs() -> None:
    async def _run() -> None:
        connector = _build_connector()
        try:
            with expect_raises(ValueError):
                connector.build_payload(
                    ticker="TEST",
                    side="buy",
                    order_type="invalid",
                    tif="gtc",
                    quantity=1,
                    price=None,
                    trigger_price=None,
                    is_post_only=False,
                    display_quantity=None,
                )

            with expect_raises(ValueError):
                connector.build_payload(
                    ticker="TEST",
                    side="buy",
                    order_type="limit",
                    tif="goodfornow",
                    quantity=1,
                    price=None,
                    trigger_price=None,
                    is_post_only=False,
                    display_quantity=None,
                )
        finally:
            await connector.close()

    asyncio.run(_run())


if __name__ == "__main__":
    try:
        import pytest
    except ModuleNotFoundError as exc:
        raise SystemExit("pytest is required to run these tests") from exc
    raise SystemExit(pytest.main([__file__]))
