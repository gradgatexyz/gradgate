import { navClick, PATH, type Page } from './Header'
import { LINKS } from '../links'

/** The footer: it sits directly under the page content. Outside links come from links.ts and hide while empty. */
export function Footer({ onPage }: { onPage: (p: Page, hash?: string) => void }) {
  const inside: { title: string; links: [string, Page, string?][] }[] = [
    { title: 'terminal', links: [['feed', 'home', 'feed'], ['strategies', 'home', 'strategies']] },
    { title: 'learn', links: [['how it works', 'how it works'], ['docs', 'docs'], ['parameters', 'docs', 'parameters'], ['live trading', 'docs', 'live']] },
  ]
  const outside = ([['site', LINKS.site], ['github', LINKS.github], ['x', LINKS.x], ['telegram', LINKS.telegram]] as const).filter(([, u]) => u)
  return (
    <footer className="gg-foot">
      <div className="wrap top">
        <div className="brandcol">
          <div className="brand"><span className="gg-sprite" aria-hidden="true" />gradgate<span>_</span></div>
          <div className="about"># nothing here is advice. a verdict is a read, not a promise — results depend on your rules and the market.</div>
        </div>
        {inside.map(c => (
          <div className="col" key={c.title}>
            <div className="gg-lbl">{c.title}</div>
            {c.links.map(([label, to, hash]) => (
              <a key={label} href={PATH[to] + (hash ? '#' + hash : '')} onClick={e => navClick(e, () => onPage(to, hash))}>{label}</a>
            ))}
          </div>
        ))}
        {outside.length > 0 && (
          <div className="col">
            <div className="gg-lbl">elsewhere</div>
            {outside.map(([label, url]) => <a key={label} href={url} target="_blank" rel="noreferrer">{label}</a>)}
          </div>
        )}
      </div>
    </footer>
  )
}
