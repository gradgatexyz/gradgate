import { useRef, type CSSProperties } from 'react'
import type { Strategy, StrategyView } from '../api'

/* ---- formatting ---- */
/** Seconds as the feed reads them: 42s · 2m 14s · 1h 05m. */
export function ago(s: number) {
  if (!Number.isFinite(s)) return '—'
  s = Math.max(0, Math.floor(s))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}
export function hhmmss(ts: number) { return new Date(ts * 1000).toISOString().slice(11, 19) }
export function hhmm(ts: number) { return new Date(ts * 1000).toISOString().slice(11, 16) }
export function meter(pct: number) {
  const fill = Math.round(Math.min(100, Math.max(0, pct)) / 12.5)
  return '█'.repeat(fill) + '░'.repeat(8 - fill)
}
export function signed(n: number, d = 2, suffix = '') { return (n > 0 ? '+' : '') + n.toFixed(d) + suffix }
export function fmtTax(bps: number) { return `${+(bps / 100).toFixed(2)}%` }
export function short(a: string) { return a.slice(0, 6) + '…' + a.slice(-4) }

const SUB = '₀₁₂₃₄₅₆₇₈₉'
/** Tiny prices the way traders read them: 0.0₆3815 = 0.0000003815. */
export function tiny(v: number | null | undefined): string {
  if (v == null || !isFinite(v) || v <= 0) return '—'
  if (v >= 1) return v.toFixed(3)
  if (v >= 0.001) return v.toFixed(5)
  const [m, e] = v.toExponential(3).split('e-')
  const zeros = Number(e) - 1
  const digits = m.replace('.', '').replace(/0+$/, '').slice(0, 4) || '0'
  return '0.0' + String(zeros).split('').map(d => SUB[Number(d)]).join('') + digits
}

/* ---- strategies ---- */
export function strip(v: StrategyView | Strategy): Strategy {
  const { id, name, enabled, mode, size, cash, quote, entry, exit } = v as Strategy
  return { id, name, enabled, mode, size, cash, quote, entry: { ...entry }, exit: { ...exit } }
}
export function shortName(v: { id: string; name: string }) {
  const p = v.name.split(' · ')
  if (p[0] === v.id) p.shift()
  return p.slice(0, 2).join(' · ') || v.id
}

/* ---- a value that flashes once when it changes (design: ggflash .55s) ---- */
const PLACEHOLDER = new Set(['—', '…', '?', ''])
export function Flash({ value, className = '', style }: { value: string; className?: string; style?: CSSProperties }) {
  const first = useRef(value)
  // the first real number after a placeholder is a load, not a change: it must not flash
  if (PLACEHOLDER.has(first.current) && !PLACEHOLDER.has(value)) first.current = value
  return <span key={value} className={className + (value !== first.current ? ' flash' : '')} style={style}>{value}</span>
}

/* ---- price series from the engine's ticks: [ts, 'b'|'s', px, eth, is_creator] ---- */
export type Tick = { ts: number; side: string; px: number; eth: number; creator: boolean }
export type Bar = { o: number; h: number; l: number; c: number; v: number }

export function toTicks(raw: [number, string, number, number, boolean][] | undefined): Tick[] {
  return (raw ?? []).map(k => ({ ts: k[0], side: k[1], px: k[2], eth: k[3], creator: k[4] })).filter(t => t.px > 0)
}

/** n equal-time candles over the curve's life so far. */
export function toCandles(ticks: Tick[], n: number, nowTs: number): { bars: Bar[]; t0: number; t1: number } | null {
  if (ticks.length < 2) return null
  const t0 = ticks[0].ts, last = ticks[ticks.length - 1].ts
  const t1 = Math.max(last, Math.min(nowTs, last + (last - t0) * 0.1)) + 1
  const w = (t1 - t0) / n
  const bars: Bar[] = []
  let prev = ticks[0].px, j = 0
  for (let i = 0; i < n; i++) {
    const end = t0 + w * (i + 1)
    const b: Bar = { o: prev, h: prev, l: prev, c: prev, v: 0 }
    while (j < ticks.length && (ticks[j].ts < end || i === n - 1)) {
      const p = ticks[j].px
      b.h = Math.max(b.h, p); b.l = Math.min(b.l, p); b.c = p; b.v += ticks[j].eth; j++
    }
    bars.push(b); prev = b.c
  }
  return { bars, t0, t1 }
}

/** % move of the last price against the price `sec` seconds ago (or the first trade, if younger). */
export function changeOver(ticks: Tick[], sec: number, nowTs: number): number | null {
  if (!ticks.length) return null
  const last = ticks[ticks.length - 1].px
  const cut = nowTs - sec
  let ref = ticks[0].px
  for (const t of ticks) { if (t.ts <= cut) ref = t.px; else break }
  return ref > 0 ? (last / ref - 1) * 100 : null
}

/* ---- link icons from the design ---- */
export const ICON = {
  pons: 'M3 12.5 L6.5 6 L9.5 9.5 L13 3.5 M13 3.5 H9.8 M13 3.5 V6.7',
  chart: 'M2.5 13.5 V2.5 M2.5 13.5 H13.5 M5.5 11 V7 M8 11 V4.5 M10.5 11 V8.5',
  scan: 'M7 2.6 A4.4 4.4 0 1 0 7 11.4 A4.4 4.4 0 1 0 7 2.6 M10.4 10.4 L13.6 13.6',
  x: 'M3 3 L13 13 M13 3 L3 13',
  site: 'M8 2 A6 6 0 1 0 8 14 A6 6 0 1 0 8 2 M2 8 H14 M8 2 C10 4.5 10 11.5 8 14 C6 11.5 6 4.5 8 2',
  copy: 'M5.5 5.5 H12.5 V12.5 H5.5 Z M3.5 10.5 V3.5 H10.5',
}
