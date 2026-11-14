import { HomePage } from '../pages/HomePage'

export function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">К содержимому</a>
      <header className="topbar">
        <a className="brand" href="/" aria-label="Video-service — главная">
          <span className="brand-mark" aria-hidden="true">▶</span>
          <span>Video-service</span>
        </a>
        <a className="docs-link" href="/docs">API Docs ↗</a>
      </header>
      <aside className="sidebar" aria-label="Навигация">
        <nav>
          <a className="nav-item active" href="/" aria-current="page">
            <span aria-hidden="true">⌂</span> Главная
          </a>
        </nav>
        <p className="sidebar-note">Смотрите. Делитесь.<br />Находите своё.</p>
      </aside>
      <main id="main" className="main-content">
        <HomePage />
      </main>
    </div>
  )
}
