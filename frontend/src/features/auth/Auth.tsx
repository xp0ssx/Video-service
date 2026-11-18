import { Avatar, Icon } from "../../components/Icon";
import { ThemeSettings } from "../../app/Theme";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode, FormEvent } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";

type Session = {
  user: { id: string; username: string; role: string };
  csrf_token: string;
};
type AuthState = {
  session: Session | null;
  loading: boolean;
  update: (value: Session | null) => void;
};
const AuthContext = createContext<AuthState>({
  session: null,
  loading: true,
  update: () => {},
});
export const useAuth = () => useContext(AuthContext);
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request(path: string, init?: RequestInit) {
  const response = await fetch(`/api/auth/${path}`, {
    credentials: "same-origin",
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      response.status === 429
        ? "Слишком много попыток. Попробуйте позже."
        : body?.message || body?.detail || "Сервис недоступен",
    );
  }
  return response.status === 204 ? null : response.json();
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const revision = useRef(0);
  const update = useCallback((value: Session | null) => {
    revision.current += 1;
    setSession(value);
    setLoading(false);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const started = revision.current;
    const active = () =>
      !controller.signal.aborted && revision.current === started;
    request("session", { signal: controller.signal })
      .then((value) => {
        if (active()) setSession(value);
      })
      .catch(() => {
        if (active()) setSession(null);
      })
      .finally(() => {
        if (active()) setLoading(false);
      });
    return () => controller.abort();
  }, []);
  return (
    <AuthContext.Provider value={{ session, loading, update }}>
      {children}
    </AuthContext.Provider>
  );
}
export function Account() {
  const { session, loading, update } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  async function logout() {
    setBusy(true);
    setError("");
    try {
      await request("logout", {
        method: "POST",
        headers: { "X-CSRF-Token": session!.csrf_token },
      });
      update(null);
      navigate("/");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        update(null);
        navigate("/");
      } else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const location = useLocation();
  useEffect(() => {
    document.getElementById("account-menu")?.hidePopover();
  }, [location.pathname, location.search]);
  return (
    <div className="account">
      {!loading && !session && (
        <Link className="sign-in" to="/login">
          <Icon name="user" size={20} />
          <span>Войти</span>
        </Link>
      )}
      <button
        type="button"
        className="icon-button account-trigger"
        aria-label={session ? "Аккаунт" : "Настройки оформления"}
        popoverTarget="account-menu"
      >
        {session ? (
          <Avatar name={session.user.username} size={32} />
        ) : (
          <Icon name="settings" />
        )}
      </button>
      <div id="account-menu" className="account-menu" popover="auto">
        {session ? (
          <>
            <div className="account-identity">
              <Avatar name={session.user.username} size={40} />
              <div>
                <strong>{session.user.username}</strong>
                <Link to={`/channels/${session.user.username}`}>
                  {session.user.username}
                </Link>
              </div>
            </div>
            <Link className="menu-link" to="/studio">
              <Icon name="studio" />
              Studio
            </Link>
            <button className="menu-link" disabled={busy} onClick={logout}>
              <Icon name="logout" />
              Выйти
            </button>
          </>
        ) : (
          <>
            <Link className="menu-link" to="/login">
              Войти в аккаунт
            </Link>
            <Link className="menu-link" to="/register">
              Регистрация
            </Link>
          </>
        )}
        <ThemeSettings />
        <a className="menu-link muted" href="/docs">
          API Docs
        </a>
        {error && <p role="alert">{error}</p>}
      </div>
    </div>
  );
}
export function AuthForm({ register = false }: { register?: boolean }) {
  const { update } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const data: Record<string, string> = {
      email: String(form.get("email")),
      password: String(form.get("password")),
    };
    if (register)
      data.username = String(form.get("username")).trim().toLowerCase();
    try {
      update(
        await request(register ? "register" : "login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }),
      );
      navigate("/");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="auth-form">
      <h1>{register ? "Регистрация" : "Вход"}</h1>
      <form onSubmit={submit}>
        <label>
          Email
          <input
            name="email"
            type="email"
            autoComplete="email"
            required
            maxLength={320}
          />
        </label>
        {register && (
          <label>
            Имя пользователя
            <input
              name="username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9_]+"
              title="3–32 латинских буквы, цифры или подчёркивание"
            />
          </label>
        )}
        <label>
          Пароль
          <input
            name="password"
            type="password"
            autoComplete={register ? "new-password" : "current-password"}
            required
            minLength={12}
            maxLength={128}
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button disabled={busy}>
          {busy ? "Подождите…" : register ? "Зарегистрироваться" : "Войти"}
        </button>
      </form>
      <Link to={register ? "/login" : "/register"}>
        {register ? "Уже есть аккаунт? Войти" : "Создать аккаунт"}
      </Link>
    </section>
  );
}
