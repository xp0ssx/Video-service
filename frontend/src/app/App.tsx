import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider, Account, AuthForm } from '../features/auth/Auth'
import { StudioPage, UploadPage, WatchPage } from '../features/videos/Videos'
import { SearchBar } from '../features/videos/SearchBar'
import { HomePage } from '../pages/HomePage'

export function App() {
  return (
    <BrowserRouter><AuthProvider><div className="app-shell">
      <a className="skip-link" href="#main">К содержимому</a>
      <header className="topbar">
        <a className="brand" href="/" aria-label="Video-service — главная">
          <span className="brand-mark" aria-hidden="true">▶</span>
          <span>Video-service</span>
        </a>
        <SearchBar /><a className="header-upload" href="/upload">+ Загрузить</a><Account /><a className="docs-link" href="/docs">API Docs ↗</a>
      </header>
      <aside className="sidebar" aria-label="Навигация">
        <nav><a className="nav-item" href="/upload">↑ Загрузить</a><a className="nav-item" href="/studio">▣ Studio</a>
          <a className="nav-item" href="/">
            <span aria-hidden="true">⌂</span> Главная
          </a>
        </nav>
        <p className="sidebar-note">Смотрите. Делитесь.<br />Находите своё.</p>
      </aside>
      <main id="main" className="main-content">
        <Routes><Route path="/results" element={<HomePage results />} /><Route path="/upload" element={<UploadPage />} /><Route path="/studio" element={<StudioPage />} /><Route path="/watch/:id" element={<WatchPage />} /><Route path="/" element={<HomePage />} /><Route path="/login" element={<AuthForm key="login" />} /><Route path="/register" element={<AuthForm key="register" register />} /></Routes>
      </main>
    </div></AuthProvider></BrowserRouter>
  )
}
