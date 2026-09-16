import { useEffect, useState, type ReactNode } from 'react'
import { navClick, PATH, type Page } from './Header'

/* Docs: run it, write strategies, every parameter in detail with examples.
   Every statement here mirrors the engine: engine/strategies.py (BASE, matches, checklist, manage) and engine/indexer.py
   (smart wallets, snipers, launch farm). Change the engine, change this page. */

type P = { k: string; unit: string; off?: string; d: ReactNode; ex?: string }

const Param = ({ p }: { p: P }) => (
  <div className="gg-param">
    <div className="h">
      <span className="k">{p.k}</span>
      <span className="u">{p.unit}</span>
      {p.off && <span className="off">off: {p.off}</span>}
    </div>
    <div className="d">{p.d}</div>
    {p.ex && <div className="ex">{p.ex}</div>}
  </div>
)
const Params = ({ ps }: { ps: P[] }) => <div className="gg-params">{ps.map(p => <Param key={p.k} p={p} />)}</div>
/* each group of sections opens like a man page: the page name, its section number, and a DESCRIPTION label */
const Man = ({ t }: { t: string }) => (
  <div className="gg-man"><span className="t">{t}</span><span className="gg-lbl">description</span></div>
)
const Pre = ({ children }: { children: string }) => (
  <pre className="gg-pre">{children.split('\n').map((l, i) => <div key={i} className={l.trimStart().startsWith('#') ? 'c' : ''}>{l || ' '}</div>)}</pre>
)

const SETTINGS: P[] = [
  { k: 'RH_RPC', unit: 'url', d: 'the robinhood chain RPC for calls: names, launch records, curve and pool quotes. publicnode is free but rate-limits when the chain is busy: before trading live, use your own provider (alchemy has a free tier).', ex: 'https://robinhood-rpc.publicnode.com' },
  { k: 'RH_RPC_LOGS', unit: 'url', d: 'the node the engine reads event logs from when it catches up (the first hour after a start, gaps).', ex: 'https://rpc.mainnet.chain.robinhood.com' },
  { k: 'RH_WS', unit: 'url', d: 'the websocket the live feed comes over: launches, every curve trade, new blocks. without it the engine polls every few seconds.', ex: 'wss://robinhood-rpc.publicnode.com' },
  { k: 'DATABASE_URL', unit: 'postgres url · optional', d: <>keeps the last 24 hours (launches, buyers, paper trades, stats) plus wallet and dev records in postgres. the first start fills it; every later start reads it back in seconds instead of re-reading an hour of chain.</>, ex: 'postgresql://postgres:postgres@127.0.0.1:5432/gradgate' },
  { k: 'GG_FLUSH_S', unit: 'seconds · default 30', d: 'how often the engine writes what changed to postgres.' },
  { k: 'PAPER_LATENCY_S', unit: 'seconds · default 1', d: 'delay between a signal and its fill, for entries and exits alike — the time a real order needs to land.' },
  { k: 'RH_LOGS_SPAN', unit: 'blocks · default 5000', d: 'the widest log range asked of the node in one call; lower it if the node answers with timeouts.' },
  { k: 'RH_PRIVATE_KEY', unit: 'hex key · live only', d: <>the wallet live strategies trade from. leave it out and nothing can trade for real. see <a href="#live">live trading</a>.</> },
  { k: 'LIVE_MAX_SIZE', unit: 'Ξ · default 0.01', d: 'the largest live entry. a live strategy with a bigger size is blocked, not shrunk.' },
  { k: 'LIVE_MAX_OPEN', unit: 'positions · default 3', d: 'live positions open at once, across all strategies.' },
  { k: 'LIVE_DAILY_STOP', unit: 'Ξ · default 0.05', d: 'once live losses closed today reach this, no new live entries until tomorrow.' },
]

