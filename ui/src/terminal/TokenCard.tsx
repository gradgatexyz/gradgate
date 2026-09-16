import { useEffect, useState, type ReactNode } from 'react'
import { api, type FeedRow, type StrategyTake, type TokenDetail } from '../api'
import { ago, fmtTax, meter, short, shortName, signed } from './lib'

/** Coin card: information only, no chart. Fixed 400 px and sticky, so it stays in
 *  view for the whole length of the feed. The name owns its line; ticker, pair chip and the verdict badge sit under it;
 *  the contract is a field with a copy button that answers back; the facts grid never runs off the right edge.
 *  Polls /api/token every 4 s; "last trade" ticks every second. */
export function TokenCard({ row }: { row: FeedRow | null }) {
  const [detail, setDetail] = useState<TokenDetail | null>(null)
  // which contract was copied, so switching coin clears the answer-back without an effect
  const [copiedTok, setCopiedTok] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const token = row?.token

  useEffect(() => {
    if (!token) return
    let alive = true
    const tick = async () => {
      try { const d = await api.token(token); if (alive) setDetail(d) }
      catch { /* launched before the engine's window: the feed row is all we have */ }
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(id) }
  }, [token])
  useEffect(() => { const id = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(id) }, [])

  if (!row) return <div className="gg-box"><div className="gg-empty">pick a launch in the feed</div></div>

  const d = detail && detail.token === row.token ? detail : null
  const x: FeedRow = d ?? row
  const copied = copiedTok === x.token
  // amounts are in the pair's quote asset: Ξ for ETH pairs, the ticker otherwise (USDG, NVDA…)
  const unit = x.quote === 'ETH' ? 'Ξ' : x.quote
  const amt = (v: number | null | undefined) => v == null ? '?' : v >= 100 ? v.toFixed(1) : v >= 1 ? v.toFixed(2) : v >= 0.001 ? v.toFixed(3) : v > 0 ? v.toPrecision(2) : '0'
  const twins = x.farm_twins ?? 0
  const links: [string, string][] = [
    ['pons', `https://www.ponsfamily.com/launchpad/${x.token}`],
    ['explorer', `https://robinhoodchain.blockscout.com/token/${x.token}`],
    ...(x.x_url ? [['x', x.x_url] as [string, string]] : []),
    ...(x.site ? [['site', x.site] as [string, string]] : []),
    ...(x.graduated ? [['chart', `https://dexscreener.com/robinhood/${x.token}`] as [string, string]] : []),   // empty before graduation
  ]
  const copy = () => { navigator.clipboard?.writeText(x.token); setCopiedTok(x.token); setTimeout(() => setCopiedTok(null), 1600) }

  const facts: [string, ReactNode][] = [
    ['graduation', x.graduated
      ? <span key="g" className="blue">graduated · trading on uniswap</span>
      : <span key="g"><span className="meter">{meter(x.progress)}</span> {amt(x.net)} of {amt(x.threshold)} {unit}</span>],
    ['buyers', `${x.buyers} · ${x.smart} smart`],
    ['activity', x.buys + x.sells === 0 ? 'no trades yet'
      : `${x.trades_1m ?? 0} trades/min · last ${x.last_trade_ts ? ago(now - x.last_trade_ts) : '?'} ago`],
    ['dev buy', x.dev_buy == null ? '?' : x.dev_buy === 0 ? 'none' : `${x.creator_pct.toFixed(1)}% of supply · ${amt(x.dev_buy)} ${unit}`],
    ['snipers', `${x.snipe_pct.toFixed(1)}% of supply`],
    ['creator tax', x.creator_tax_bps == null ? '?' : fmtTax(x.creator_tax_bps)],
    ['fees go to', x.creator_tax_bps == null ? '?' : x.fee_third_party ? <span key="f" className="amber">a third party</span> : 'the deployer'],
    ['bundle', x.exempt_n == null ? '?' : x.exempt_n === 0 ? 'none' : `${x.exempt_n} exempt wallet${x.exempt_n > 1 ? 's' : ''}`],
    ['deployer', `${short(x.creator)} · ${x.creator_prior ?? '?'} prior, ${x.creator_grads ?? '?'} graduated`],
    ...(twins >= 1 ? [['launch farm', <span key="lf" className={twins >= 2 ? 'red' : 'amber'}>{twins + 1} launches with this fingerprint in 30 min</span>] as [string, ReactNode]] : []),
  ]
  const takes = d ? d.strategies : null

  return (
    <div className="gg-box gg-tok">
      <div className="gg-tok-top">
        <span className="nm" title={x.name}>{x.name || '(unnamed)'}</span>
        {x.graduated && <span className="gg-chip graduated">graduated</span>}
      </div>
      <div className="gg-tok-sub">
        <span className="sy">{x.symbol ? '$' + x.symbol.replace(/^\$+/, '') : short(x.token)}</span>
        <span className="sep">/</span>
        <span className="pair">{x.quote}</span>
      </div>

      <div className={'gg-ca' + (copied ? ' ok' : '')}>
        <span className="p">ca</span>
        <span className="a">{x.token}</span>
        <button onClick={copy} aria-label="copy the contract address">
          {copied ? <><Glyph name="check" />copied</> : <><Glyph name="copy" />copy</>}
        </button>
      </div>

      <div className="gg-links">
        {links.map(([label, href]) => (
          <a key={label} className="gg-link" href={href} target="_blank" rel="noreferrer"><Glyph name={label} />{label}</a>
        ))}
      </div>

      <div className="gg-facts">
        {facts.map(([k, v]) => <div className="f" key={k}><span className="k">{k}</span><span className="v">{v}</span></div>)}
      </div>

      <div className="gg-lbl" style={{ margin: '14px 0 8px' }}>your strategies</div>
      <Takes takes={takes} />

    </div>
  )
}

