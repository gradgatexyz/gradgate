/** The engine's API (engine/, FastAPI). Relative paths: the engine serves this page itself, and Vite proxies /api in dev. */

export type Item = { k: string; ok: boolean; v: string; want: string }
export type FeedRow = {
  token: string; symbol: string; name: string; creator: string; block: number; age_s: number; ts: number
  buys: number; sells: number; buyers: number; sellers?: number; eth_in: number; eth_out: number; net: number; progress: number
  quote: string; threshold: number; snipe_pct: number; creator_pct: number; creator_sold_eth: number; graduated: boolean; smart: number
  px: number | null; peak_net: number; idle_s?: number
  image: string | null; x_url: string | null; site: string | null; tg_url?: string | null; description?: string
  creator_tax_bps?: number | null; fee_third_party?: boolean; exempt_n?: number | null; dev_buy?: number | null
  items: Item[]; failed: string[]; ready: boolean; held: boolean; done: boolean
  // strategy-free facts the feed shades rows with (indexer State.row_extra)
  creator_prior?: number; creator_dumps?: number; creator_grads?: number
  last_trade_ts?: number | null; trades_1m?: number; flow_1m?: number
  farm_twins?: number   // earlier launches in 30 min from other creators with the same launch fingerprint
}
export type FeedLists = { hot: FeedRow[]; near: FeedRow[]; graduated: FeedRow[]; counts?: { all: number; live: number } }
/** Sums over the engine's stored day (stats_minute, kept 24 h). `since` is the oldest minute it actually has. */
export type DayFate = { graduated: number; half: number; past_minute: number; dead: number }
export type DayPulse = { launches: number; grads: number; buys: number; eth_in: number; since: number; window_s: number; fate?: DayFate }
export type FeedData = {
  strategy: { id: string; name: string; mode: string | null } | null
  rows: FeedRow[]; now: number; head?: number; head_age_s?: number | null
  pulse?: { launches_1h: number; grads_1h: number; launches_5m: number; buys_1h: number; eth_in_1h: number; tracked: number }
  busy?: boolean; warming?: boolean; phase?: string
  for?: string   // the sid this answer was requested with (set client-side)
}

export type Entry = Record<string, number | boolean>
export type Exit = Record<string, number | boolean>
export type Strategy = {
  id: string; name: string; enabled: boolean; mode?: string | null; size: number; cash: number; quote: string
  entry: Entry; exit: Exit
}
export type Book = { cash: number; equity: number; start: number; open: number; closed: number; wins: number; pnl: number; pnl_pct: number; best: number; worst: number }
export type Position = { token: string; sym?: string; size: number; open_ts: number; entry_px: number; cur_px: number | null; age_s: number; pnl: number | null; live?: boolean; image?: string | null; tokens?: number; half_done?: boolean; venue?: 'curve' | 'pool'; quote?: string }
export type Closed = { token: string; sym: string; open_ts: number; close_ts: number; size: number; pnl: number; pnl_pct: number; reason?: string; close_reason?: string; live?: boolean | null; tx_in?: string | null; tx_out?: string | null }
export type StrategyView = Strategy & { owner?: string; book: Book; positions: Position[]; closed: Closed[]; decisions: { ts: number; token: string; sym: string; action: string; reason: string }[] }
export type StateData = {
  now: number; head: number; head_age_s: number | null; tick_ms: number; backfilled: boolean
  live: { wallet: string | null; allowed: [boolean, string]; kill: boolean }
  ws: { status: string; events: number; blocks: number; age_s: number | null }
  strategies: StrategyView[]
  pulse: Record<string, number>
}
/** One strategy's take on a launch: its checklist stage, or that it holds / already traded the coin. */
export type StrategyTake = {
  id: string; name: string; enabled: boolean
  stage: 'ready' | 'waiting' | 'no' | 'held' | 'traded' | 'graduated' | 'no buys'
  items: Item[]; failed: string[]; blocked: string[]
  pnl_pct?: number | null; venue?: 'curve' | 'pool'; reason?: string | null
}
export type TokenDetail = FeedRow & {
  strategies: StrategyTake[]
  creator_record: { launches: number; dumps: number; grads?: number; eth_out?: number; tokens: string[]; self_buy_launches?: number }
  trades: { kind: string; wallet: string; eth: number; tokens: number; block: number; ts: number }[]
  ticks: [number, string, number, number, boolean][]   // ts, 'b'|'s', px, eth, is_creator
  marks: { ts: number; strategy: string; action: string; reason?: string; px?: number }[]
  net_hist: [number, number][]
}
/** What the engine allows from here: editing strategies (only from the machine it runs on) and whether live mode is armed. */
export type Config = { local_edit: boolean; live: boolean; live_reason: string; database: boolean }

const headers = (json = false): Record<string, string> => (json ? { 'content-type': 'application/json' } : {})
async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => null) as { error?: string; detail?: string } | null
    throw new Error(body?.error ?? body?.detail ?? `${r.status}`)
  }
  return r.json() as Promise<T>
}
const get = async <T,>(url: string) => fetch(url).then(r => j<T>(r))
const send = async <T,>(url: string, method: string, body?: unknown) =>
  fetch(url, { method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) }).then(r => j<T>(r))

export const api = {
  feed: (sid: string, limit = 80) => get<FeedData>(`/api/feed?sid=${encodeURIComponent(sid)}&limit=${limit}`),
  state: () => get<StateData>('/api/state'),
  config: () => get<Config>('/api/config'),
  strategies: () => get<Strategy[]>('/api/strategies'),
  save: (s: Strategy) => send<Strategy>('/api/strategies', 'POST', s),
  remove: (id: string) => send<{ ok: boolean }>(`/api/strategies/${encodeURIComponent(id)}`, 'DELETE'),
  reset: (id: string) => send<{ ok: boolean }>(`/api/strategies/${encodeURIComponent(id)}/reset`, 'POST'),
  /** How many launches of the feed window a draft would pass right now; nothing is saved. */
  preview: (s: Strategy) => send<{ pass: number; near: number; total: number }>('/api/strategies/preview', 'POST', s),
  token: (addr: string) => get<TokenDetail>(`/api/token/${addr}`),
  /** The feed's last-hour lists (--hot, --near, --graduated) and the hour's launch counts for the filter badges. */
  lists: () => get<FeedLists & { now?: number }>('/api/feed/lists'),
  /** Sums over the last 24 h, read from the database. `null` when the engine runs without one. */
  day: () => get<DayPulse | null>('/api/pulse/day'),
}
