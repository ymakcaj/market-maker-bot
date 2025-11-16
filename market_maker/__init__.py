"""Market maker bot package exposing available strategies."""

from .strategies import (
    DesignatedMarketMaker,
    InventoryAwareMarketMaker,
    SimpleMarketMaker,
)

__all__ = [
    "DesignatedMarketMaker",
    "InventoryAwareMarketMaker",
    "SimpleMarketMaker",
]
