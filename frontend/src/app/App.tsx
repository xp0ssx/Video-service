import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AuthProvider, Account, AuthForm } from '../features/auth/Auth'
import { StudioPage, UploadPage, WatchPage } from '../features/videos/Videos'
import { SearchBar } from '../features/videos/SearchBar'
import { ChannelPage, SubscriptionsPage, PlaylistPage, PlaylistStudio } from '../features/channels/Channels'
import { HomePage } from '../pages/HomePage'

import { ModerationPage, AdminPage, ModerationLinks } from '../features/moderation/Moderation'

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
        <nav><ModerationLinks/><a className="nav-item" href="/subscriptions">Подписки</a><a className="nav-item" href="/studio/playlists">Плейлисты</a><a className="nav-item" href="/upload">↑ Загрузить</a><a className="nav-item" href="/studio">▣ Studio</a>
          <a className="nav-item" href="/">
            <span aria-hidden="true">⌂</span> Главная
          </a>
        </nav>
        <p className="sidebar-note">Смотрите. Делитесь.<br />Находите своё.</p>
      </aside>
      <main id="main" className="main-content">
        <Routes><Route path="/moderation" element={<ModerationPage/>}/><Route path="/admin" element={<AdminPage/>}/><Route path="/channels/:username" element={<ChannelPage />} /><Route path="/subscriptions" element={<SubscriptionsPage />} /><Route path="/playlists/:id" element={<PlaylistPage />} /><Route path="/studio/playlists" element={<PlaylistStudio />} /><Route path="/studio/playlists/:id" element={<PlaylistStudio />} /><Route path="/results" element={<HomePage results />} /><Route path="/upload" element={<UploadPage />} /><Route path="/studio" element={<StudioPage />} /><Route path="/watch/:id" element={<WatchPage />} /><Route path="/" element={<HomePage />} /><Route path="/login" element={<AuthForm key="login" />} /><Route path="/register" element={<AuthForm key="register" register />} /></Routes>
      </main>
    </div></AuthProvider></BrowserRouter>
  )
}
