"""Simplified market making strategy placeholder."""

from __future__ import annotations

from typing import Any

from market_bot_core import AbstractAgent


class SimpleMarketMaker(AbstractAgent):
    """Skeleton strategy placing symmetrical quotes around the mid price."""

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config

    async def on_market_data(self, data: dict[str, Any]) -> None:
        raise NotImplementedError(
            "SimpleMarketMaker is a stub; implement strategy logic in"
            " market_maker.strategies.simple_mm"
        )

    async def on_private_data(self, data: dict[str, Any]) -> None:
        raise NotImplementedError(
            "SimpleMarketMaker is a stub; implement private data handling in"
            " market_maker.strategies.simple_mm"
        )


__all__ = ["SimpleMarketMaker"]
