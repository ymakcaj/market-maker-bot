"""Reusable mixins for quoting agents."""

from __future__ import annotations

import decimal
from typing import Any, Mapping, Optional, Sequence, Tuple, cast


class QuotingBookMixin:
    """Provide order book utilities for quoting strategies."""

    tick_size: decimal.Decimal  # Implementations define a positive tick size.

    def round_to_tick(self, price: decimal.Decimal) -> decimal.Decimal:
        """Round a price to the nearest valid tick size."""
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")

        with decimal.localcontext() as ctx:
            ctx.prec = max(decimal.getcontext().prec, 28)
            scaled = (price / self.tick_size).quantize(
                decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP
            )
            return scaled * self.tick_size

    def _parse_price(self, level: Any) -> Optional[decimal.Decimal]:
        """Extract a Decimal price from heterogeneous level structures."""
        if level is None:
            return None

        price_candidate: Any
        if isinstance(level, (list, tuple)):
            level_seq = cast(Sequence[Any], level)
            price_candidate = level_seq[0] if level_seq else None
        elif isinstance(level, Mapping):
            level_mapping = cast(Mapping[str, Any], level)
            price_candidate = level_mapping.get("price")
        else:
            price_candidate = level

        if price_candidate is None:
            return None

        try:
            return decimal.Decimal(str(price_candidate))
        except (ValueError, decimal.InvalidOperation):
            return None

    def _best_bid_ask(
        self,
        bids: Optional[Sequence[Any]],
        asks: Optional[Sequence[Any]],
    ) -> Tuple[Optional[decimal.Decimal], Optional[decimal.Decimal]]:
        """Return best bid/ask prices from heterogeneous book data."""

        best_bid = self._parse_price(bids[0]) if bids else None
        best_ask = self._parse_price(asks[0]) if asks else None
        return best_bid, best_ask
