import { useEffect, useState } from 'react'
import { api, type FeedData, type FeedRow } from '../api'

/** The feed over server-sent events (GET /api/feed/stream):
 *  hello + the window's rows, then a `launch` the moment one lands, `rows` deltas once a second, a `tick` every 10 s.
 *  EventSource reconnects with its own backoff (a dead engine answers 502 through the proxy and the browser gives up, we do not);
 *  events are applied in one render per 40 ms; rows merge by token, newest first, 100 kept; `fresh` = younger than 30 s.
 *  If the stream never opens, polling GET /api/feed takes over. */
export function useFeed(sid: string) {
  const [feed, setFeed] = useState<FeedData | null>(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let alive = true, es: EventSource | null = null, wait = 1000, timer = 0, pollTimer = 0, opened = false
    let pending: FeedRow[] = []; let flush = 0
    const base = (d: Partial<FeedData>) => ({ strategy: null, rows: [], now: Math.floor(Date.now() / 1000), ...d, for: sid } as FeedData)
    const commit = () => {
      flush = 0; const batch = pending; pending = []
      setFeed(p => {
        const cur = p ?? base({})
        const seen = new Map(cur.rows.map(r => [r.token, r]))
        for (const r of batch) seen.set(r.token, r)
        const rows = [...seen.values()].sort((a, b) => b.ts - a.ts || b.block - a.block).slice(0, 100)
        return { ...cur, rows, for: sid }
      })
    }
    const push = (rows: FeedRow[]) => { pending.push(...rows); if (!flush) flush = window.setTimeout(commit, 40) }
    const open = () => {
      es = new EventSource(`/api/feed/stream?sid=${encodeURIComponent(sid)}`)
      es.onopen = () => { wait = 1000; opened = true; if (pollTimer) { clearInterval(pollTimer); pollTimer = 0 } }
      es.addEventListener('hello', m => {
        const d = JSON.parse((m as MessageEvent).data) as FeedData & { warming?: boolean }
        if (d.warming) { setFeed(base({ warming: true, phase: d.phase })); return }
        pending = []; setFeed(base({ ...d, warming: false })); setErr(false)
      })
      es.addEventListener('launch', m => push([JSON.parse((m as MessageEvent).data) as FeedRow]))
      es.addEventListener('rows', m => { const d = JSON.parse((m as MessageEvent).data) as { rows: FeedRow[]; head?: number; head_age_s?: number | null }; push(d.rows); setFeed(p => p ? { ...p, head: d.head ?? p.head, head_age_s: d.head_age_s ?? p.head_age_s } : p) })
      es.addEventListener('tick', m => {
        const d = JSON.parse((m as MessageEvent).data) as { warming?: boolean; phase?: string; head?: number; head_age_s?: number | null; pulse?: FeedData['pulse'] }
        setFeed(p => p ? { ...p, warming: !!d.warming, phase: d.phase, head: d.head ?? p.head, head_age_s: d.head_age_s ?? p.head_age_s, pulse: d.pulse ?? p.pulse } : base({ warming: !!d.warming, phase: d.phase }))
      })
      es.onerror = () => {
        es?.close(); es = null
        if (!alive) return
        if (!opened && !pollTimer) {            // the stream never came up (old engine, proxy): poll instead, keep trying the stream
          const poll = async () => { try { const d = await api.feed(sid, 100); if (alive && d && d.rows) { setFeed({ ...d, for: sid }); setErr(false) } } catch { if (alive) setErr(true) } }
          poll(); pollTimer = window.setInterval(poll, 1500)
        }
        if (opened) setErr(true)
        timer = window.setTimeout(open, wait); wait = Math.min(wait * 2, 30000)
      }
    }
    open()
    return () => { alive = false; clearTimeout(timer); clearTimeout(flush); if (pollTimer) clearInterval(pollTimer); es?.close() }
  }, [sid])
  return { feed, err }
}
