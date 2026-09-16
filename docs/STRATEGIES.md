# strategies

a strategy is a list of rules: **entry rules** say which launches qualify, **exit rules** say when to leave. every launch gets a verdict from every enabled strategy, and a strategy that says yes trades it on its own book — paper by default.

- [how a strategy trades](#how-a-strategy-trades)
- [reading a verdict](#reading-a-verdict)
- [create a strategy](#create-a-strategy)
- [the file](#the-file)
- [parameters](#parameters) — [sizing](#sizing) · [window](#entrywindow--when) · [flow](#entryflow--how-much-interest) · [wallets](#entrywallets--who-is-in) · [launch](#entrylaunch--who-made-it-and-how) · [exit](#exit--when-to-leave) · [json only](#json-only)
- [examples](#examples)

## how a strategy trades

- every enabled strategy checks a launch each time it trades. a strategy buys a launch at most once.
- when every entry rule passes, the entry fills one second later (`PAPER_LATENCY_S`) at the curve's price at that moment, with the curve's own fee (1–5 %, read from its trades).
- **never inside the opening tax.** pons taxes buys at launch by whole block seconds — 99 % in the launch second, 6.18 % one second later, 0.19 % after two, 0 from the third. no fill happens while the tax is above 3 %; paper pays what is left of it.
- open positions are checked every second. an exit also fills one second after it triggers.
- after graduation a position is priced by a uniswap v4 pool quote and sold in the pool.
- each strategy has its own book: cash, open positions, closed trades with the reason each one closed.

when several exits trigger at once, the first in this order names the exit: pre-graduation sell, graduation, take profit, stop loss, trailing, dev sold, flow reversed, timeout.

## reading a verdict

click a launch in the terminal (or run `gradgate token 0x…`): every strategy's take is listed, and each line opens its checks — the launch's value next to what the rule wants.

| verdict | meaning |
|---|---|
| **ready** | every entry rule passes; the strategy buys it (after the latency, and once the opening tax is under 3 %) |
| **waiting** | only rules that time can still fix fail: too young, too few buyers, no smart wallet yet, flow not rising yet |
| **no** | a rule that cannot change fails: the pair, snipers, the dev's record, tax, bundle, socials, launch farm, a closed window, a crowd, a curve past its max |
| **held** / **traded** | the strategy holds it, or already closed a trade on it (with the result) |
| **graduated** / **no buys** | nothing to check |

## create a strategy

1. open the terminal and click **edit** on the strategy closest to what you want.
2. close it and press **+ new strategy**: it forks the strategy you opened last, paused, under a new id.
3. change the rules. the editor's footer shows how many launches in the feed pass right now, and how many the original passed.
4. save, then switch it on with the toggle on its card. it trades on its own paper book from that moment.
5. click the card to see the book.

strategies change only from the machine the engine runs on. `gradgate on <id>` and `gradgate off <id>` do the same from a terminal.

## the file

strategies live in `engine/strategies.json`, created from `engine/strategies.default.json` (the four house strategies) on the first start. the editor writes the same shape. edit it by hand if you like — write every key (copy a house strategy), then restart the engine: the editor fills missing keys, a hand-edited file does not. keys the engine does not know are dropped.

```json
{
  "id": "early",
  "name": "Early & clean",
  "enabled": true,
  "mode": "paper",
  "size": 0.05,
  "cash": 2.0,
  "quote": "any",
  "entry": {
    "min_age_s": 0, "max_age_s": 600,
    "min_buyers": 10, "max_buyers": 50,
    "min_net": 0.0, "max_net": 999.0,
    "min_progress": 0, "max_progress": 100,
    "max_snipe_pct": 5.0, "max_creator_pct": 100.0,
    "creator_max_prior": 999, "creator_min_grads": 0, "creator_no_dumps": false,
    "require_rising": false, "min_buy_sell_ratio": 0.0, "min_vel": 0.0,
    "min_smart": 0, "max_smart": 0,
    "max_creator_tax_bps": 300, "max_exempt": 0,
    "require_socials": false, "no_third_party": false, "max_farm_twins": 1
  },
  "exit": {
    "tp_pct": 10000, "sl_pct": 40, "timeout_s": 1800,
    "on_graduation": true, "on_creator_sell": false,
    "flow_reversal_pct": 100, "trail_pct": 100, "partial_pct": 0, "pre_grad_pct": 0
  }
}
```

`"mode": "live"` is set only by hand — see [LIVE.md](LIVE.md).

## parameters

every rule has an **off** position: a rule in that position checks nothing. the editor groups them the same way as below.

### [sizing]

| key | unit · off | what it does | example |
|---|---|---|---|
| `size` | Ξ per entry | what one entry spends. with `quote: "any"` it is ETH-equivalent: a launch paired with USDG, cbBTC or a stock token is sized as pair amount × 4.2 ÷ that pair's graduation threshold, so the same size buys the same share of a curve on every pair. an entry is skipped when the book has less cash than this. | `0.05` → ten entries use 0.5 Ξ of a 2 Ξ book |
| `quote` | `"any"` · `"ETH"` | `"ETH"` trades only launches paired with ETH; `"any"` trades every pair. the editor calls it *pair*. | `"ETH"` → a /USDG launch reads "pair not traded by this strategy" |
| `cash` | Ξ · default 2.0 | the paper book a strategy starts with. every open position holds its size out of it; closes pay the value back. json only. | |

### [entry.window] — when

| key | unit · off | what it does | example |
|---|---|---|---|
| `min_age_s` | seconds since launch · off 0 | the launch must be at least this old. a younger one is *waiting*. | `30` → let the launch-second snipers finish first |
| `max_age_s` | seconds since launch | the launch must be at most this old. past it the window is closed for good. | `600` → only the first 10 minutes |
| `min_progress` | % of the curve · off 0 | curve fill: how much of the graduation threshold the curve holds right now (pair bought in minus pair sold out). 100 % is graduation, and a graduated launch is never entered. | `60` → only curves past 60 % |
| `max_progress` | % of the curve · off 100 | the curve must not be past this fill. once it is, the verdict is *no*. | `90` → leave the last 10 % to others |

### [entry.flow] — how much interest

| key | unit · off | what it does | example |
|---|---|---|---|
| `min_buyers` | wallets · off 1 | distinct wallets that bought on the curve. below it the launch is *waiting*. | `10` |
| `max_buyers` | wallets · off 0 | at most this many buyers. past it the launch is too crowded, for good. | `50` → early, not after the crowd |
| `min_vel` | buyers per minute · off 0 | buyers divided by the launch's age in minutes (the age counts as at least half a minute). | `3` → 15 buyers by minute five |
| `require_rising` | true · false · off false | the net inflow of the curve is higher than three trades ago and within 10 % of its peak: money is still coming in. | |

### [entry.wallets] — who is in

| key | unit · off | what it does | example |
|---|---|---|---|
| `min_smart` | wallets · off 0 | smart wallets among the buyers. a wallet is smart when, before this buy, it had bought 3 or more launches and at least a quarter of them graduated — over the 14-day history plus everything the engine has seen since. | `1` |
| `max_smart` | wallets · off 0 | at most this many smart wallets: a launch they already piled into is too crowded. | `min_smart 1` + `max_smart 4` → the first smart money, not the last |
| `max_snipe_pct` | % of supply · off 100 | share of the supply bought in the launch block and the two blocks after it by wallets other than the creator. a big number means bots or a bundle got in before anyone could. | `5` |
| `max_creator_pct` | % of supply · off 100 | share of the supply the creator bought on its own curve: the dev buy at launch plus any later buys. | `10` → skip devs holding more than a tenth |

### [entry.launch] — who made it and how

| key | unit · off | what it does | example |
|---|---|---|---|
| `max_creator_tax_bps` | basis points (100 = 1 %) · off 0 | the tax the creator put on trades of its token. while the launch record is not read yet (the first seconds) the rule does not refuse. | `300` → tax up to 3 % |
| `max_exempt` | wallets · off −1 | wallets the launch transaction declared exempt from the opening tax — a declared bundle. `0` allows none. | `0` |
| `max_farm_twins` | launches · off −1 | launches from other wallets in the 30 minutes before this one with the same fingerprint: the exact dev buy, the same tax, the same set of links, the same number of exempt wallets. many twins mean a launch farm. | `1` |
| `require_socials` | true · false · off false | the launch declared at least one of X, a website or telegram. | |
| `creator_max_prior` | launches · off 999 | how many earlier launches the creator may have (14-day history plus live). `0` means first-time devs only. | `0` |
| `creator_min_grads` | launches · off 0 | how many of the creator's earlier launches must have graduated. | `1` → devs who graduated at least once |
| `creator_no_dumps` | true · false · off false | skip creators who ever sold into one of their own curves. burning their own tokens is not a dump. | |

### [exit] — when to leave

| key | unit · off | what it does | example |
|---|---|---|---|
| `tp_pct` | % up · off 10000 | take profit: leave when the position is up this much (a half already taken counts). | `150` → out at +150 % |
| `sl_pct` | % down · off 99 | stop loss: leave when the position is down this much. | `35` → out at −35 % |
| `trail_pct` | % below the best value · off 100 | trailing stop, armed once the position has been above +20 %: leave when its value falls this share below the best value it reached. | `25`, best +100 % (2×) → out under +50 % (1.5×) |
| `timeout_s` | seconds held | the longest a position is held. | `1800` → 30 minutes |
| `partial_pct` | % up · off 0 | take half once, when the position is up this much; the other half follows the other exits. | `100` → half off at +100 % |
| `on_graduation` | true · false | **true**: sell right after the curve graduates, in the launch's uniswap v4 pool. **false**: hold through graduation — the position is then priced by the pool and the other exits still apply. | |
| `on_creator_sell` | true · false · off false | leave as soon as the creator has sold on this curve. | |

### json only

the engine reads these; they are not in the editor.

| key | unit · off | what it does |
|---|---|---|
| `min_net` · `max_net` | Ξ-equivalent · off 0 and 999 | what the curve holds right now, in ETH terms, as a floor and a ceiling. |
| `min_buy_sell_ratio` | ratio · off 0 | buys divided by sells, counted in trades. |
| `no_third_party` | true · false · off false | skip launches whose creator fees go to a wallet other than the creator. |
| `flow_reversal_pct` | % · off 100 | exit: leave when the net inflow falls this share below its peak since the entry (after 20 s held). |
| `pre_grad_pct` | % of the curve · off 0 | exit: sell before graduation, once the curve reaches this fill. |

## examples

how rules combine — not recommendations, and no expected returns. keys not listed stay off.

**the four house strategies**

```text
early & clean       max_age_s 600 · min_buyers 10 · max_buyers 50 · max_snipe_pct 5
                    max_exempt 0 · max_creator_tax_bps 300 · max_farm_twins 1
                    exit: sl_pct 40 · timeout_s 1800 · on_graduation on

follow smart money  max_age_s 900 · min_buyers 5 · min_smart 1 · max_smart 4 · max_snipe_pct 10
                    exit: partial_pct 100 · sl_pct 35 · timeout_s 1800 · on_graduation on

graduation run      max_age_s 7200 · min_buyers 15 · min_progress 60 · max_progress 90 · require_rising on
                    exit: sl_pct 20 · timeout_s 3600 · on_graduation on

trusted dev         max_age_s 1800 · min_buyers 5 · creator_min_grads 1 · creator_no_dumps on
                    max_creator_tax_bps 200 · require_socials on
                    exit: tp_pct 150 · sl_pct 30 · timeout_s 3600 · on_graduation off
```

**first-time devs, small crowd** — fresh wallets only, no bundle, no farm, out fast.

```text
entry: max_age_s 300 · min_buyers 8 · max_buyers 40 · creator_max_prior 0
       max_snipe_pct 3 · max_exempt 0 · max_farm_twins 0 · require_socials on
exit:  tp_pct 80 · sl_pct 30 · timeout_s 900 · on_graduation on
```

**the last stretch to graduation** — curves already far along and still filling; half off early, a trailing stop for the rest.

```text
entry: max_age_s 3600 · min_buyers 20 · min_progress 70 · max_progress 95 · require_rising on
exit:  partial_pct 50 · trail_pct 30 · sl_pct 25 · timeout_s 1800 · on_graduation on
```

**hold through graduation** — a proven dev's launch, kept into the pool instead of sold at graduation.

```text
entry: min_buyers 10 · creator_min_grads 2 · creator_no_dumps on · max_creator_pct 10
exit:  tp_pct 200 · sl_pct 40 · trail_pct 35 · timeout_s 7200 · on_graduation off · on_creator_sell on
```