const SIZING: P[] = [
  { k: 'size', unit: 'Ξ per entry', d: <>what one entry spends. with <code>pair = any</code> it is ETH-equivalent: a launch paired with USDG, cbBTC or a stock token is sized as pair amount × 4.2 ÷ that pair's graduation threshold, so the same size buys the same share of a curve on every pair. an entry is skipped when the book has less cash than this.</>, ex: 'size = 0.05 → ten entries use 0.5 Ξ of a 2 Ξ book' },
  { k: 'pair', unit: 'any · eth', d: <><code>eth</code> trades only launches paired with ETH; <code>any</code> trades every pair. in json this key is <code>quote</code>: <code>"ETH"</code> or <code>"any"</code>.</>, ex: 'pair = eth → a /USDG launch reads "pair not traded by this strategy"' },
  { k: 'cash', unit: 'Ξ · json only · default 2.0', d: 'the paper book a strategy starts with. every open position holds its size out of it; closes pay the value back.' },
]

const WINDOW: P[] = [
  { k: 'min_age_s', unit: 'seconds since launch', off: '0', d: 'the launch must be at least this old. a younger one is "waiting".', ex: 'min_age_s = 30 → let the launch-second snipers finish first' },
  { k: 'max_age_s', unit: 'seconds since launch', d: 'the launch must be at most this old. past it the window is closed for good.', ex: 'max_age_s = 600 → only the first 10 minutes' },
  { k: 'min_progress', unit: '% of the curve', off: '0', d: <>curve fill: how much of the graduation threshold the curve holds right now (pair bought in minus pair sold out). 100 % is graduation, and a graduated launch is never entered.</>, ex: 'min_progress = 60 → only curves past 60 %' },
  { k: 'max_progress', unit: '% of the curve', off: '100', d: 'the curve must not be past this fill. once it is, the verdict is "no".', ex: 'max_progress = 90 → leave the last 10 % to others' },
]

const FLOW: P[] = [
  { k: 'min_buyers', unit: 'wallets', off: '1', d: 'distinct wallets that bought on the curve. below it the launch is "waiting" — the engine does not even list a reason.', ex: 'min_buyers = 10' },
  { k: 'max_buyers', unit: 'wallets', off: '0', d: 'at most this many buyers. past it the launch is "too crowded" for good.', ex: 'max_buyers = 50 → early, not after the crowd' },
  { k: 'min_vel', unit: 'buyers per minute', off: '0', d: 'buyers divided by the launch\'s age in minutes (the age counts as at least half a minute).', ex: 'min_vel = 3 → 15 buyers by minute five' },
  { k: 'require_rising', unit: 'on · off', off: 'off', d: 'the net inflow of the curve is higher than three trades ago and within 10 % of its peak: money is still coming in, not leaving.' },
]

const WALLETS: P[] = [
  { k: 'min_smart', unit: 'wallets', off: '0', d: <>smart wallets among the buyers. a wallet counts as smart when, before this buy, it had bought 3 or more launches and at least a quarter of them graduated — over the 14-day history plus everything the engine has seen since.</>, ex: 'min_smart = 1' },
  { k: 'max_smart', unit: 'wallets', off: '0', d: 'at most this many smart wallets: a launch they already piled into is "too crowded".', ex: 'min_smart = 1 and max_smart = 4 → follow the first smart money, not the last' },
  { k: 'max_snipe_pct', unit: '% of supply', off: '100', d: 'share of the supply bought in the launch block and the two blocks after it by wallets other than the creator. a big number means bots or a bundle got in before anyone could.', ex: 'max_snipe_pct = 5' },
  { k: 'max_creator_pct', unit: '% of supply', off: '100', d: 'share of the supply the creator bought on its own curve: the dev buy at launch plus any later buys.', ex: 'max_creator_pct = 10 → skip devs holding more than a tenth' },
]

const LAUNCH: P[] = [
  { k: 'max_creator_tax_bps', unit: 'basis points · 100 = 1 %', off: '0', d: 'the tax the creator put on trades of its token. while the launch record is not read yet (the first seconds) the rule does not refuse.', ex: 'max_creator_tax_bps = 300 → tax up to 3 %' },
  { k: 'max_exempt', unit: 'wallets', off: '−1', d: <>wallets the launch transaction declared exempt from the opening tax — a declared bundle. <code>0</code> allows none.</>, ex: 'max_exempt = 0' },
  { k: 'max_farm_twins', unit: 'launches', off: '−1', d: 'launches from other wallets in the 30 minutes before this one with the same fingerprint: the exact dev buy, the same tax, the same set of links, the same number of exempt wallets. many twins mean a launch farm.', ex: 'max_farm_twins = 1' },
  { k: 'require_socials', unit: 'on · off', off: 'off', d: 'the launch declared at least one of X, a website or telegram.' },
  { k: 'creator_max_prior', unit: 'launches', off: '999', d: 'how many earlier launches the creator may have (14-day history plus live). 0 means first-time devs only.', ex: 'creator_max_prior = 0' },
  { k: 'creator_min_grads', unit: 'launches', off: '0', d: 'how many of the creator\'s earlier launches must have graduated.', ex: 'creator_min_grads = 1 → devs who graduated at least once' },
  { k: 'creator_no_dumps', unit: 'on · off', off: 'off', d: 'skip creators who ever sold into one of their own curves. burning their own tokens is not a dump.' },
]

const EXIT: P[] = [
  { k: 'tp_pct', unit: '% up', off: '10000', d: 'take profit: leave when the position is up this much (a half already taken counts).', ex: 'tp_pct = 150 → out at +150 %' },
  { k: 'sl_pct', unit: '% down', off: '99', d: 'stop loss: leave when the position is down this much.', ex: 'sl_pct = 35 → out at −35 %' },
  { k: 'trail_pct', unit: '% below the best value', off: '100', d: 'trailing stop, armed once the position has been above +20 %: leave when its value falls this share below the best value it reached.', ex: 'trail_pct = 25, best +100 % (2×) → out once it drops under +50 % (1.5×)' },
  { k: 'timeout_s', unit: 'seconds held', d: 'the longest a position is held.', ex: 'timeout_s = 1800 → 30 minutes' },
  { k: 'partial_pct', unit: '% up', off: '0', d: 'take half once, when the position is up this much; the other half follows the other exits.', ex: 'partial_pct = 100 → half off at +100 %, the rest rides' },
  { k: 'on_graduation', unit: 'on · off', d: <><b>on</b>: sell right after the curve graduates, in the launch's uniswap v4 pool. <b>off</b>: hold through graduation — the position is then priced by the pool and the other exits still apply.</> },
  { k: 'on_creator_sell', unit: 'on · off', off: 'off', d: 'leave as soon as the creator has sold on this curve. if it had already sold before the entry, that is the next check.' },
]

const JSON_ONLY: P[] = [
  { k: 'min_net · max_net', unit: 'Ξ-equivalent', d: 'what the curve holds right now, in ETH terms, as a floor and a ceiling (0 and 999 are off).' },
  { k: 'min_buy_sell_ratio', unit: 'ratio', off: '0', d: 'buys divided by sells, counted in trades.' },
  { k: 'no_third_party', unit: 'true · false', off: 'false', d: 'skip launches whose creator fees go to a wallet other than the creator.' },
  { k: 'flow_reversal_pct', unit: '% · exit', off: '100', d: 'leave when the net inflow falls this share below its peak since the entry (after 20 s held).' },
  { k: 'pre_grad_pct', unit: '% of the curve · exit', off: '0', d: 'sell before graduation, once the curve reaches this fill.' },
]

const EARLY_JSON = `{
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
}`

const TOC: [string, [string, string][]][] = [
  ['run it', [['install', 'install'], ['commands', 'commands'], ['settings', 'settings'], ['live', 'live trading']]],
  ['strategies', [['strategies', 'how a strategy trades'], ['create', 'create a strategy'], ['parameters', 'parameters'], ['examples', 'examples']]],
  ['the feed', [['verdicts', 'reading a verdict']]],
]

export function Docs({ onPage }: { onPage: (p: Page, hash?: string) => void }) {
  const [active, setActive] = useState('')
  // a link straight to a section (/docs#parameters) lands on it
  useEffect(() => {
    const id = decodeURIComponent(location.hash.slice(1))
    if (id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }))
  }, [])
  // the nav marks the section being read: the topmost one still under the header wins
  useEffect(() => {
    const secs = [...document.querySelectorAll<HTMLElement>('.gg-doc section[id]')]
    if (!secs.length || !('IntersectionObserver' in window)) return
    const io = new IntersectionObserver(es => {
      const top = es.filter(e => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
      if (top) setActive(top.target.id)
    }, { rootMargin: '-90px 0px -68% 0px' })
    secs.forEach(s => io.observe(s))
    return () => io.disconnect()
  }, [])
  const jump = (id: string) => {
    history.replaceState(null, '', PATH.docs + '#' + id)
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  const app = (label: string) => <a href={PATH.home} onClick={e => navClick(e, () => onPage('home'))}>{label}</a>

  return (
    <section className="wrap gg-page">
      <div className="gg-title">docs</div>
      <p className="gg-lead">run the engine on your own machine, write strategies, and know exactly what every rule checks.</p>
      <div className="gg-docs">
        <nav className="gg-toc">
          <div className="gg-lbl">on this page</div>
          {TOC.map(([h, items]) => (
            <div key={h} className="grp">
              <div className="h">{h}</div>
              {items.map(([id, label]) => (
                <a key={id} href={'#' + id} className={active === id ? 'on' : ''} onClick={e => navClick(e, () => jump(id))}>{label}</a>
              ))}
            </div>
          ))}
        </nav>

        <div className="gg-doc">
          <Man t="gradgate-run (1) · commands" />
          <section id="install">
            <h2>install</h2>
            <p>gradgate is one process: the engine reads robinhood chain, trades your strategies and serves this terminal on <code>127.0.0.1:8765</code>. you need git and python 3.11 or newer. node is only for changing the terminal itself.</p>
            <Pre>{`git clone https://github.com/gradgatexyz/gradgate gradgate
cd gradgate
./install.sh              # a .venv, the gradgate command, a .env from .env.example
.venv/bin/gradgate start  # opens http://127.0.0.1:8765`}</Pre>
            <p>or with docker, postgres included: <code>docker compose up</code>.</p>
            <p>the first start reads the last hour of launches and trades off the chain: two to four minutes, the header says <code>warming up</code> until then. <code>gradgate doctor</code> checks the rpc, the websocket, the database and the key before you start.</p>
            <p>with postgres the engine keeps the last 24 hours and later starts take seconds. without it everything works; each start just warms up again.</p>
            <div className="gg-note">the engine's api has no login: it listens on <code>127.0.0.1</code> and accepts changes only from this machine. never expose port 8765 to the internet.</div>
          </section>

          <section id="commands">
            <h2>commands</h2>
            <p>the <code>gradgate</code> command starts the engine and talks to the running one from a second terminal.</p>
            <Pre>{`gradgate start              # the engine and this terminal on 127.0.0.1:8765
gradgate doctor             # rpc, websocket, database, key and limits, checked
gradgate hunt               # the live feed in your terminal
gradgate strategies         # every strategy with its book
gradgate book early         # one book: cash, open positions, closed trades
gradgate on early           # switch a strategy on (off to pause it)
gradgate token 0x…          # a coin card with every strategy's verdict
gradgate kill               # stop new live entries now (unkill lifts it)
gradgate wallet             # your address, ETH, limits and the kill switch
gradgate buy 0x… 0.005      # plan a buy on the curve; --send trades
gradgate sell 0x… --pct 50  # plan a sell on the curve or in the pool; --send trades`}</Pre>
          </section>

          <section id="settings">
            <h2>settings</h2>
            <p>the engine reads <code>.env</code> in the repository root on start. the defaults in <code>.env.example</code> are free public endpoints:</p>
            <Pre>{`RH_RPC=https://robinhood-rpc.publicnode.com
RH_RPC_LOGS=https://rpc.mainnet.chain.robinhood.com
RH_WS=wss://robinhood-rpc.publicnode.com`}</Pre>
            <Params ps={SETTINGS} />
            <h3>files the engine keeps in engine/</h3>
            <ul>
              <li><code>strategies.json</code> — your strategies. created from <code>strategies.default.json</code> (the four house strategies) on the first start; the editor writes it.</li>
              <li><code>smart_wallets.json.gz</code>, <code>creators.json.gz</code> — the memory the engine starts with: wallet and dev records built from 14 days of pons.</li>
              <li><code>terminal.db</code> — a local sqlite tape of trades for the coin card.</li>
              <li><code>risk.json</code>, <code>KILL</code>, <code>live_positions.json</code> — live trading only, below.</li>
            </ul>
          </section>

          <section id="live">
            <h2>live trading</h2>
            <div className="gg-note">real money. use a fresh wallet holding only what you can lose. rules that did well on paper can lose live; nothing here is advice.</div>
            <ol>
              <li>put the wallet key in <code>.env</code>: <code>RH_PRIVATE_KEY=0x…</code></li>
              <li>set limits — in <code>.env</code> (<code>LIVE_MAX_SIZE</code>, <code>LIVE_MAX_OPEN</code>, <code>LIVE_DAILY_STOP</code>) or in <code>engine/risk.json</code>, which is re-read on every check, no restart needed.</li>
              <li>the editor only makes paper strategies. pick one in <code>engine/strategies.json</code>, set <code>"mode": "live"</code> and a <code>size</code> within <code>LIVE_MAX_SIZE</code>, restart the engine.</li>
              <li>stop new live entries at any moment: <code>gradgate kill</code>, <code>touch engine/KILL</code> or <code>curl -X POST 127.0.0.1:8765/api/kill</code>. open positions still exit by their rules. <code>DELETE /api/kill</code> or removing the file lifts it.</li>
            </ol>
            <Pre>{`# engine/risk.json
{ "max_live_size": 0.01, "max_live_open": 3, "daily_loss_stop": 0.05 }`}</Pre>
            <p>live orders spend the launch's own pair (ETH, USDG, a stock token…), sell on the curve while it trades and in the uniswap v4 pool after graduation, and book nothing until the transaction is mined: a reverted buy returns its cash, a reverted exit goes back out with wider slippage. open live positions are saved to <code>live_positions.json</code> and picked up again after a restart.</p>
            <h3>manual trades</h3>
            <p><code>gradgate buy &lt;token&gt; &lt;eth&gt;</code> and <code>gradgate sell &lt;token&gt;</code> plan by default: the curve's quote, the minimum you accept at your slippage, the opening tax for your address and an <code>eth_call</code> of the exact transaction — no key and no funds needed. add <code>--send</code> to trade; you type <code>yes</code> to confirm. buys take open ETH-paired curves up to <code>LIVE_MAX_SIZE</code>; sells take the curve or, after graduation, the uniswap v4 pool. manual trades do not enter a strategy's book.</p>
          </section>

          <Man t="gradgate-strategy (5) · config" />
          <section id="strategies">
            <h2>how a strategy trades</h2>
            <ul>
              <li>every enabled strategy checks a launch each time it trades. a strategy buys a launch at most once.</li>
              <li>when every entry rule passes, the entry fills one second later (<code>PAPER_LATENCY_S</code>) at the curve's price at that moment, with the curve's own fee (1–5 %, read from its trades) — and never while the launch's opening tax is above 3 % (it starts at 99 % and is gone three seconds after the launch).</li>
              <li>open positions are checked every second. an exit also fills one second after it triggers.</li>
              <li>after graduation a position is priced by a uniswap v4 pool quote and sold in the pool.</li>
              <li>each strategy has its own book: cash, open positions, closed trades.</li>
            </ul>
            <p>when several exits trigger at once, the first in this order names the exit: graduation, take profit, stop loss, trailing, dev sold, timeout.</p>
          </section>

          <section id="create">
            <h2>create a strategy</h2>
            <ol>
              <li>open {app('the terminal')}. click <b>edit</b> on the strategy closest to what you want — the editor opens with its rules.</li>
              <li>close it and press <b>+ new strategy</b>: it forks the strategy you opened last, paused, under a new id.</li>
              <li>change the rules. the editor's footer shows how many launches in the feed pass right now, and how many the original passed. </li>
              <li>save, then switch it on with the toggle on its card. the engine writes <code>engine/strategies.json</code>; any number of strategies run at once, each on its own paper book.</li>
              <li>click the card to see the book: equity, win rate, open positions, closed trades with the reason each one closed.</li>
            </ol>
            <p>locally a strategy is json in <code>engine/strategies.json</code>, the same shape the editor writes. when you edit the file by hand, write every key (copy a house strategy and change it) and restart the engine: the editor fills missing keys, a hand-edited file does not.</p>
            <Pre>{EARLY_JSON}</Pre>
          </section>

          <section id="parameters">
            <h2>parameters</h2>
            <p>every rule has an <b>off</b> position, listed under its name: a rule in that position checks nothing. the editor groups them the same way.</p>
            <h3 id="p-sizing">[sizing]</h3><Params ps={SIZING} />
            <h3 id="p-window">[entry.window] — when</h3><Params ps={WINDOW} />
            <h3 id="p-flow">[entry.flow] — how much interest</h3><Params ps={FLOW} />
            <h3 id="p-wallets">[entry.wallets] — who is in</h3><Params ps={WALLETS} />
            <h3 id="p-launch">[entry.launch] — who made it and how</h3><Params ps={LAUNCH} />
            <h3 id="p-exit">[exit] — when to leave</h3><Params ps={EXIT} />
            <h3 id="p-json">json only</h3>
            <p>the engine still reads these; they left the editor to keep it short. set them in <code>strategies.json</code>.</p>
            <Params ps={JSON_ONLY} />
          </section>

          <section id="examples">
            <h2>examples</h2>
            <p>how rules combine — not recommendations, and no expected returns. keys not listed stay off.</p>
            <h3>the four house strategies</h3>
            <Pre>{`early & clean       max_age_s 600 · min_buyers 10 · max_buyers 50 · max_snipe_pct 5
                    max_exempt 0 · max_creator_tax_bps 300 · max_farm_twins 1
                    exit: sl_pct 40 · timeout_s 1800 · on_graduation on

follow smart money  max_age_s 900 · min_buyers 5 · min_smart 1 · max_smart 4 · max_snipe_pct 10
                    exit: partial_pct 100 · sl_pct 35 · timeout_s 1800 · on_graduation on

graduation run      max_age_s 7200 · min_buyers 15 · min_progress 60 · max_progress 90 · require_rising on
                    exit: sl_pct 20 · timeout_s 3600 · on_graduation on

trusted dev         max_age_s 1800 · min_buyers 5 · creator_min_grads 1 · creator_no_dumps on
                    max_creator_tax_bps 200 · require_socials on
                    exit: tp_pct 150 · sl_pct 30 · timeout_s 3600 · on_graduation off`}</Pre>
            <h3>first-time devs, small crowd</h3>
            <p>fresh wallets only, no bundle, no farm, out fast.</p>
            <Pre>{`entry: max_age_s 300 · min_buyers 8 · max_buyers 40 · creator_max_prior 0
       max_snipe_pct 3 · max_exempt 0 · max_farm_twins 0 · require_socials on
exit:  tp_pct 80 · sl_pct 30 · timeout_s 900 · on_graduation on`}</Pre>
            <h3>the last stretch to graduation</h3>
            <p>curves already far along and still filling; half off early, a trailing stop for the rest.</p>
            <Pre>{`entry: max_age_s 3600 · min_buyers 20 · min_progress 70 · max_progress 95 · require_rising on
exit:  partial_pct 50 · trail_pct 30 · sl_pct 25 · timeout_s 1800 · on_graduation on`}</Pre>
            <h3>hold through graduation</h3>
            <p>a proven dev's launch, kept into the pool instead of sold at graduation.</p>
            <Pre>{`entry: min_buyers 10 · creator_min_grads 2 · creator_no_dumps on · max_creator_pct 10
exit:  tp_pct 200 · sl_pct 40 · trail_pct 35 · timeout_s 7200 · on_graduation off · on_creator_sell on`}</Pre>
          </section>

          <Man t="gradgate-feed (7) · api" />
          <section id="verdicts">
            <h2>reading a verdict</h2>
            <p>click any launch in the feed: the coin card lists every strategy's take on it. click a strategy line to see its checks, each with the launch's value and what the rule wants.</p>
            <ul>
              <li><b className="mint">ready</b> — every entry rule passes; the strategy buys it on the next trade.</li>
              <li><b className="amber">waiting</b> — only rules that time can still fix fail: too young, too few buyers, no smart wallet yet, flow not rising yet.</li>
              <li><b className="red">no</b> — a rule that cannot change fails: the pair, snipers, the dev's record, tax, bundle, socials, launch farm, a closed window, a crowd, a curve past its max.</li>
              <li><b>held</b> / <b>traded</b> — the strategy holds it or already closed a trade on it (with the result); <b>graduated</b> / <b>no buys</b> — nothing to check.</li>
            </ul>
          </section>
        </div>
      </div>
    </section>
  )
}
