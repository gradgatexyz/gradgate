# architecture

gradgate is one python process — the **engine** — plus a prebuilt web page it serves and a command line that talks to it. everything runs on your machine; the only traffic out is to the rpc endpoints in `.env` and the ipfs gateways for launch images.

```text
             robinhood chain
   websocket (launches, trades, heads)   rpc (logs, quotes, names, orders)
                    │                                 │
┌───────────────────▼─────────────────────────────────▼───────────────────┐
│ engine (engine/, 127.0.0.1:8765)                                        │
│                                                                         │
│  indexer.py   state in memory: launches, trades, buyers, smart wallets, │
│               dev records, launch farms, the hour's pulse               │
│  strategies.py  every strategy's verdict, fills, exits, books           │
│  exec_pons.py · pons_pool.py   curve and uniswap v4 orders (live only)  │
│  store.py     optional postgres: the last 24 h and the permanent records│
│  server.py    http api + the feed stream + serves ui/dist               │
└──────────────▲───────────────────────────────▲──────────────────────────┘
               │ http · sse (127.0.0.1)         │ http (127.0.0.1)
        the terminal (ui/dist)            the command line (gradgate/)
```

## the engine

**start.** `seed_wallets` loads the memory the engine ships with — `smart_wallets.json.gz` (114,051 wallet records) and `creators.json.gz` (198,528 dev records), built from 14 days of pons — or, with postgres, reads them from the database. then `backfill` reads the last hour of launches and trades off the chain (the header says `warming up`). with postgres and a recent run, `store.restore` brings back the last day instead and a restart takes seconds.

**running.** `indexer.loop` keeps a websocket subscription to launches, every curve trade and new blocks (`live_ws.py`), and a poller that fills any gap. four more threads:

| thread | every | does |
|---|---|---|
| `exits_loop` | 1 s | fills pending entries, checks every open position's exits |
| `naming_loop` | on a new launch, or 1.5 s | reads names, launch records (tax, fee recipient, bundle, dev buy) and token info in batches |
| `pool_loop` | 3 s | prices graduated positions with the v4 quoter |
| store writer | `GG_FLUSH_S` (30 s) | writes what changed to postgres, when configured |

**a launch's facts.** buyers and sellers are sets of wallets; *smart* buyers are those with ≥3 earlier launches bought and ≥25 % of them graduated; *snipers* are buys in the launch block and the two after it by wallets other than the creator; a *launch farm* is the same fingerprint (dev buy, tax, links, exempt wallets) from other wallets within 30 minutes; the *dev record* counts earlier launches, graduations and dumps.

**strategies.** each time a launch trades, every enabled strategy runs its checklist (`strategies.Engine.matches`). a pass queues an entry; `fill_pending` fills it after `PAPER_LATENCY_S` at the curve's price and fee, and never while the opening tax is above 3 %. `manage` checks exits every second. a book is cash, open positions and closed trades; its p&l comes from cash, so no trade is ever dropped from the total. see [STRATEGIES.md](STRATEGIES.md).

**live.** a strategy with `"mode": "live"` passes the gates in [LIVE.md](LIVE.md) and then signs through `exec_pons` (curves) or `pons_pool` (graduated pools). manual trades from the command line go through `trade.py`, which plans every order with an `eth_call` before anything is signed.

## the api

the terminal and the command line use the same local api. changes answer only the machine the engine runs on (`GRADGATE_ALLOW_EDIT=1` lifts that inside docker, where the port is published on 127.0.0.1 only).

| method · path | returns |
|---|---|
| `GET /api/config` | whether edits are allowed from here, whether live mode is armed, whether postgres is on |
| `GET /api/state` | head, websocket status, pulse, every strategy with its book, positions, closed trades, decisions |
| `GET /api/feed/stream` | server-sent events: `hello` (the newest 100 launches), `launch`, `rows` (changes once a second), `tick` (pulse every 2 s) |
| `GET /api/feed` · `/api/feed/lists` | the feed without streaming · the hour's hot / near / graduated lists and counts |
| `GET /api/pulse/day` | the last 24 hours from postgres (null without it) |
| `GET /api/token/{address}` | a coin card: facts, the dev record, trades, and every strategy's verdict with its checks |
| `GET /img/{cid}` | a launch image, fetched once through ipfs gateways and cached as a 64 px webp |
| `GET /api/strategies` | the strategies as json |
| `POST /api/strategies` · `DELETE /api/strategies/{id}` · `POST /api/strategies/{id}/reset` | save · delete · start a book again (local only) |
| `POST /api/strategies/preview` | how many launches in the feed a draft would pass; nothing is saved |
| `POST /api/kill` · `DELETE /api/kill` | stop · allow new live entries (local only) |

## storage

| where | what | kept |
|---|---|---|
| memory | the working state | while it runs |
| `engine/terminal.db` (sqlite) | a tape of trades for coin cards, decisions, paper closes | local file |
| `engine/strategies.json` | your strategies | until you change them |
| `engine/live_positions.json` | open live positions | until they close |
| postgres (optional) | launches, buyers, paper trades, per-minute stats | 24 h |
| postgres (optional) | wallet and dev records | permanently, growing |

## the terminal

`ui/` is react + vite. `ui/dist` is committed prebuilt, so running gradgate needs no node. to change the terminal: `npm --prefix ui install`, `npm --prefix ui run dev` (proxies `/api` to the engine on 8765), `npm --prefix ui run build`.

## tests

`pytest` covers the opening tax schedule, strategy normalisation and books, the curve math, the api's local-only rules and ui route, manual trade planning, and the live gates — each live test arms a trap in place of the order sender, and a control test proves the trap fires when every gate is open.
