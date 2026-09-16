import { useState } from 'react'
import type { FeedData, StrategyView } from '../api'
import { Flash, shortName, signed } from './lib'

/** Strategy cards: five columns, the fifth one adds a strategy. The id sits in the
 *  top row with the status dot and the toggle, the name gets its own line so it never truncates, and an enabled
 *  strategy carries a mint ring — the toggle alone was not legible enough. */
export function StrategyCards({ views, onBook, onEdit, onToggle, onNew }: {
  views: StrategyView[]
  onBook: (id: string) => void; onEdit: (id: string) => void; onToggle: (v: StrategyView) => void; onNew: () => void
}) {
  const [all, setAll] = useState(false)
  const CAP = 8
  const shown = all ? views : views.slice(0, CAP)
  return (
    <section className="wrap gg-strats" id="strategies">
      <div className="gg-sec-head">
        <span className="gg-h">your strategies</span>
        <span className="gg-sub">real launches, paper money · each strategy runs its own book</span>
        <span className="r">
          <span className="mono gg-sub">{views.length} strategies</span>
        </span>
      </div>
      <div className="gg-cards">
        {shown.map(v => {
          const b = v.book, on = v.enabled
          const pnlText = b.closed ? signed(b.pnl_pct, 2, '%') : '0.00%'
          const pnlCls = b.pnl_pct > 0 ? 'mint' : b.pnl_pct < 0 ? 'red' : ''
          const win = b.closed ? Math.round((100 * b.wins) / b.closed) + '%' : '—'
          return (
            <div key={v.id} className={'gg-card' + (on ? ' on' : '')} onClick={() => onBook(v.id)}>
              <div className="top">
                <span className={'sd' + (on ? ' on' : '')} />
                <span className="gg-lbl">{v.id}</span>
                <button className={'gg-sw' + (on ? ' on' : '')} aria-label={on ? 'pause strategy' : 'run strategy on paper'} title={on ? 'running · click to pause' : 'paused · click to run on paper'}
                  onClick={e => { e.stopPropagation(); onToggle(v) }}><i /></button>
              </div>
              <div className={'nm' + (on ? ' on' : '')} title={v.name}>{shortName(v)}</div>
              <div className="mid">
                <Flash value={pnlText} className={'pnl ' + pnlCls} />
                <button className="gg-mini" onClick={e => { e.stopPropagation(); onEdit(v.id) }}>edit</button>
              </div>
              <div className="stats">
                {([['equity', b.equity.toFixed(3) + ' Ξ'], ['win', win], ['trades', String(b.closed)], ['open', String(b.open)]] as const).map(([l, val]) => (
                  <span className="stat" key={l}><span className="l">{l}</span><Flash value={val} className="v" /></span>
                ))}
              </div>
            </div>
          )
        })}
        {/* the fifth card: it forks whichever strategy is selected and leaves the copy paused */}
        <button className="gg-card gg-card-new" onClick={onNew} disabled={!views.length}>
          <span className="gg-lbl">new</span>
          <span className="t">+ new strategy</span>
          <span className="b">forks the strategy you have selected and starts it paused</span>
        </button>
      </div>
      {views.length > CAP && <button className="gg-more" onClick={() => setAll(!all)}>{all ? 'show less' : `show all ${views.length} strategies`}</button>}
    </section>
  )
}

export function Rail({ feed }: { feed: FeedData | null }) {
  const p = feed?.pulse
  const tiles: [string, number | undefined, string][] = [
    ['launches / 5 min', p?.launches_5m, ''],
    ['launches / hour', p?.launches_1h, ''],
    ['graduations / hour', p?.grads_1h, ''],
    ['buys / hour', p?.buys_1h, ''],
  ]
  return (
    <section className="gg-band">
      <div className="wrap">
        {tiles.map(([l, v, suf]) => (
          <span className="gg-band-item" key={l}>
            <span className="gg-lbl">{l}</span>
            <Flash className="v" value={v == null ? '—' : Math.round(v).toLocaleString('en') + suf} />
          </span>
        ))}
      </div>
    </section>
  )
}
