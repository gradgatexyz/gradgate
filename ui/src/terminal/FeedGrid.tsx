import { useRef } from 'react'
import type { FeedRow } from '../api'
import { rowStyle } from './highlight'
import { ago, fmtTax } from './lib'

// `all` is the default: a launch nobody has bought yet is still movement, and the tape is worth more when it moves
export type Filter = 'all' | 'hot' | 'near' | 'graduated'
const FILTERS: Filter[] = ['all', 'hot', 'near', 'graduated']
type Flash = { kind: 'buy' | 'grad'; at: number }

/** The live feed as a terminal tape: every row mono, one column template shared by the header and the rows, and counts
 *  rather than pictures — age · launch · trades · buyers · smart · dev · snipe · tax. Filters are flags with `--all` as
 *  the default (a launch nobody has bought yet is still movement), and the command line under the tape mirrors them.
 *
 *  Rows are shaded by activity and red flags (highlight.ts), never by a strategy: the engine serves the feed without a
 *  checklist, so no row carries a verdict. Graduation lives in the coin card and in the row's blue wash. */
export function FeedGrid(p: {
  rows: FeedRow[]; counts: Record<Filter, number>
  filter: Filter; onFilter: (f: Filter) => void
  selected: string | null; onSelect: (token: string) => void; isNew: (token: string) => boolean
  empty: string; cli: string
}) {
  // events between two renders of the same row: a burst of buys, graduation (a dev selling is not an event worth a flash)
  const prev = useRef(new Map<string, { buys: number; grad: boolean }>())
  const flashes = useRef(new Map<string, Flash>())
  const nowMs = Date.now(), now = nowMs / 1000
  {
    for (const r of p.rows) {
      const was = prev.current.get(r.token)
      if (was) {
        const kind = !was.grad && r.graduated ? 'grad' : r.buys - was.buys >= 3 ? 'buy' : null
        if (kind) flashes.current.set(r.token, { kind, at: nowMs })
      }
      prev.current.set(r.token, { buys: r.buys, grad: r.graduated })
    }
    if (prev.current.size > 600) {
      const keep = new Set(p.rows.map(r => r.token))
      for (const k of prev.current.keys()) if (!keep.has(k)) { prev.current.delete(k); flashes.current.delete(k) }
    }
  }

  return (
    <div className="gg-feed">
      <div className="gg-feed-head">
        <span className="gg-h">live feed</span>
        <span className="meta">last 1h</span>
        <div className="f">
          {FILTERS.map(f => (
            <button key={f} className={'gg-filter' + (p.filter === f ? ' on' : '')} onClick={() => p.onFilter(f)}>
              --{f} <b>{p.counts[f]}</b>
            </button>
          ))}
        </div>
      </div>

      <div className="gg-scroll">
        <div className="gg-grid">
          {/* the header lives inside the scrolling tape: same container, same column widths, sticky at the top */}
          <div className="gg-tape">
            <div className="gg-cols gg-thead">
              <div>age</div><div>launch</div><div className="r">trades</div><div className="r">buyers</div><div className="r">smart</div>
              <div className="r">dev</div><div className="r">snipe</div><div className="r">tax</div>
            </div>
            {p.rows.map(r => {
              const sel = p.selected === r.token
              const f = flashes.current.get(r.token)
              const fl = f && nowMs - f.at < 1400 ? ' fl-' + f.kind : ''
              const vd = verdictOf(r)
              return (
                <div key={r.token} className={'gg-cols gg-row' + (sel ? ' sel' : '') + (p.isNew(r.token) ? ' row-in' : '') + fl}
                  style={rowStyle(r, now)} onClick={() => p.onSelect(r.token)}>
                  <div className="c-time">{ago(now - r.ts)}</div>
                  <div className="c-launch">
                    <span className="mk" style={{ color: sel ? 'var(--gg-head)' : vd?.[0] === 'ready' ? 'var(--gg-mint)' : 'transparent' }}>
                      {sel ? '▸' : vd?.[0] === 'ready' ? '›' : ' '}
                    </span>
                    <span className="nm">{r.name || '(unnamed)'}</span>
                    <span className="sy">{r.symbol ? '$' + r.symbol.replace(/^\$+/, '') : r.token.slice(0, 8)}</span>
                    {/* the pair is always named : creators pick ETH, USDG, stock tokens… */}
                    <span className="pr">/{r.quote}</span>
                  </div>
                  <div className="num">{r.buys + r.sells}</div>
                  {/* buyers and smart stay side by side, the way the tape has always read them */}
                  <div className="num">{r.buyers}</div>
                  <div className="num">{r.smart}</div>
                  <div className={'num ' + (r.creator_pct > 0 ? '' : 'dim')}>{r.creator_pct.toFixed(1)}%</div>
                  <div className={'num ' + (r.snipe_pct > 5 ? 'red' : 'dim')}>{r.snipe_pct.toFixed(1)}%</div>
                  <div className="num dim">{r.creator_tax_bps == null ? '?' : fmtTax(r.creator_tax_bps)}</div>
                </div>
              )
            })}
            {p.rows.length === 0 && <div className="gg-empty">{p.empty}</div>}
          </div>
        </div>
      </div>

      <div className="gg-cli"><span className="p">$</span><span className="t">{p.cli}</span><span className="caret" /></div>
    </div>
  )
}

/** [ready] mint · [near] amber · [skip] mute · [grad] brand; nothing when no strategy checked this launch. */
function verdictOf(r: FeedRow): [string, string] | null {
  if (r.graduated) return ['grad', 'blue']
  if (r.held) return ['held', 'head']
  if (r.ready) return ['ready', 'mint']
  if (r.failed.length === 1) return ['near', 'amber']
  return r.items.length ? ['skip', 'dim'] : null
}
