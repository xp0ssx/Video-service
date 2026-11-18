import { Icon } from "../../components/Icon";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, HttpError } from "../../api/client";
import type { Page } from "../../api/client";
import { useAuth } from "../auth/Auth";

type Report = {
  id: string;
  video_id: string;
  owner_id: string;
  reporter_id: string;
  reason: string;
  body: string;
  status: string;
  review_note: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  resolved_action_id: string | null;
  created_at: string;
};
type Action = {
  id: string;
  actor_id: string;
  video_id: string | null;
  target_user_id: string | null;
  action: string;
  reason: string;
  previous_value: string;
  new_value: string;
  created_at: string;
};
type User = { id: string; username: string; role: string };
const reasons: Record<string, string> = {
  spam: "Спам",
  copyright: "Авторские права",
  abuse: "Оскорбления",
  other: "Другое",
};
const statuses: Record<string, string> = {
  open: "Открыта",
  dismissed: "Отклонена",
  actioned: "Приняты меры",
};
function useWrite() {
  const { session } = useAuth();
  return <T,>(path: string, method: string, body: unknown) =>
    api<T>(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session?.csrf_token || "",
      },
      body: JSON.stringify(body),
    });
}
export function ReportForm({ id }: { id: string }) {
  const { session } = useAuth(),
    write = useWrite();
  const [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const row = await write<{ id: string; status: string }>(
        `/videos/${id}/reports`,
        "POST",
        {
          reason: String(form.get("reason")),
          body: String(form.get("body")).trim(),
        },
      );
      setNotice(
        `Жалоба ${row.id}: ${statuses[row.status]}. Повторная отправка возвращает эту же жалобу.`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!session) return null;
  return (
    <details className="report-form">
      <summary>
        <Icon name="flag" size={20} />
        <span>Пожаловаться</span>
      </summary>
      <form className="metadata-form" onSubmit={submit}>
        <label>
          Причина жалобы
          <select name="reason">
            {Object.entries(reasons).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Текст жалобы
          <textarea name="body" required maxLength={2000} />
        </label>
        <button disabled={busy}>Отправить жалобу</button>
        {notice && <p role="status">{notice}</p>}
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}
export function ModerationLinks() {
  const { session } = useAuth();
  return (
    <>
      {["moderator", "admin"].includes(session?.user.role || "") && (
        <Link className="nav-item" to="/moderation">
          Модерация
        </Link>
      )}
      {session?.user.role === "admin" && (
        <Link className="nav-item" to="/admin">
          Администрирование
        </Link>
      )}
    </>
  );
}
function Gate({
  admin = false,
  children,
}: {
  admin?: boolean;
  children: React.ReactNode;
}) {
  const { session, loading, update } = useAuth();
  const [checking, setChecking] = useState(true),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const refresh = () => {
      api<typeof session>("/auth/session")
        .then((value) => {
          if (active) {
            update(value);
            setError("");
          }
        })
        .catch((e) => {
          if (active) {
            if (e instanceof HttpError && e.status === 401) update(null);
            setError(e.message);
          }
        })
        .finally(() => {
          if (active) setChecking(false);
        });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
    };
  }, [update]);
  if (loading || checking) return <p>Проверка доступа…</p>;
  if (!session)
    return (
      <p>
        <Link to="/login">Войдите в аккаунт</Link>.
      </p>
    );
  if (error) return <p role="alert">{error}</p>;
  if (
    admin
      ? session.user.role !== "admin"
      : !["admin", "moderator"].includes(session.user.role)
  )
    return <p role="alert">Недостаточно прав</p>;
  return <>{children}</>;
}
function Paged<T extends { id: string }>({
  path,
  render,
}: {
  path: string;
  render: (row: T) => React.ReactNode;
}) {
  const [items, setItems] = useState<T[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [after, setAfter] = useState<string | null>(null),
    [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    api<Page<T>>(
      `${path}${path.includes("?") ? "&" : "?"}limit=20${after ? "&cursor=" + encodeURIComponent(after) : ""}`,
      { signal: controller.signal },
    )
      .then((page) => {
        if (!controller.signal.aborted) {
          setItems((old) => (after ? [...old, ...page.items] : page.items));
          setCursor(page.next_cursor);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [path, after, revision]);
  return (
    <>
      {items.map((row) => (
        <article className="moderation-card" key={row.id}>
          {render(row)}
        </article>
      ))}
      {busy && <p role="status">Загрузка…</p>}
      {error && (
        <p role="alert">
          {error}
          <button
            onClick={() => {
              setAfter(null);
              setRevision((n) => n + 1);
            }}
          >
            Повторить
          </button>
        </p>
      )}
      {!busy && !error && !items.length && <p>Записей нет.</p>}
      {cursor && !error && (
        <button disabled={busy} onClick={() => setAfter(cursor)}>
          Показать ещё
        </button>
      )}
    </>
  );
}
function ReportCard({ row, onChange }: { row: Report; onChange: () => void }) {
  const write = useWrite(),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function decide(status: string) {
    if (!reason.trim()) {
      setError("Укажите обоснование");
      return;
    }
    if (
      !window.confirm(
        status === "actioned"
          ? "Скрыть видео и разрешить жалобу?"
          : "Отклонить жалобу?",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await write(`/moderation/reports/${row.id}`, "PATCH", {
        status,
        reason: reason.trim(),
      });
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h2>Жалоба {row.id}</h2>
      <p>
        Видео: <span>{row.video_id}</span>
      </p>
      <p>Владелец: {row.owner_id}</p>
      <p>Заявитель: {row.reporter_id}</p>
      <p>
        {reasons[row.reason]} — {statuses[row.status]}
      </p>
      <time dateTime={row.created_at}>
        {new Date(row.created_at).toLocaleString("ru-RU")}
      </time>
      <p className="video-description">{row.body}</p>
      {row.review_note && (
        <p>
          Решение: {row.review_note}; модератор {row.reviewed_by};{" "}
          {row.reviewed_at}; действие {row.resolved_action_id || "—"}
        </p>
      )}
      {row.status === "open" && (
        <>
          <label>
            Обоснование решения
            <textarea
              required
              maxLength={2000}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <div className="moderation-buttons">
            <button disabled={busy} onClick={() => void decide("dismissed")}>
              Отклонить жалобу
            </button>
            <button disabled={busy} onClick={() => void decide("actioned")}>
              Скрыть по жалобе
            </button>
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </>
  );
}
function VideoStateForm({ onChange }: { onChange: () => void }) {
  const write = useWrite(),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (!window.confirm("Изменить состояние видео?")) return;
    setBusy(true);
    setError("");
    try {
      const result = await write<{ action_id: string | null }>(
        "/moderation/videos/" + String(form.get("video")) + "/state",
        "PUT",
        {
          state: String(form.get("state")),
          reason: String(form.get("reason")).trim(),
        },
      );
      setNotice(
        result.action_id
          ? "Состояние изменено"
          : "Состояние уже установлено; нового действия нет",
      );
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>Скрыть или восстановить видео по ID</summary>
      <form className="metadata-form" onSubmit={submit}>
        <label>
          ID видео
          <input name="video" required />
        </label>
        <label>
          Состояние
          <select name="state">
            <option value="visible">Восстановить</option>
            <option value="hidden">Скрыть</option>
          </select>
        </label>
        <label>
          Обоснование действия
          <textarea name="reason" required maxLength={2000} />
        </label>
        <button disabled={busy}>Применить состояние</button>
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
      </form>
    </details>
  );
}
export function ModerationPage() {
  return (
    <Gate>
      <ModerationContent />
    </Gate>
  );
}
function ModerationContent() {
  const [status, setStatus] = useState("open"),
    [revision, setRevision] = useState(0);
  const reload = () => setRevision((n) => n + 1);
  return (
    <section className="moderation-page">
      <h1>Модерация</h1>
      <label>
        Статус жалоб
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="open">Открытые</option>
          <option value="dismissed">Отклонённые</option>
          <option value="actioned">Приняты меры</option>
          <option value="all">Все</option>
        </select>
      </label>
      <button onClick={reload}>Обновить очередь</button>
      <Paged<Report>
        key={status + revision}
        path={"/moderation/reports?status=" + status}
        render={(row) => <ReportCard row={row} onChange={reload} />}
      />
      <VideoStateForm onChange={reload} />
      <h2>Журнал действий</h2>
      <Paged<Action>
        key={"actions" + revision}
        path="/moderation/actions"
        render={(row) => (
          <>
            <strong>{row.action}</strong>
            <p>Цель: {row.video_id || row.target_user_id}</p>
            <p>
              {row.previous_value} → {row.new_value}
            </p>
            <p className="video-description">{row.reason}</p>
            <p>
              Автор: {row.actor_id};{" "}
              {new Date(row.created_at).toLocaleString("ru-RU")}
            </p>
          </>
        )}
      />
    </section>
  );
}
export function AdminPage() {
  return (
    <Gate admin>
      <AdminContent />
    </Gate>
  );
}
function AdminContent() {
  const write = useWrite();
  const [users, setUsers] = useState<User[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [reason, setReason] = useState("");
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const username = String(new FormData(event.currentTarget).get("username"));
    setBusy(true);
    setUsers([]);
    setError("");
    try {
      setUsers(
        await api<User[]>("/admin/users?" + new URLSearchParams({ username })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function change(user: User) {
    if (!reason.trim()) {
      setError("Укажите обоснование");
      return;
    }
    if (!window.confirm("Изменить роль пользователя?")) return;
    setBusy(true);
    setError("");
    try {
      const updated = await write<User>(`/admin/users/${user.id}/role`, "PUT", {
        role: user.role === "moderator" ? "user" : "moderator",
        reason: reason.trim(),
      });
      setUsers([updated]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="moderation-page">
      <h1>Администрирование</h1>
      <form className="metadata-form" onSubmit={search}>
        <label>
          Username пользователя
          <input name="username" required minLength={3} maxLength={32} />
        </label>
        <button disabled={busy}>Найти пользователя</button>
      </form>
      {busy && <p role="status">Загрузка…</p>}
      {error && <p role="alert">{error}</p>}
      {!busy && !users.length && <p>Пользователи не выбраны или не найдены.</p>}
      <label>
        Причина изменения роли
        <textarea
          maxLength={2000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {users.map((user) => (
        <article className="admin-user-card" key={user.id}>
          <p>
            {user.username}: <span data-testid="user-role">{user.role}</span>
          </p>
          {user.role !== "admin" && (
            <button disabled={busy} onClick={() => void change(user)}>
              {user.role === "moderator"
                ? "Снять модератора"
                : "Назначить модератором"}
            </button>
          )}
        </article>
      ))}
    </section>
  );
}
