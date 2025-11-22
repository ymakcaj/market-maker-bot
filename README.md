# Market Maker Bot

## Overview
- Async trading agent framework that connects to the matcher through `market_bot_core`.
- Strategies live under `market_maker/strategies` and implement `AbstractAgent` hooks for market and private data events.
- Tests in `tests/` validate connector payloads and strategy behaviour via pytest.

## Maker/Taker Microstructure
- Prices are organised as a limit order book where *makers* post resting liquidity (limit orders) and earn rebates or priority, while *takers* consume liquidity (marketable orders) and usually pay fees.
- Makers quote both sides of the book to tighten spreads, accepting inventory risk in exchange for rebates and spread capture.
- Takers demand immediacy; they execute against the best available quotes and create price impact proportional to their size relative to displayed depth.
- The bot focuses on maker behaviour: posting passive orders, managing inventory, and only cancelling when re-quoting rather than reacting as a taker.

## Live Strategies

### Market Maker Strategies
- **Designated Market Maker (DMM)**
	- Contract obligations: always maintain a two sided quote with size `required_qty` and maximum spread `max_spread` for `ticker`.
	- Applies an inventory skew so that positions migrate toward `target_inventory` rather than pulling quotes.
	- Bootstraps quotes if `bootstrap_mid_price` is configured, ensuring the book is seeded even before external market data arrives.
	- Quote construction:
		- Mid price: `m = (best_bid + best_ask) / 2`
		- Inventory delta: `ΔI = inventory - target_inventory`
		- Skew term: `skew = ΔI * inventory_skew_factor`
		- Half spread cap: `h = max_spread / 2`
		- Rounded quotes: `bid = round_to_tick(m - h - skew)` and `ask = round_to_tick(m + h - skew)`
	- Order size: `max(required_qty, 1)` unless a bootstrap quantity overrides it.
	- Cancels resting quotes before refreshing, then submits post-only GTC limit orders.
- **Simple Market Maker**: placeholder; no executable logic yet.
- **Inventory Aware Market Maker**: placeholder; inventory control logic not implemented.

### Execution Trader Strategies
- **Execution trader XO**: planned; will accept customer orders, fill at `mid + spread`, and seek price improvement for the internal inventory leg.
- **POV Execution Agent**: live; executes a parent order by matching a percentage of observed market volume over time.
    - Configuration: `parent_order` (with `ticker`, `side`, `total_qty`), `participation_rate`, and `slice_interval_seconds`.
    - Logic: On each interval, accumulates public trade volume, then submits a child order sized as `min(participation_rate × observed_volume, remaining_qty)`.
    - Formula:
        - Child order quantity: $q_{child} = \min(\text{participation\_rate} \times \text{market\_volume}, \text{total\_qty} - \text{qty\_executed})$
    - Sends IOC market orders for each slice until the parent order is complete.

## TODO
- Execution trader XO (improved)
	- Implement the planned Execution trader XO that accepts customer parent orders and performs a controlled child-slicing algorithm to fill them at `mid + spread` while seeking price improvement for the internal inventory leg.
	- Design notes:
		- Support parent order config: `ticker`, `side`, `total_qty`, `start_time`, `end_time`, and `aggressiveness`.
		- The XO should schedule child slices, try to post passive limit slices around mid ± spread, and optionally escalate to marketable IOC slices if required by time/volume constraints.
	- Acceptance criteria:
		- Clear config API and documented behavior.
		- Unit tests demonstrating child scheduling, limit-first posting, and fallback to IOC when no fills are available within `slice_timeout_seconds`.

- PusherAgent: random target-path generation
	- Enhance the existing `PusherAgent` so it can optionally synthesize a stochastic target-price path instead of reading a static JSON file. The generator should accept an input price (market mid or a seed), and parameters for distributional shape (standard deviation, skew, kurtosis) and sample at 1s resolution. The agent should support both deterministic replay (seeded RNG) and fully random draws. Acceptance criteria:
		- New config flags: `randomize_path` (bool), `random_seed` (optional), `path_generation` (dict with `stddev`, `skew`, `kurtosis`, `length_seconds`).
		- When `randomize_path` is true, the agent constructs `self.price_path` at startup and behaves the same as when a JSON path is supplied.

- POV agent: mixed market + limit slice executor
	- Replace the current POV execution (uses IOC market orders) with a hybrid executor that submits limit child orders at sensible price levels and falls back to immediate market-taking only if the limit slices fail to post within configured time. This prevents accidental eating through the top-of-book and gives better control of slippage.
	- Design notes:
		- Child slice should first post a post-only limit at `aggressiveness` ticks inside the mid (or `best_bid`/`best_ask` depending on side).
		- If the slice doesn't execute within `slice_timeout_seconds`, convert the remainder to a marketable IOC slice sized up to `max_take_pct` of the original slice.
	- Acceptance criteria:
		- Config options: `slice_timeout_seconds`, `max_take_pct`, `aggressiveness_ticks`, `use_post_only`.
		- Unit tests showing the POV agent posts limit orders then converts to IOC after timeout.

- VWAP execution trader
	- Implement a VWAP execution strategy under `execution_trader/strategies/vwap.py` that consumes a parent order and executes child orders spaced over the trading window, targeting volume-weighted-average price. Core behavior:
		- Accept `start_time`, `end_time`, `total_quantity`, and `schedule_granularity_seconds`.
		- At each step, compute target cumulative volume from a historical or live volume profile, and send child orders sized to track that profile.
		- Support `aggressiveness` parameter to trade more or less aggressively than the profile (mixing limit and market as configured).
	- Acceptance criteria:
		- A clear API in config and minimal unit tests verifying the scheduling logic and child sizing.

Notes and priorities:
- These items are medium-complexity; I recommend tackling them in the above order (Pusher random path first, POV hybrid second, VWAP third).
- For Pusher's random path, we can re-use the `quote_helpers` book parsing utilities and add a small `path_generator` helper in `market_maker/utils` to keep things testable.
- I can start implementing any of these in follow-up PRs; tell me which one you'd like first.

## Running Tests
- Use `python -m pytest` from the project root to execute the existing test suite.