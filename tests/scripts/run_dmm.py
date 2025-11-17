import asyncio
import os
from market_maker.strategies.dmm import DesignatedMarketMaker

DMM_CONFIG = {
    "ticker": "TEST",
    "required_qty": "10",
    "max_spread": "1",
    "tick_size": "0.001",
    "inventory_skew_factor": "0.2",
    "target_inventory": "5",
    "bootstrap_mid_price": "100.000",
    "bootstrap_qty": 1,
}

print(os.environ["MATCHER_API_KEY"])

async def main() -> None:
    token = os.environ["MATCHER_API_KEY"]

    print('creating agent..')

    agent = DesignatedMarketMaker(
        config=DMM_CONFIG,
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