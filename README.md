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

## Strategy Status
- **Designated Market Maker**: live and covered by tests.
- **Simple Market Maker**: placeholder; no executable logic yet.
- **Inventory Aware Market Maker**: placeholder; inventory control logic not implemented.

## TODO
- Market-simulating maker that ingests JSON-configured quote levels and trades the book toward that target while maintaining a large capital base.
- Execution trader XO that accepts customer orders, fills at `mid + spread`, and then seeks price improvement for the internal inventory leg.

## Running Tests
- Use `python -m pytest` from the project root to execute the existing test suite.