/** Stroke-only icons, 13px, currentColor. */
function Glyph({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    pons: <><path d="M3 12h18" /><path d="M6 12a6 6 0 0 1 12 0" /><path d="M12 6v12" /></>,
    explorer: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
    x: <><path d="M4 4l16 16" /><path d="M20 4 4 20" /></>,
    site: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18" /></>,
    chart: <><path d="M4 19V5" /><path d="M4 19h16" /><path d="m8 15 4-5 3 3 4-6" /></>,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></>,
    check: <path d="m5 13 4 4 10-10" />,
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name] ?? paths.site}
    </svg>
  )
}

const STAGE: Record<StrategyTake['stage'], [string, string]> = {
  ready: ['would buy', 'mint'], waiting: ['waiting', 'amber'], no: ['not a fit', 'dim'], held: ['holding', 'head'],
  traded: ['traded', 'head'], graduated: ['graduated', 'blue'], 'no buys': ['no buys yet', 'dim'],
}

/** Every strategy's take on this coin, one line each; a line opens its full checklist. */
function Takes({ takes }: { takes: StrategyTake[] | null }) {
  const [open, setOpen] = useState<string | null>(null)
  if (!takes) return <div className="gg-empty" style={{ padding: '18px 0' }}>reading the strategies…</div>
  if (!takes.length) return <div className="gg-empty" style={{ padding: '18px 0' }}>no strategies yet</div>
  return (
    <div className="gg-takes">
      {takes.map(s => {
        const [label, cls] = STAGE[s.stage]
        const key = s.blocked[0] ?? s.failed[0]
        const it = s.items.find(i => i.k === key)
        const why = s.stage === 'held' ? `${signed(s.pnl_pct ?? 0, 1, '%')}${s.venue === 'pool' ? ' · in the uniswap pool' : ''}`
          : s.stage === 'traded' ? [s.pnl_pct == null ? '' : signed(s.pnl_pct, 1, '%'), s.reason ?? ''].filter(Boolean).join(' · ')
          : it ? `${it.k} ${it.v}, wants ${it.want}` : ''
        const isOpen = open === s.id
        return (
          <div key={s.id} className={'gg-take' + (s.enabled ? '' : ' off')}>
            <button className="row" onClick={() => setOpen(isOpen ? null : s.id)} disabled={!s.items.length} aria-expanded={isOpen}>
              <span className="id">{s.id}</span>
              <span className="nm">{shortName(s)}</span>
              <span className={'st ' + cls}>{label}{s.enabled ? '' : ' · paused'}</span>
            </button>
            {why && <div className="why">{why}</div>}
            {isOpen && (
              <div className="gg-checks">
                {s.items.map(c => (
                  <div className="gg-check" key={c.k}>
                    <span className={'m ' + (c.ok ? 'mint' : 'red')}>{c.ok ? 'ok' : 'fail'}</span>
                    <span className="k">{c.k}</span>
                    <span className="v">{c.v}</span>
                    <span className="w">{c.want}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
