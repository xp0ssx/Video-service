import { useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Link,
  NavLink,
  useLocation,
} from "react-router-dom";
import {
  AuthProvider,
  AuthRecovery,
  Account,
  AuthForm,
  useAuth,
} from "../features/auth/Auth";
import { StudioPage, UploadPage, WatchPage } from "../features/videos/Videos";
import { SearchBar } from "../features/videos/SearchBar";
import {
  ChannelPage,
  SubscriptionsPage,
  PlaylistPage,
  PlaylistStudio,
} from "../features/channels/Channels";
import { HomePage } from "../pages/HomePage";
import { ModerationPage, AdminPage } from "../features/moderation/Moderation";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { ThemeProvider } from "./Theme";
function Shell() {
  const location = useLocation(),
    { session } = useAuth();
  const [collapsed, setCollapsed] = useState(false),
    [drawer, setDrawer] = useState(false),
    [search, setSearch] = useState(false);
  const [narrow, setNarrow] = useState(
    () => matchMedia("(max-width: 1100px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width: 1100px)");
    const sync = () => {
      setNarrow(media.matches);
      if (!media.matches) setDrawer(false);
    };
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  const menuButton = useRef<HTMLButtonElement>(null),
    side = useRef<HTMLElement>(null);
  useEffect(() => {
    setDrawer(false);
    setSearch(false);
    setCollapsed(location.pathname.startsWith("/watch/"));
  }, [location.pathname]);
  useEffect(() => {
    if (!drawer) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    side.current?.querySelector<HTMLAnchorElement>("a")?.focus();
    return () => {
      document.body.style.overflow = previous;
    };
  }, [drawer]);
  function close() {
    setDrawer(false);
    requestAnimationFrame(() => menuButton.current?.focus());
  }
  const links: [string, string, IconName][] = [
    ["/", "Главная", "home"],
    ["/subscriptions", "Подписки", "subscriptions"],
    ["/studio/playlists", "Плейлисты", "playlist"],
    ["/studio", "Studio", "studio"],
    ["/upload", "Загрузить", "upload"],
  ];
  if (["moderator", "admin"].includes(session?.user.role || ""))
    links.push(["/moderation", "Модерация", "shield"]);
  if (session?.user.role === "admin")
    links.push(["/admin", "Администрирование", "settings"]);
  return (
    <div
      className={`app-shell${collapsed ? " sidebar-collapsed" : ""}${drawer ? " drawer-open" : ""}${search ? " search-open" : ""}`}
    >
      <a className="skip-link" href="#main">
        К содержимому
      </a>
      <header
        className="topbar"
        inert={drawer}
        onKeyDown={(event) => {
          if (search && event.key === "Escape") {
            setSearch(false);
            requestAnimationFrame(() =>
              document
                .querySelector<HTMLButtonElement>(".mobile-search")
                ?.focus(),
            );
          }
        }}
      >
        <div className="brand-group">
          <button
            ref={menuButton}
            className="icon-button nav-toggle"
            aria-label="Меню навигации"
            aria-expanded={narrow ? drawer : !collapsed}
            aria-controls="navigation"
            onClick={() => {
              if (narrow) setDrawer((v) => !v);
              else setCollapsed((v) => !v);
            }}
          >
            <Icon name="menu" />
          </button>
          <Link className="brand" to="/" aria-label="Video-service — главная">
            <span className="brand-mark" aria-hidden="true">
              <svg viewBox="0 0 32 32">
                <path d="m4 6 8 20h8L28 6h-8l-4 12-4-12Z" fill="currentColor" />
              </svg>
            </span>
            <span>Video-service</span>
          </Link>
        </div>
        <button
          className="icon-button close-search"
          aria-label="Закрыть поиск"
          onClick={() => setSearch(false)}
        >
          <Icon name="close" />
        </button>
        <SearchBar />
        <div className="header-actions">
          <button
            className="icon-button mobile-search"
            aria-label="Открыть поиск"
            onClick={() => {
              setSearch(true);
              requestAnimationFrame(() =>
                document
                  .querySelector<HTMLInputElement>(".search-bar input")
                  ?.focus(),
              );
            }}
          >
            <Icon name="search" />
          </button>
          <Link className="header-upload" to="/upload">
            <Icon name="plus" />
            <span>Загрузить</span>
          </Link>
          <Account />
        </div>
      </header>
      {drawer && (
        <button
          className="nav-backdrop"
          aria-label="Закрыть навигацию"
          onClick={close}
        />
      )}
      <aside
        ref={side}
        id="navigation"
        className="sidebar"
        aria-label="Навигация"
        role={drawer ? "dialog" : undefined}
        aria-modal={drawer || undefined}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            close();
            return;
          }
          if (drawer && e.key === "Tab") {
            const nodes = Array.from(
              side.current!.querySelectorAll<
                HTMLAnchorElement | HTMLButtonElement
              >("a,button"),
            ).filter((node) => node.getClientRects().length > 0);
            const first = nodes[0],
              last = nodes.at(-1);
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last?.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <button
          className="icon-button drawer-close"
          onClick={close}
          aria-label="Закрыть меню"
        >
          <Icon name="close" />
        </button>
        <nav>
          {links.map(([to, label, icon]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/" || to === "/studio"}
              className={({ isActive }) =>
                `nav-item${isActive ? " active" : ""}`
              }
              title={label}
            >
              <Icon name={icon} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <p>Video-service</p>
          <a href="/docs">Документация API</a>
        </div>
      </aside>
      <main id="main" className="main-content" inert={drawer}>
        <AuthRecovery />
        <Routes>
          <Route path="/moderation" element={<ModerationPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/channels/:username" element={<ChannelPage />} />
          <Route path="/subscriptions" element={<SubscriptionsPage />} />
          <Route path="/playlists/:id" element={<PlaylistPage />} />
          <Route path="/studio/playlists" element={<PlaylistStudio />} />
          <Route path="/studio/playlists/:id" element={<PlaylistStudio />} />
          <Route path="/results" element={<HomePage results />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/studio" element={<StudioPage />} />
          <Route path="/watch/:id" element={<WatchPage />} />
          <Route path="/" element={<HomePage />} />
          <Route path="/login" element={<AuthForm key="login" />} />
          <Route
            path="/register"
            element={<AuthForm key="register" register />}
          />
          <Route
            path="*"
            element={
              <section className="empty-state">
                <h1>Страница не найдена</h1>
                <Link to="/">На главную</Link>
              </section>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
export function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <Shell />
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
