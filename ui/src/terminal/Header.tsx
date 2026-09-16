import type { MouseEvent } from 'react'
import type { Config, FeedData } from '../api'
import { LINKS } from '../links'

export type Page = 'home' | 'how it works' | 'docs'
export const PAGES: Page[] = ['home', 'how it works', 'docs']
// every page has its own path: the terminal is the root
export const PATH: Record<Page, string> = { home: '/', 'how it works': '/how-it-works', docs: '/docs' }
export const TITLE: Record<Page, string> = { home: 'gradgate // terminal', 'how it works': 'how it works · gradgate', docs: 'docs · gradgate' }

// what the nav shows where it differs from the page key
const LABEL: Partial<Record<Page, string>> = { home: 'terminal' }

const clean = (path: string) => path.replace(/\/+$/, '') || '/'
export function pageFromPath(path = location.pathname): Page {
  return PAGES.find(p => PATH[p] === clean(path)) ?? 'home'
}
/** Where the current address belongs, if not where it is: unknown paths go to the terminal. */
export function canonicalUrl(): string | null {
  const page = pageFromPath()
  return PATH[page] === clean(location.pathname) ? null : PATH[page] + location.search + location.hash
}
/** A plain click on a link navigates in place; cmd/ctrl/shift/middle clicks keep the browser's own behaviour. */
export function navClick(e: MouseEvent, go: () => void) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  e.preventDefault(); go()
}

export function Header({ page, onPage, feed, err, cfg }: {
  page: Page; onPage: (p: Page, hash?: string) => void; feed: FeedData | null; err: boolean; cfg: Config | null
}) {
  const warming = !!feed?.warming
  const off = err || (!!feed && !warming && (feed.head_age_s ?? 99) > 20)
  const status = err ? 'engine offline'
    : warming ? `warming up · ${(feed?.phase ?? '').slice(0, 22)}`
    : feed?.head ? `live · head ${feed.head.toLocaleString('en')} · ${feed.head_age_s ?? '—'}s`
    : 'connecting…'
  return (
    <header className="gg-hdr">
      <div className="wrap">
        {/* the mascot rides the wordmark: frame 2 of the sprite strip, pinned — it does not animate in the header */}
        <a className="gg-logo" href={PATH.home} onClick={e => navClick(e, () => onPage('home'))}>
          <span className="gg-sprite" aria-hidden="true" />
          <span className="gg-mark"><span className="t">gradgate</span><span className="c">_</span></span>
        </a>
        <nav className="gg-nav">
          {PAGES.map(p => (
            <a key={p} href={PATH[p]} className={page === p ? 'on' : ''} onClick={e => navClick(e, () => onPage(p))}>
              {page === p && <span className="sl">//</span>}
              {LABEL[p] ?? p}
            </a>
          ))}
        </nav>
        <span className="gg-live"><span className={'gg-dot' + (off ? ' off' : warming ? ' warm' : '')} />{status}</span>
        {/* paper unless a key is set: live orders need RH_PRIVATE_KEY and a strategy switched to "mode": "live" */}
        {cfg && (cfg.live
          ? <span className="gg-mode live" title="RH_PRIVATE_KEY is set: strategies with mode live trade real money">live armed</span>
          : <span className="gg-mode" title={cfg.live_reason || 'paper only'}>paper</span>)}
        {LINKS.github && <a className="gg-ghost" href={LINKS.github} target="_blank" rel="noreferrer">github</a>}
        <a className="gg-cta" href={PATH.docs + '#strategies'} onClick={e => navClick(e, () => onPage('docs', 'strategies'))}><span className="d" />write a strategy</a>
      </div>
    </header>
  )
}
