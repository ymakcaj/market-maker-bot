import asyncio
import os
from execution_trader.strategies.pov import PovExecutionAgent

POV_CONFIG = {
    "parent_order": {
        "ticker": "TEST",
        "side": "BUY",
        "total_qty": "100",
    },
    "participation_rate": "1",
    "slice_interval_seconds": 1.5,
}

async def main() -> None:
    # token = os.environ["MATCHER_API_KEY"]
    token = 'beta-test-token'

    agent = PovExecutionAgent(
        config=POV_CONFIG,
        api_key=token,
        api_base_url=os.environ.get("MATCHER_HTTP", "http://localhost:7070"),
        ws_public_url=os.environ.get("MATCHER_WS_PUBLIC", "ws://localhost:7070/ws/public"),
        ws_private_url=os.environ.get(
            "MATCHER_WS_PRIVATE",
            f"ws://localhost:7070/ws/private?token={token}"
        ),
    )
    try:
        await agent.run()
    finally:
        await agent.close()

if __name__ == "__main__":
    asyncio.run(main())
