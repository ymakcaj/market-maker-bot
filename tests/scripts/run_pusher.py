import asyncio
import json
import os
import tempfile
from pathlib import Path

from market_maker.strategies.pusher import PusherAgent

PUSHER_CONFIG = {
    "ticker": "TEST",
    "strategy_interval_seconds": 1.5,
    "price_tolerance": "0.05",
    "aggression_qty": 10,
    "defense_qty": 50,
    "inventory_limit": "1000",
    "tick_size": "0.01",
}


def _load_price_path(env_var: str = "TARGET_PRICE_PATH") -> str:
    """Resolve the target price file from env var or create a dummy path."""

    override = os.environ.get(env_var)
    if override:
        return override

    temp_dir = tempfile.mkdtemp(prefix="pusher-path-")
    path = Path(temp_dir) / "target_path.json"
    path.write_text(
        json.dumps(
            {
                "00:00:01": "100.00",
                "00:00:02": "100.50",
                "00:00:03": "101.00",
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def _build_agent(token: str) -> PusherAgent:
    config = dict(PUSHER_CONFIG)
    config["target_price_path"] = _load_price_path()

    return PusherAgent(
        config=config,
        api_key=token,
        api_base_url=os.environ.get("MATCHER_HTTP", "http://localhost:7070"),
        ws_public_url=os.environ.get(
            "MATCHER_WS_PUBLIC",
            "ws://localhost:7070/ws/public",
        ),
        ws_private_url=os.environ.get(
            "MATCHER_WS_PRIVATE",
            f"ws://localhost:7070/ws/private?token={token}",
        ),
    )


async def main() -> None:
    token = os.environ["MATCHER_API_KEY"]
    agent = _build_agent(token)
    try:
        await agent.run()
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
