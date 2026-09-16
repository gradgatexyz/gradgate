import { useEffect, useState } from 'react'
import { api, type Strategy, type StrategyView } from '../api'
import { hhmmss, signed } from './lib'

/** Strategy editor as a right-hand sheet: a drop zone for a strategy file above
 *  the rules, groups that read as config with the engine's own field names, a five-cell stepper row so the value and
 *  its unit stop sharing a cramped cell, and a two-row footer whose buttons never wrap.
 *
 *  `canSave`: a signed-in user (their own strategies) or a self-hosted engine (its house file). Otherwise the editor is
 *  a preview — every rule can be changed and the pass count follows — and saving is refused (another machine). */

export type SheetState = { mode: 'edit' | 'new'; base: Strategy; tab: 'rules' | 'book'; forkOf?: string }

type F = { k: string; step: number; unit?: string; kind?: 'bool'; min?: number }
// the engine keeps min_net/max_net, min_buy_sell_ratio, no_third_party, flow_reversal_pct and pre_grad_pct neutral;
// they stay out of the editor (docs list them as json only). Off: 0 for max_buyers / max_smart / max_creator_tax_bps,
// −1 for max_exempt / max_farm_twins.
const GROUPS: { label: string; part: 'entry' | 'exit'; fields: F[] }[] = [
  { label: '[entry.window]', part: 'entry', fields: [
    { k: 'min_age_s', step: 10, unit: 's' }, { k: 'max_age_s', step: 30, unit: 's' },
    { k: 'min_progress', step: 5, unit: '%' }, { k: 'max_progress', step: 5, unit: '%' },
  ] },
  { label: '[entry.flow]', part: 'entry', fields: [
    { k: 'min_buyers', step: 1 }, { k: 'max_buyers', step: 1 }, { k: 'min_vel', step: 0.5, unit: '/min' },
    { k: 'require_rising', step: 1, kind: 'bool' },
  ] },
  { label: '[entry.wallets]', part: 'entry', fields: [
    { k: 'min_smart', step: 1 }, { k: 'max_smart', step: 1 },
    { k: 'max_snipe_pct', step: 1, unit: '%' }, { k: 'max_creator_pct', step: 1, unit: '%' },
  ] },
  { label: '[entry.creator]', part: 'entry', fields: [
    { k: 'creator_max_prior', step: 1 }, { k: 'creator_min_grads', step: 1 }, { k: 'creator_no_dumps', step: 1, kind: 'bool' },
  ] },
  { label: '[entry.record]', part: 'entry', fields: [
    { k: 'max_creator_tax_bps', step: 50, unit: 'bps' }, { k: 'max_exempt', step: 1, min: -1 },
    { k: 'max_farm_twins', step: 1, min: -1 }, { k: 'require_socials', step: 1, kind: 'bool' },
  ] },
  { label: '[exit]', part: 'exit', fields: [
    { k: 'tp_pct', step: 10, unit: '%' }, { k: 'sl_pct', step: 5, unit: '%' }, { k: 'trail_pct', step: 5, unit: '%' },
    { k: 'timeout_s', step: 60, unit: 's' }, { k: 'partial_pct', step: 10, unit: '%' },
    { k: 'on_graduation', step: 1, kind: 'bool' }, { k: 'on_creator_sell', step: 1, kind: 'bool' },
  ] },
]

const dec = (step: number) => (step < 1 ? String(step).split('.')[1].length : 0)

