"""Participation of volume execution strategy placeholder."""

from __future__ import annotations

from typing import Any

from market_bot_core import AbstractAgent


class ParticipationOfVolumeTrader(AbstractAgent):
    """Skeleton strategy to execute orders based on market volume."""

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config

    async def on_market_data(self, data: dict[str, Any]) -> None:
        raise NotImplementedError(
            "ParticipationOfVolumeTrader is a stub; implement strategy logic "
            "in market_maker.strategies.pov"
        )

    async def on_private_data(self, data: dict[str, Any]) -> None:
        raise NotImplementedError(
            "ParticipationOfVolumeTrader is a stub; implement private data "
            "handling in market_maker.strategies.pov"
        )


__all__ = ["ParticipationOfVolumeTrader"]
