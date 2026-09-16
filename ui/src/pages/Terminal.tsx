import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Config, type FeedLists, type StateData, type StrategyView } from '../api'
import { useFeed } from '../terminal/useFeed'
import { Header, PATH, TITLE, canonicalUrl, pageFromPath, type Page } from '../terminal/Header'
import { Rail, StrategyCards } from '../terminal/StrategyCards'
import { FeedGrid, type Filter } from '../terminal/FeedGrid'
import { TokenCard } from '../terminal/TokenCard'
import { Sheet, type SheetState } from '../terminal/Sheet'
import { Footer } from '../terminal/Pages'
import { HowItWorks } from '../terminal/HowItWorks'
import { Docs } from '../terminal/Docs'
import { shortName, strip } from '../terminal/lib'
import { tierOf } from '../terminal/highlight'
import '../terminal/gg.css'

/** The terminal on your own engine: the live feed, the coin card and your strategies with their books. Strategies are
 *  data (engine/strategies.json); the editor writes that file through the engine, which accepts changes only from this
 *  machine. Paper by default; a strategy trades real money only with RH_PRIVATE_KEY set and "mode": "live". */
export default function Terminal() {
  // every page has its own address; old #pages and / are rewritten in place on load
  const [page, setPage] = useState<Page>(() => { const c = canonicalUrl(); if (c) history.replaceState(null, '', c); return pageFromPath() })
  // strategies are data (strategies.json): no id is known to the code; an empty sid means "the first one"
  const [sid, setSid] = useState<string>(() => { try { return localStorage.getItem('gg.sid') || '' } catch { return '' } })
  const [filter, setFilter] = useState<Filter>('all')
  const [selTok, setSelTok] = useState<string | null>(() => new URLSearchParams(location.search).get('token')?.toLowerCase() ?? null)
  // on a phone the coin card is a sheet from the bottom: picking a launch opens it, and it closes
  const [cardOpen, setCardOpen] = useState(false)
  const [state, setState] = useState<StateData | null>(null)
  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [cfg, setCfg] = useState<Config | null>(null)
  // the feed shows facts only, the same for everyone: which strategy is open in the editor does not change it
  const { feed, err } = useFeed('')
  // every filter looks at the last hour: the stream holds the 100 newest launches, the engine's lists hold the rest
  const [lists, setLists] = useState<FeedLists>({ hot: [], near: [], graduated: [] })
  useEffect(() => {
    let alive = true
    const tick = async () => { try { const d = await api.lists(); if (alive) setLists({ hot: d.hot ?? [], near: d.near ?? [], graduated: d.graduated ?? [], counts: d.counts }) } catch { /* engine busy or down: keep the last lists */ } }
    tick(); const id = setInterval(tick, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  useEffect(() => { const f = () => api.config().then(setCfg).catch(() => { /* engine not up yet */ }); f(); const id = setInterval(f, 15000); return () => clearInterval(id) }, [])

  // the engine takes changes only from the machine it runs on
  const canSave = !!cfg?.local_edit

  // the page owns the whole viewport while mounted
  useEffect(() => {
    const prev = document.body.style.background
    document.body.style.background = '#000'
    return () => { document.body.style.background = prev }
  }, [])

  const refresh = useCallback(async () => {
    try { setState(await api.state()) } catch { /* the header shows the outage */ }
  }, [])
  useEffect(() => { refresh(); const id = setInterval(refresh, 4000); return () => clearInterval(id) }, [refresh])
  useEffect(() => { try { localStorage.setItem('gg.sid', sid) } catch { /* private mode */ } }, [sid])
  useEffect(() => { const f = () => setPage(pageFromPath()); window.addEventListener('popstate', f); return () => window.removeEventListener('popstate', f) }, [])
  useEffect(() => { document.title = TITLE[page] }, [page])

  const goPage = (p: Page, hash = '') => {
    const url = PATH[p] + (p === 'home' ? location.search : '') + (hash ? '#' + hash : '')
    if (url !== location.pathname + location.search + location.hash) history.pushState(null, '', url)
    setPage(p)
    if (hash) requestAnimationFrame(() => document.getElementById(hash)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    else window.scrollTo({ top: 0 })
  }

  const views: StrategyView[] = state?.strategies ?? []
  const liveRows = feed?.rows ?? []
  const rows = liveRows

  // rows that arrived after the first batch slide in once (design: rowin, first 2 s of a launch on screen)
  const seen = useRef<Map<string, number>>(new Map())
  const booted = useRef(false)
  const nowMs = Date.now()
  for (const r of liveRows) if (!seen.current.has(r.token)) seen.current.set(r.token, booted.current ? nowMs : 0)
  if (liveRows.length) booted.current = true
  const isNew = (t: string) => nowMs - (seen.current.get(t) ?? 0) < 2000

  // --hot: the engine's busiest launches (5+ trades a minute), kept only when the row shading calls them green 
  const hotGreen = useMemo(() => lists.hot.filter(r => { const t = tierOf(r, Date.now() / 1000); return t === 'top' || t === 'interesting' }), [lists])
  // badges count the whole hour; live / all show its newest 100 launches
  const counts = useMemo(() => ({ all: lists.counts?.all ?? rows.length,
    hot: hotGreen.length, near: lists.near.length, graduated: lists.graduated.length }), [rows, lists, hotGreen])
  const visible = useMemo(() => filter === 'hot' ? hotGreen : filter === 'near' ? lists.near : filter === 'graduated' ? lists.graduated
    : rows, [rows, lists, hotGreen, filter])
  // default card: the newest launch that already trades (a launch with no buys has nothing to show)
  const selected = (selTok && (visible.find(r => r.token === selTok) || rows.find(r => r.token === selTok)
    || lists.hot.find(r => r.token === selTok) || lists.near.find(r => r.token === selTok) || lists.graduated.find(r => r.token === selTok)))
    || visible.find(r => r.buys >= 3) || visible[0] || null
  // pin the default pick, so the card does not jump to a newer launch on every feed update
  useEffect(() => { if (!selTok && selected) setSelTok(selected.token) }, [selTok, selected])

  const cur = views.find(v => v.id === sid)
  // the command line under the tape mirrors what is on screen: the strategy in the editor, its entry rule, the filter
  const cli = ['gradgate hunt',
    cur ? `--strategy ${cur.id}` : '',
    Number(cur?.entry?.min_buyers) > 0 ? `--min-buyers ${cur?.entry?.min_buyers}` : '',
    filter !== 'all' ? `--${filter}` : ''].filter(Boolean).join(' ')
  const empty = err ? 'the engine is not answering'
    : feed?.warming ? `the engine is warming up · ${feed.phase ?? 'backfilling the tape'} · a couple of minutes`
    : !feed ? 'connecting to the engine…'
    : filter === 'graduated' ? 'no graduations in the last hour'
    : filter === 'near' ? 'no curve is past 60% right now'
    : filter === 'hot' ? 'nothing trades 5+ times a minute right now'
    : 'waiting for the next launch'

  const openSheet = (id: string, tab: 'rules' | 'book') => {
    const v = views.find(x => x.id === id)
    if (!v) return
    setSid(id)
    setSheet({ mode: 'edit', base: strip(v), tab })
  }
  const onNew = () => {
    const base = cur ?? views[0]
    if (!base) return
    const id = 'U' + Date.now().toString(36).slice(-5).toUpperCase()
    setSheet({ mode: 'new', base: { ...strip(base), id, name: `${shortName(base)} fork`, enabled: false, mode: 'paper' }, tab: 'rules', forkOf: base.id })
  }
  const onEdit = (id: string) => {
    openSheet(id, 'rules')
  }
  const onToggle = async (v: StrategyView) => {
    if (!canSave) return                                       // switching a strategy on or off is a save
    try { await api.save({ ...strip(v), enabled: !v.enabled }) } finally { refresh() }
  }

  return (
    <div className="gg">
      <Header page={page} onPage={goPage} feed={feed} err={err} cfg={cfg} />

      {page === 'home' && <>
        <StrategyCards views={views} onBook={id => openSheet(id, 'book')} onEdit={onEdit} onToggle={onToggle} onNew={onNew} />
        <Rail feed={feed} />
        <section className="wrap gg-home" id="feed">
          <FeedGrid
            rows={visible} counts={counts} filter={filter} onFilter={setFilter}
            selected={selected?.token ?? null} onSelect={t => { setSelTok(t); setCardOpen(true) }} isNew={isNew} empty={empty} cli={cli}
          />
          <aside className={'gg-side' + (cardOpen ? ' open' : '')}>
            <button className="gg-card-close" onClick={() => setCardOpen(false)}>close</button>
            <TokenCard row={selected} />
          </aside>
        </section>
      </>}
      {page === 'how it works' && <HowItWorks pulse={feed?.pulse} onPage={goPage} />}
      {page === 'docs' && <Docs onPage={goPage} />}

      <Footer onPage={goPage} />

      {sheet && (
        <Sheet
          key={sheet.base.id + sheet.mode}
          st={sheet}
          view={sheet.mode === 'edit' ? views.find(v => v.id === sheet.base.id) : undefined}
          canSave={canSave}
          onClose={() => setSheet(null)}
          onSaved={id => { setSid(id); refresh() }}
          onDeleted={id => { if (sid === id) setSid(views.find(v => v.id !== id)?.id ?? ''); refresh() }}
        />
      )}
    </div>
  )
}