export function Sheet({ st, view, canSave, onLogin, onClose, onSaved, onDeleted }: {
  st: SheetState; view?: StrategyView; canSave: boolean; onLogin?: () => void
  onClose: () => void; onSaved: (id: string) => void; onDeleted: (id: string) => void
}) {
  const [draft, setDraft] = useState<Strategy>(() => JSON.parse(JSON.stringify(st.base)))
  const [tab, setTab] = useState(st.tab)
  const [closing, setClosing] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const [before, setBefore] = useState<number | null>(null)
  const [now, setNow] = useState<{ pass: number; total: number } | null>(null)
  const dirty = st.mode === 'new' || JSON.stringify(draft) !== JSON.stringify(st.base)

  useEffect(() => {
    let alive = true
    api.preview(st.base).then(p => { if (alive && typeof p.pass === 'number') setBefore(p.pass) }).catch(() => {})
    return () => { alive = false }
  }, [st.base])
  useEffect(() => {
    let alive = true
    const id = setTimeout(() => {
      api.preview(draft).then(p => { if (alive && typeof p.pass === 'number') setNow({ pass: p.pass, total: p.total }) }).catch(() => {})
    }, 350)
    return () => { alive = false; clearTimeout(id) }
  }, [draft])

  const close = () => { if (closing) return; setClosing(true); setTimeout(onClose, 220) }
  useEffect(() => {
    const f = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', f)
    return () => window.removeEventListener('keydown', f)
  })

  const setVal = (part: 'entry' | 'exit', k: string, v: number | boolean) => setDraft(d => ({ ...d, [part]: { ...d[part], [k]: v } }))

  /** A strategy file — the same shape the engine writes. Keys it does not know are ignored and reported. */
  const loadFile = async (file: File | undefined) => {
    if (!file) return
    try {
      const raw = JSON.parse(await file.text()) as Record<string, unknown>
      const s = (Array.isArray(raw) ? raw[0] : raw) as Partial<Strategy> & Record<string, unknown>
      const ignored: string[] = []
      const merge = (part: 'entry' | 'exit') => {
        const out = { ...draft[part] }
        for (const [k, v] of Object.entries((s[part] ?? {}) as Record<string, unknown>)) {
          if (k in out && (typeof v === 'number' || typeof v === 'boolean')) out[k] = v
          else ignored.push(`${part}.${k}`)
        }
        return out
      }
      const entry = merge('entry'), exit = merge('exit')
      setDraft(d => ({ ...d, name: typeof s.name === 'string' && s.name ? s.name : d.name,
        size: Number.isFinite(Number(s.size)) && Number(s.size) > 0 ? Number(s.size) : d.size,
        quote: s.quote === 'ETH' ? 'ETH' : s.quote === 'any' ? 'any' : d.quote, entry, exit }))
      setMsg(ignored.length ? `loaded · ignored ${ignored.length} key${ignored.length > 1 ? 's' : ''} the engine does not know: ${ignored.slice(0, 3).join(', ')}${ignored.length > 3 ? '…' : ''}`
        : `loaded ${file.name}`)
    } catch (e) {
      setMsg(`that file did not parse: ${(e as Error).message.slice(0, 60)}`)
    }
  }

  const save = async () => {
    if (!canSave) { onLogin?.(); return }
    setBusy(true); setMsg(null)
    // the engine decides the id of a new strategy (a fork of a house one gets its own): use the one it returns
    try { const saved = await api.save(draft); onSaved(saved.id); close() } catch (e) { setMsg(`save failed: ${(e as Error).message}`) }
    setBusy(false)
  }
  const del = async () => {
    if (st.mode === 'new') { close(); return }
    if (!confirmDel) { setConfirmDel(true); return }
    setBusy(true)
    try { await api.remove(draft.id); onDeleted(draft.id); close() } catch (e) { setMsg(`delete failed: ${(e as Error).message}`) }
    setBusy(false)
  }

  const b = view?.book
  const closed = view?.closed ?? []
  const bestPct = closed.length ? Math.max(...closed.map(c => c.pnl_pct)) : null
  const worstPct = closed.length ? Math.min(...closed.map(c => c.pnl_pct)) : null
  const status = msg ?? (!canSave ? (onLogin ? 'preview · sign in to keep your copy' : 'preview · edit from the machine the engine runs on')
    : dirty ? 'unsaved changes' : draft.enabled ? 'saved · running on paper' : 'saved · paused')

  return (
    <>
      <div className={'gg-backdrop' + (closing ? ' out' : '')} onClick={close} />
      <aside className={'gg-sheet' + (closing ? ' out' : '')} aria-label="strategy editor">
        <div className="gg-sheet-top">
          <span className="nm">
            <span className="gg-lbl">name</span>
            <input value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} placeholder="strategy name" />
          </span>
          <span className="sub">{st.mode === 'new' ? `fork of ${st.forkOf ?? draft.id} · paused` : `${draft.id} · paper`}</span>
          <button className="gg-x" onClick={close} aria-label="close">✕</button>
        </div>

        <div className="gg-stabs">
          {(['rules', 'book'] as const).map(t => <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>)}
        </div>

        <div className="gg-sheet-body">
          {tab === 'rules' && <>
            <label className={'gg-drop' + (over ? ' over' : '')}
              onDragOver={e => { e.preventDefault(); setOver(true) }}
              onDragLeave={() => setOver(false)}
              onDrop={e => { e.preventDefault(); setOver(false); void loadFile(e.dataTransfer.files[0]) }}>
              <input type="file" accept="application/json,.json" hidden onChange={e => { void loadFile(e.target.files?.[0]); e.target.value = '' }} />
              <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 16V4" /><path d="M7.5 8.5 12 4l4.5 4.5" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" />
              </svg>
              <span className="t">drop strategy.json here</span>
              <span className="b">the same file the engine writes, so a strategy tuned in the repo lands here unchanged. keys it does not know are ignored and reported.</span>
              <span className="c">or browse · .json</span>
            </label>
            <div className="gg-or"><span className="ln" /><span className="gg-cap">or set the rules by hand</span><span className="ln" /></div>

            <div className="gg-grp">
              <div className="gl">[sizing]</div>
              <div className="fs">
                {/* any pair: sizes are ETH-equivalent (pair amount × 4.2 / its graduation threshold) */}
                <Num label="size" unit={draft.quote === 'ETH' ? 'Ξ' : 'Ξ eq'} step={0.001} v={draft.size} min={0} on={v => setDraft(d => ({ ...d, size: v }))} />
                <div className="gg-fld">
                  <span className="fl">pair =</span>
                  <button className="st" onClick={() => setDraft(d => ({ ...d, quote: 'ETH' }))}>−</button>
                  <input className="vv" readOnly value={draft.quote === 'ETH' ? 'eth' : 'any'} />
                  <span className="un" />
                  <button className="st" onClick={() => setDraft(d => ({ ...d, quote: 'any' }))}>+</button>
                </div>
              </div>
            </div>

            {GROUPS.map(g => (
              <div className="gg-grp" key={g.label}>
                <div className="gl">{g.label}</div>
                <div className="fs">
                  {g.fields.map(f => f.kind === 'bool'
                    ? (
                      <div className="gg-fld" key={f.k}>
                        <span className="fl">{f.k} =</span>
                        <button className="st" onClick={() => setVal(g.part, f.k, false)}>−</button>
                        <input className="vv" readOnly value={draft[g.part][f.k] ? 'on' : 'off'} />
                        <span className="un" />
                        <button className="st" onClick={() => setVal(g.part, f.k, true)}>+</button>
                      </div>
                    )
                    : <Num key={f.k} label={f.k} unit={f.unit} step={f.step} min={f.min ?? 0} v={Number(draft[g.part][f.k] ?? 0)} on={v => setVal(g.part, f.k, v)} />)}
                </div>
              </div>
            ))}
          </>}

          {tab === 'book' && <>
            {!b && <div className="gg-empty">no trades yet — save it and switch it on</div>}
            {b && <>
              <div className="gg-bstats">
                {[
                  ['equity', b.equity.toFixed(3) + ' Ξ', ''],
                  ['pnl', signed(b.pnl, 4) + ' Ξ', b.pnl > 0 ? 'mint' : b.pnl < 0 ? 'red' : ''],
                  ['pnl %', signed(b.pnl_pct, 1, '%'), b.pnl_pct > 0 ? 'mint' : b.pnl_pct < 0 ? 'red' : ''],
                  ['trades', String(b.closed), ''],
                  ['win rate', b.closed ? Math.round((100 * b.wins) / b.closed) + '%' : '—', ''],
                  ['best / worst', bestPct == null ? '—' : `${signed(bestPct, 0, '%')} / ${signed(worstPct ?? 0, 0, '%')}`, ''],
                ].map(([l, val, cls]) => <div className="gg-bstat" key={l}><div className="l">{l}</div><div className={'v ' + cls}>{val}</div></div>)}
              </div>

              <div className="gg-sec"><span className="gg-lbl">open</span><span className="n">{view?.positions.length ?? 0} · still moving</span></div>
              {(view?.positions.length ?? 0) === 0 && <div className="gg-empty" style={{ padding: '14px 0' }}>nothing open right now</div>}
              {view?.positions.map(p => {
                const pct = p.pnl == null ? null : (p.pnl / p.size) * 100
                return (
                  <div className="gg-pos" key={p.token}>
                    <div className="r1">
                      <span className="s">{p.sym ? '$' + p.sym.replace(/^\$+/, '') : p.token.slice(0, 8)} <span className="pair">/{p.quote ?? 'ETH'}</span></span>
                      <span className={'v ' + ((p.pnl ?? 0) >= 0 ? 'mint' : 'red')}>{p.pnl == null ? '—' : signed(p.pnl, 4) + ' Ξ'}</span>
                      <span className={'p ' + ((pct ?? 0) >= 0 ? 'mint' : 'red')}>{pct == null ? '' : signed(pct, 0, '%')}</span>
                    </div>
                    <div className="r2"><span className="why">{hhmmss(p.open_ts)} · {p.venue === 'pool' ? 'in the uniswap pool' : 'on the curve'}</span></div>
                  </div>
                )
              })}

              <div className="gg-sec"><span className="gg-lbl">closed</span><span className="n">{closed.length} · settled</span></div>
              {closed.length === 0 && <div className="gg-empty" style={{ padding: '14px 0' }}>no closed trades yet</div>}
              {closed.map((c, i) => (
                <div className="gg-pos closed" key={c.token + i}>
                  <div className="r1">
                    <span className="s">{c.sym ? '$' + c.sym.replace(/^\$+/, '') : c.token.slice(0, 8)} <span className="pair">/{'ETH'}</span></span>
                    <span className={'v ' + (c.pnl >= 0 ? 'mint' : 'red')}>{signed(c.pnl, 4)} Ξ</span>
                    <span className={'p ' + (c.pnl >= 0 ? 'mint' : 'red')}>{signed(c.pnl_pct, 0, '%')}</span>
                  </div>
                  <div className="r2"><span className="why" title={c.close_reason ?? c.reason}>{hhmmss(c.close_ts)} · {c.close_reason ?? c.reason}</span></div>
                </div>
              ))}
            </>}
          </>}
        </div>

        <div className="gg-sheet-foot">
          <div className="pass"><b>{now ? now.pass : '…'}</b> of {now ? now.total : '…'} launches in the feed pass · was {before ?? '…'}</div>
          <div className="row">
            <span className="hint" style={{ color: msg ? (msg.startsWith('loaded') ? 'var(--gg-mint)' : 'var(--gg-red)') : canSave && dirty ? 'var(--gg-amber)' : 'var(--gg-mute)' }}>{status}</span>
            {canSave && <button className={'gg-sbtn del' + (confirmDel ? ' confirm' : '')} onClick={del} disabled={busy}>{confirmDel ? 'delete for good' : 'delete'}</button>}
            <button className="gg-sbtn discard" onClick={close} disabled={busy}>close</button>
            {canSave
              ? <button className={'gg-sbtn save' + (dirty ? ' dirty' : '')} onClick={save} disabled={busy || !dirty}>{busy ? 'saving…' : 'save'}</button>
              : onLogin && <button className="gg-sbtn save dirty" onClick={save}>sign in to save</button>}
          </div>
        </div>
      </aside>
    </>
  )
}

function Num({ label, unit, step, v, min, on }: { label: string; unit?: string; step: number; v: number; min: number; on: (v: number) => void }) {
  const d = dec(step)
  const fix = (x: number) => Math.max(min, Number(x.toFixed(d)))
  return (
    <div className="gg-fld">
      <span className="fl">{label} =</span>
      <button className="st" onClick={() => on(fix(v - step))} aria-label={`decrease ${label}`}>−</button>
      <input className="vv" value={String(v)} onChange={e => { const n = parseFloat(e.target.value); if (!Number.isNaN(n)) on(fix(n)) }} />
      <span className="un">{unit ?? ''}</span>
      <button className="st" onClick={() => on(fix(v + step))} aria-label={`increase ${label}`}>+</button>
    </div>
  )
}
