import type { CSSProperties } from 'react'
import type { FeedRow } from '../api'

/** Row shading for the live feed, strategy-free: how alive a launch is and whether it carries red flags.
 *  One scale (chosen over a two-axis and a minimal variant): a background wash from
 *  red (likely scam) through nothing to mint (top), quiet rows dimmed. */

export type Tier = 'top' | 'interesting' | 'neutral' | 'caution' | 'scam' | 'dead' | 'graduated'

/** Red flags anyone can read off the launch (thresholds from the 14-day tape):
 *  level 2 = likely scam (one severe flag or two flags), 1 = caution, 0 = clean. */
export function riskOf(r: FeedRow): 0 | 1 | 2 {
  let flags = 0, severe = false
  if (r.snipe_pct > 30) severe = true; else if (r.snipe_pct > 15) flags++
  // a dev selling (this launch or past ones) is not a red flag : not counted
  if ((r.creator_prior ?? 0) >= 5) flags++                          // serial launcher
  // launch farm: 2+ twins in 30 min; only with a dev buy, since bare template launches share one empty fingerprint by the hundred
  if ((r.farm_twins ?? 0) >= 2 && (r.dev_buy ?? 0) > 0) flags++
  if ((r.exempt_n ?? 0) >= 3) flags++                               // declared bundle
  if ((r.creator_tax_bps ?? 0) > 300) flags++
  if (r.dev_buy != null && r.threshold > 0 && r.dev_buy / r.threshold > 0.12) flags++
  return severe || flags >= 2 ? 2 : flags === 1 ? 1 : 0
}

/** 0..1: recency of the last trade (full under 10 s, gone by 3 min), trades in the last minute, curve filled in the last minute. */
export function heatOf(r: FeedRow, now: number): number {
  if (r.buys === 0) return 0
  const idle = Math.max(0, now - (r.last_trade_ts ?? r.ts))
  const recency = idle <= 10 ? 1 : Math.max(0, 1 - (idle - 10) / 170)
  const pace = Math.min(1, Math.log1p(r.trades_1m ?? 0) / Math.log1p(40))
  const flow = Math.min(1, Math.max(0, (r.flow_1m ?? 0) / 10))      // 10 % of the curve in a minute counts as full
  return 0.5 * recency + 0.3 * pace + 0.2 * flow
}

export function tierOf(r: FeedRow, now: number): Tier {
  if (r.graduated) return 'graduated'
  const risk = riskOf(r)
  if (risk === 2) return 'scam'
  if (r.buys > 0 && now - (r.last_trade_ts ?? r.ts) > 180) return 'dead'
  if (risk === 1) return 'caution'
  const h = heatOf(r, now)
  if (h >= 0.6 && ((r.smart >= 1 && r.smart <= 4) || (r.flow_1m ?? 0) > 3)) return 'top'
  if (h >= 0.35) return 'interesting'
  return 'neutral'
}

const RGB = { mint: '46, 232, 154', amber: '255, 176, 32', red: '255, 93, 74', blue: '124, 140, 255' }
const rgba = (c: keyof typeof RGB, a: number) => `rgba(${RGB[c]}, ${a})`

// Same colours, carrying further : the wash was too faint to read against the dot grid.
const TINT: Record<Tier, string> = {
  top: rgba('mint', 0.24), interesting: rgba('mint', 0.11), neutral: 'transparent', caution: rgba('amber', 0.15),
  scam: rgba('red', 0.21), dead: 'transparent', graduated: rgba('blue', 0.16),
}

/** CSS variables for one row: --o (text brightness) and --tint (background wash). */
export function rowStyle(r: FeedRow, now: number): CSSProperties {
  const t = tierOf(r, now)
  const quiet = r.buys === 0 || t === 'dead'
  // 0.4 buried every launch nobody had bought yet — and with `all` as the default filter that is most of the tape.
  // Quiet still reads quieter, but it stays legible rather than fading into the background.
  return { '--o': quiet ? '0.82' : t === 'scam' ? '0.92' : '1', '--tint': TINT[t] } as CSSProperties
}
