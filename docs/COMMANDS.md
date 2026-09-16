# commands

`gradgate start` runs the engine; every other command reads the running engine or the chain. `--port` (default 8765, or `GRADGATE_PORT`) goes before the command: `gradgate --port 8777 hunt`.

## run

| command | does |
|---|---|
| `gradgate start [--no-open]` | the engine and the terminal on `http://127.0.0.1:8765`; opens the browser unless `--no-open`. ctrl+c stops it. |
| `gradgate doctor` | checks python, `.env`, the rpc (head and latency), the logs rpc, the websocket (waits for a block), postgres, the key (address, balance, limits), the kill switch, the built terminal, and whether an engine is running. exits non-zero when something fails. |

## watch

| command | does |
|---|---|
| `gradgate hunt [--rows 24] [--no-screen]` | the live feed in your terminal: age, launch, pair, trades, buyers, smart wallets, curve, dev buy, red flags. |
| `gradgate token <address>` | a coin card: graduation, buyers, dev buy, snipers, tax, bundle, the deployer's record, every strategy's verdict and the rule behind it, red flags. |

## strategies

| command | does |
|---|---|
| `gradgate strategies` | every strategy: on/off, mode, p&l, equity, win rate, trades, open positions. |
| `gradgate book <id> [--rows 12]` | one book: p&l, equity, cash, win rate, open positions and the latest closed trades with their reasons. |
| `gradgate on <id>` · `gradgate off <id>` | run or pause a strategy on its book. |

## trade

manual trades plan by default and need `--send` to go out. see [LIVE.md](LIVE.md).

| command | does |
|---|---|
| `gradgate wallet [token …]` | your address, ETH balance, live limits, the kill switch, and balances of the tokens you list. |
| `gradgate buy <token> <eth> [--slippage 3] [--send] [--yes]` | buy on an open ETH-paired curve, up to `LIVE_MAX_SIZE`. the plan shows tokens out, the minimum at your slippage, the opening tax and an `eth_call` simulation. |
| `gradgate sell <token> [--pct 100] [--slippage 5] [--send] [--yes]` | sell on the curve, or in the uniswap v4 pool after graduation. the plan shows what you hold and what you would receive. |
| `gradgate kill` · `gradgate unkill` | stop · allow new live entries, strategies and manual buys alike. |
