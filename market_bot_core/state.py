"""Lightweight state container shared by bot implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, cast


@dataclass
class BotState:
    """Dictionary-backed state helper with convenience accessors."""

    data: Dict[str, Any] = field(
        default_factory=lambda: cast(Dict[str, Any], {})
    )

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.data)


__all__ = ["BotState"]
