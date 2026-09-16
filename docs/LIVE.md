# live trading

> **real money.** use a fresh wallet that holds only what you can lose. rules that did well on paper can lose live. nothing in gradgate is advice, and nothing promises a return.

gradgate trades real money two ways: **strategies** you switch to live, and **manual trades** from the command line. both sign with the key in your `.env`, on your machine. without a key nothing can trade for real.

- [set up a wallet](#set-up-a-wallet)
- [limits](#limits)
- [live strategies](#live-strategies)
- [manual trades](#manual-trades)
- [what a live order does](#what-a-live-order-does)
- [stop everything](#stop-everything)
- [before you go live](#before-you-go-live)

## set up a wallet

1. create a fresh wallet for gradgate only. fund it with a little ETH on robinhood chain — enough for a few entries and gas.
2. put its key in `.env`: `RH_PRIVATE_KEY=0x…`
3. use your own RPC. public nodes rate-limit (HTTP 429) when the chain is busy. for live trading, put your own provider in `RH_RPC` — [alchemy](https://www.alchemy.com) has a free tier: create an app on Robinhood Chain and paste its HTTPS url. keep `RH_RPC_LOGS` on the public node: it serves wide log ranges the free tier refuses.
4. run `gradgate doctor` — it shows the address, its balance and your limits. `gradgate wallet` shows the same at any time.

the key never leaves `.env` and your machine. the engine reads it to know its own address and to sign; the api that serves the terminal never touches it, and a test fails if that changes.

## limits

set in `.env`, or in `engine/risk.json`, which is re-read on every check (no restart).

| key | default | what it does |
|---|---|---|
| `LIVE_MAX_SIZE` | 0.01 ETH | the largest live entry. a live strategy with a bigger `size` is blocked, not shrunk; `gradgate buy` refuses more. |
| `LIVE_MAX_OPEN` | 3 | live positions open at once, across all strategies. |
| `LIVE_DAILY_STOP` | 0.05 ETH | once live losses closed today reach this, no new live entries until tomorrow. |

```json
{ "max_live_size": 0.01, "max_live_open": 3, "daily_loss_stop": 0.05 }
```

## live strategies

a live strategy is a paper strategy you switched by hand. the editor never makes one.

1. run the strategy on paper first and read its book.
2. in `engine/strategies.json`, set `"mode": "live"` and a `size` within `LIVE_MAX_SIZE`.
3. restart the engine. the header shows **live armed** once a key is set; the strategy's card shows its mode.

every live entry passes, in order: no `KILL` file → a key is set → size within the limit → open positions under the limit → today's losses under the stop → the opening tax for your address at or under 3 %. any gate that fails is written to the strategy's decisions with the reason, and nothing is sent.

open live positions are saved to `engine/live_positions.json` and picked up again after a restart.

## manual trades

`buy` and `sell` **plan by default**. a plan reads the factory record, the curve's own quote, the opening tax for your address and runs an `eth_call` of the exact transaction — it needs no key and no funds. add `--send` to trade; you are asked to type `yes` (or pass `--yes`).

```text
gradgate wallet [token …]                 address, ETH, limits, kill switch, token balances
gradgate buy <token> <eth> [--send]       buy on an open ETH-paired curve (slippage --slippage 3)
gradgate sell <token> [--pct 100] [--send]  sell on the curve, or in the uniswap v4 pool after graduation (--slippage 5)
```

- buys take open curves paired with ETH, up to `LIVE_MAX_SIZE`, and wait out the opening tax: a plan above 3 % refuses.
- sells take ETH-paired curves and any graduated launch's pool, whatever its pair. a first sell of a token approves it first (one transaction), which is why a plan's simulation can revert before that approval exists.
- a `KILL` file blocks manual sends too.
- manual trades do not enter a strategy's book.

## what a live order does

- **buys** go to the curve with `minTokensOut` from the curve's own quote less your slippage, and never inside the opening tax.
- **pairs** — a strategy on `quote: "any"` spends the launch's own pair (ETH, USDG, a stock token), approving the curve once per pair token.
- **sells** go to the curve while it trades, and after graduation to the launch's uniswap v4 pool through the universal router (permit2 approvals once per token). `minOut` comes from a fresh quote.
- **receipts** — nothing is booked on a transaction hash. a buy becomes a position once it is mined, at the tokens that actually arrived; a reverted buy (or one not mined in 2 minutes) leaves the book and its cash comes back. a position closes once its sell is mined, at the ETH the sell actually paid.
- **exits price on the curve itself** — while a live position is on the curve, take profit, stop loss and trailing read the curve's own sell quote (an `eth_call` every 2 seconds), not a formula over the trade tape, which drifts on fast curves.
- **retries** — a reverted exit goes straight back out with wider slippage: 5 %, then 15 %, 40 %, 99 %. a failed half-sell retries after 5 seconds.
- **one wallet, one queue** — sends go out one at a time with their own nonce, so an exit, an approval and a new buy never collide.
- **no mempool** — robinhood chain's sequencer publishes finished blocks, so there is nothing to front-run and priority fees change nothing.

## stop everything

- `gradgate kill` — no new live entries (strategies and manual buys). open positions still exit by their rules. `gradgate unkill` lifts it.
- the same without the command line: `touch engine/KILL`, or `curl -X POST 127.0.0.1:8765/api/kill`.
- to stop live trading for good: set `"mode": "paper"` again, or remove `RH_PRIVATE_KEY` from `.env`, and restart.

## before you go live

- [ ] a fresh wallet with only what you can lose
- [ ] your own RPC in `RH_RPC` (alchemy or any provider), not the public node
- [ ] `gradgate doctor` shows your address and balance, every check `ok`
- [ ] the strategy ran on paper long enough for its book to mean something
- [ ] `LIVE_MAX_SIZE`, `LIVE_MAX_OPEN` and `LIVE_DAILY_STOP` set to amounts you accept losing
- [ ] you know where `gradgate kill` is
- [ ] a first manual `gradgate buy <token> 0.001` planned, then sent, then `gradgate sell <token> --send` — so you have seen one round trip with your own wallet
