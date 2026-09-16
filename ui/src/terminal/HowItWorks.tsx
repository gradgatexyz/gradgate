import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { FeedData } from '../api'
import { navClick, PATH, type Page } from './Header'

/* How it works as a deck, one idea a slide.
   HIST is the 14-day history.db the house strategies and the wallet / creator records were built from. */

const HIST = { launches: 331_149, grads: 6_513, trades: 17_353_087, wallets: 114_051, creators: 198_528 }
const fmt = (v: number | undefined) => (v == null ? '—' : Math.round(v).toLocaleString('en'))

type Slide = { k: string; title: string; body: ReactNode; visual: ReactNode }

export function HowItWorks({ pulse, onPage }: { pulse: FeedData['pulse'] | undefined; onPage: (p: Page, hash?: string) => void }) {
  const link = (p: Page, label: string, hash = '', cls = 'gg-ghost') => (
    <a className={cls} href={PATH[p] + (hash ? '#' + hash : '')} onClick={e => navClick(e, () => onPage(p, hash))}>{label}</a>
  )
  const gradShare = (100 * HIST.grads) / HIST.launches

  const slides: Slide[] = [
    {
      k: 'gradgate',
      title: 'every pons launch, read the moment it lands and traded on paper by rules you write',
      body: <p>gradgate watches the pons launchpad on robinhood chain, reads each launch against what it knows from its history, and lets strategies trade the ones that pass. on paper by default; for real once you give the engine a key.</p>,
      visual: (
        <div>
          <div className="gg-nums">
            <div className="gg-numtile"><div className="v">{fmt(pulse?.launches_1h)}</div><div className="l">launches in the last hour</div></div>
            <div className="gg-numtile"><div className="v">{fmt(pulse?.buys_1h)}</div><div className="l">curve buys in the last hour</div></div>
            <div className="gg-numtile"><div className="v">{fmt(pulse?.grads_1h)}</div><div className="l">graduations in the last hour</div></div>
            <div className="gg-numtile"><div className="v">{fmt(pulse?.launches_5m)}</div><div className="l">launches in the last 5 min</div></div>
          </div>
          <div className="gg-vcap"><span className="gg-dot" /> live from the engine</div>
        </div>
      ),
    },
    {
      k: 'the problem',
      title: 'hundreds of launches an hour, and almost all of them die',
      body: <>
        <p>in 14 days pons saw {fmt(HIST.launches)} launches. {fmt(HIST.grads)} of them reached graduation — about 2 in 100. the rest went quiet within minutes, often after a bundle, a sniper or a serial dev got to the first buyers.</p>
        <p>nobody can read that by hand. a machine can.</p>
      </>,
      visual: (
        <div className="gg-funnel">
          <div className="row"><span className="l">launched</span><span className="bar"><i style={{ width: '100%' }} /></span><span className="v">{fmt(HIST.launches)}</span></div>
          <div className="row"><span className="l">graduated</span><span className="bar"><i className="g" style={{ width: `${Math.max(gradShare, 0.8)}%` }} /></span><span className="v">{fmt(HIST.grads)}</span></div>
          <div className="gg-vcap">{gradShare.toFixed(1)}% of launches graduate · 14 days of pons</div>
        </div>
      ),
    },
    {
      k: 'step 1 · watch',
      title: 'it sees a launch in the block it lands',
      body: <p>the engine keeps a live connection to the chain: every launch, every buy and sell on every curve, every graduation. a new launch is on the feed within seconds, named, with its launch record read from the transaction that created it.</p>,
      visual: (
        <div className="gg-flow">
          <div className="n">robinhood chain<span>a block every ~0.1 s</span></div>
          <div className="a">↓ socket + rpc</div>
          <div className="n">gradgate engine<span>decodes, keeps the last day</span></div>
          <div className="a">↓</div>
          <div className="n">feed · coin card · strategies<span>/app</span></div>
        </div>
      ),
    },
    {
      k: 'step 2 · read',
      title: 'each launch becomes a set of facts',
      body: <>
        <p>not a chart — a few minutes in there is nothing to chart yet. what matters is who is in it and who made it:</p>
        <ul>
          <li>buyers, and how many of them are smart wallets</li>
          <li>the dev: earlier launches, graduations, dumps</li>
          <li>snipers in the first blocks, the declared bundle, the creator tax</li>
          <li>launch farms: the same template shipped from many wallets</li>
        </ul>
      </>,
      visual: (
        <div className="gg-box">
          <div className="gg-vcap" style={{ marginTop: 0, marginBottom: 10 }}>example launch</div>
          <div className="gg-facts">
            <div className="f"><span className="k">curve</span><span className="v"><span className="meter">███░░░░░</span> 42% · 1.76 of 4.20 Ξ</span></div>
            <div className="f"><span className="k">buyers</span><span className="v">38 · 2 smart</span></div>
            <div className="f"><span className="k">dev buy</span><span className="v">1.2% of supply</span></div>
            <div className="f"><span className="k">snipers</span><span className="v">0.4% of supply</span></div>
            <div className="f"><span className="k">creator tax</span><span className="v">1%</span></div>
            <div className="f"><span className="k">bundle</span><span className="v">none</span></div>
            <div className="f"><span className="k">deployer</span><span className="v">3 prior · 1 graduated</span></div>
            <div className="f"><span className="k">launch farm</span><span className="v">no twins</span></div>
          </div>
        </div>
      ),
    },
    {
      k: 'the memory',
      title: 'the facts lean on the full history of pons',
      body: <>
        <p>before it reads a new launch, gradgate already knows {fmt(HIST.wallets)} wallets and {fmt(HIST.creators)} devs from the on-chain history: who buys launches that graduate, who launches and dumps.</p>
        <p>a smart wallet bought 3 or more launches, and at least a quarter of them graduated. the four house strategies were shaped on the same history.</p>
      </>,
      visual: (
        <div className="gg-nums">
          <div className="gg-numtile"><div className="v">{fmt(HIST.launches)}</div><div className="l">launches read</div></div>
          <div className="gg-numtile"><div className="v">{(HIST.trades / 1e6).toFixed(1)} M</div><div className="l">curve trades</div></div>
          <div className="gg-numtile"><div className="v">{fmt(HIST.wallets)}</div><div className="l">wallets with a record</div></div>
          <div className="gg-numtile"><div className="v">{fmt(HIST.creators)}</div><div className="l">devs with a record</div></div>
        </div>
      ),
    },
    {
      k: 'step 3 · rules',
      title: 'a strategy is a list of rules',
      body: <>
        <p>entry rules say which launches qualify: age, curve fill, buyers, smart wallets, snipers, the dev's record, tax, bundle. exit rules say when to leave.</p>
        <p>every launch gets a verdict from every strategy — <b className="mint">ready</b>, <b className="amber">waiting</b> (time can still fix it) or <b className="red">no</b> — with the checks behind it.</p>
      </>,
      visual: (
        <div className="gg-box">
          <div className="gg-vcap" style={{ marginTop: 0, marginBottom: 12 }}>early &amp; clean · example launch → <span className="mint">ready</span></div>
          <div className="gg-checks">
            {[['age', '212s', '0–600s'], ['buyers', '23', '10–50'], ['snipe', '1.2%', '≤5%'], ['tax', '1.0%', '≤3%'], ['bundle', '0 exempt', '≤0'], ['farm', '0 twins', '≤1']].map(([k, v, w]) => (
              <div className="gg-check" key={k}><span className="m mint">✓</span><span className="k">{k}</span><span className="v">{v}</span><span className="w">{w}</span></div>
            ))}
          </div>
        </div>
      ),
    },
    {
      k: 'step 4 · paper',
      title: 'passing launches are bought with paper money at the real price',
      body: <p>a signal fills one second later at the price the curve has then, with the curve's fee — and never inside the launch's opening tax, which starts at 99% and is gone after three seconds. from there the engine checks the exits every second. after graduation the position is priced and sold in the launch's uniswap v4 pool, the way a real one would be.</p>,
      visual: (
        <div className="gg-flow">
          <div className="n">signal<span>every entry rule passes</span></div>
          <div className="a">↓ 1 s</div>
          <div className="n">fill<span>curve price + fee · opening tax ≤3%</span></div>
          <div className="a">↓ every second</div>
          <div className="n">manage<span>stop · take profit · trailing · half off</span></div>
          <div className="a">↓</div>
          <div className="n">exit<span>timeout · dev sold · graduation → v4 pool</span></div>
        </div>
      ),
    },
    {
      k: 'house strategies',
      title: 'four strategies to start from',
      body: <p>each runs on its own paper book from the moment the engine starts. they are starting points to fork, not advice.</p>,
      visual: (
        <div className="gg-houses">
          {[['early', 'early & clean', 'up to 10 min old · 10–50 buyers · snipers ≤5% · no bundle · tax ≤3%'],
            ['smart', 'follow smart money', 'up to 15 min · 1–4 smart wallets in · snipers ≤10% · half off at +100%'],
            ['gradrun', 'graduation run', 'curve 60–90% and still rising · 15+ buyers · stop at −20%'],
            ['trusted', 'trusted dev', 'dev graduated before and never dumped · socials · tax ≤2% · holds through graduation']].map(([id, n, r]) => (
            <div className="h" key={id}><div className="id">{id}</div><div className="n">{n}</div><div className="r">{r}</div></div>
          ))}
        </div>
      ),
    },
    {
      k: 'yours',
      title: 'fork one, change a rule, switch it on',
      body: <>
        <p>while you change the rules, the editor shows how many launches in the feed would pass. a saved strategy trades on its own book.</p>
        <p>everything runs on this machine. paper is the default: real money needs your own key, your limits and a strategy switched to live. nothing here promises a return.</p>
        <div className="acts">
          {link('home', 'open the terminal', '', 'gg-cta')}
          {link('docs', 'live trading', 'live')}
          {link('docs', 'every parameter', 'parameters')}
        </div>
      </>,
      visual: (
        <div className="gg-box">
          <div className="gg-h" style={{ marginBottom: 14 }}>what it never does</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {['sends your key anywhere — it stays in .env on this machine', 'spends real money on a paper strategy', 'promises a return — a verdict is a read, not a signal', 'hides a rule: every verdict lists its checks'].map(n => (
              <div className="gg-never" key={n}><span className="red">✕</span><span>{n}</span></div>
            ))}
          </div>
        </div>
      ),
    },
  ]

  const count = slides.length
  const [i, setI] = useState(() => { const n = parseInt(location.hash.slice(1), 10); return n >= 1 && n <= count ? n - 1 : 0 })
  const go = useCallback((n: number) => {
    const j = Math.min(count - 1, Math.max(0, n))
    setI(j)
    history.replaceState(null, '', PATH['how it works'] + (j ? '#' + (j + 1) : ''))
  }, [count])
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowRight') go(i + 1)
      if (e.key === 'ArrowLeft') go(i - 1)
    }
    window.addEventListener('keydown', f)
    return () => window.removeEventListener('keydown', f)
  }, [i, go])

  const s = slides[i]
  return (
    <section className="wrap gg-page">
      <div className="gg-title">how it works</div>
      <p className="gg-lead" style={{ marginBottom: 22 }}>{count} slides, one idea each. use ← → or the dots.</p>
      <div className="gg-deck">
        <div className="gg-deck-bar"><i style={{ width: `${(100 * (i + 1)) / count}%` }} /></div>
        <div className="gg-slide" key={i}>
          <div className="txt">
            <div className="k">{String(i + 1).padStart(2, '0')} / {String(count).padStart(2, '0')} · {s.k}</div>
            <h2>{s.title}</h2>
            {s.body}
          </div>
          <div className="vis">{s.visual}</div>
        </div>
        <div className="gg-deck-nav">
          <button className="gg-ghost" onClick={() => go(i - 1)} disabled={i === 0}>← back</button>
          <div className="gg-dots">
            {slides.map((x, j) => <button key={x.k} className={j === i ? 'on' : ''} onClick={() => go(j)} aria-label={`slide ${j + 1}: ${x.k}`} />)}
          </div>
          <button className="gg-ghost" onClick={() => go(i + 1)} disabled={i === count - 1}>next →</button>
        </div>
      </div>
    </section>
  )
}
