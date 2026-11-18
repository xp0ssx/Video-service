import { Avatar, Icon } from "../../components/Icon";
import { VideoCard, Thumbnail } from "../../components/VideoCard";
import type { VideoCardData } from "../../components/VideoCard";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams, useNavigate, useLocation } from "react-router-dom";
import { api, HttpError } from "../../api/client";
import type { Page } from "../../api/client";
import { useAuth } from "../auth/Auth";

const statusLabel: Record<string, string> = {
  ready: "Готово",
  awaiting_upload: "Ожидает файл",
  uploading: "Отправляется",
  queued: "В очереди",
  processing: "Обрабатывается",
  failed: "Ошибка",
};

type Channel = {
  id: string;
  username: string;
  title: string;
  description: string;
  owned_by_me: boolean;
  subscribers_count: number;
  subscribed_by_me: boolean;
};
type Video = VideoCardData & { status?: string; moderation_state?: string };
type Playlist = {
  id: string;
  title: string;
  description: string;
  username: string;
  owned_by_me: boolean;
  items: Video[];
  next_cursor: string | null;
};
function useWrite() {
  const { session } = useAuth();
  return (path: string, method: string, body?: unknown) =>
    api(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session?.csrf_token || "",
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
}
function useResource<T>(path: string) {
  const [resource, setResource] = useState<{ key: string; data: T } | null>(
      null,
    ),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const { session } = useAuth();
  const key =
    path +
    ":" +
    (session?.user.id || "guest") +
    ":" +
    (session?.user.role || "");
  const value = resource?.key === key ? resource.data : null;
  const reload = () => setRevision((n) => n + 1);
  useEffect(() => {
    const controller = new AbortController(),
      request = ++generation.current;
    const current = () =>
      !controller.signal.aborted && request === generation.current;
    setError("");
    api<T>(path, { signal: controller.signal })
      .then((data) => {
        if (current()) setResource({ key, data });
      })
      .catch((e) => {
        if (current()) {
          setError(e.message);
          if (e instanceof HttpError && [401, 403, 404].includes(e.status))
            setResource(null);
        }
      });
    return () => controller.abort();
  }, [path, revision, key]);
  function confirm(data: Partial<T>) {
    // A GET started before this successful write cannot replace its confirmed result.
    generation.current++;
    setResource((current) =>
      current?.key === key
        ? { key, data: { ...current.data, ...data } }
        : current,
    );
    setError("");
    reload();
  }
  useEffect(() => {
    const refresh = () => setRevision((n) => n + 1);
    window.addEventListener("focus", refresh);
    window.addEventListener("subscriptions-changed", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("subscriptions-changed", refresh);
    };
  }, []);
  return { value, error, reload, confirm };
}
function LoginRequired() {
  return (
    <p>
      <Link to="/login">Войдите в аккаунт</Link>, чтобы управлять каналом и
      плейлистами.
    </p>
  );
}
function MetadataForm({
  title = "",
  description = "",
  onSave,
  label,
  inlineActions = false,
}: {
  title?: string;
  description?: string;
  onSave: (data: { title: string; description: string }) => Promise<void>;
  label: string;
  inlineActions?: boolean;
}) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [draft, setDraft] = useState<{ title: string; description: string } | null>(
      null,
    );
  const edits = useRef(0),
    saving = useRef(false);
  const fields = draft || { title, description };
  function change(data: { title: string; description: string }) {
    edits.current++;
    setDraft(data);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving.current) return;
    const snapshot = {
        title: fields.title.trim(),
        description: fields.description.trim(),
      },
      version = edits.current;
    saving.current = true;
    setBusy(true);
    setError("");
    try {
      await onSave(snapshot);
      // Only retire the submitted draft; edits made while PATCH was pending stay open.
      if (edits.current === version) setDraft(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }
  const actions = (
    <>
      <button disabled={busy}>{label}</button>
      <button
        type="button"
        className={inlineActions ? "secondary" : undefined}
        disabled={busy}
        onClick={() => {
          setDraft(null);
          setError("");
        }}
      >
        Отменить изменения
      </button>
    </>
  );
  return (
    <form className="metadata-form" onSubmit={submit}>
      <label>
        Название
        <input
          name="title"
          required
          maxLength={200}
          value={fields.title}
          onChange={(event) => change({ ...fields, title: event.target.value })}
        />
      </label>
      <label>
        Описание
        <textarea
          name="description"
          maxLength={5000}
          value={fields.description}
          onChange={(event) =>
            change({ ...fields, description: event.target.value })
          }
        />
      </label>
      {error && <p role="alert">{error}</p>}
      {inlineActions ? <div className="metadata-actions">{actions}</div> : actions}
    </form>
  );
}
function Listing<T extends { id: string }>({
  path,
  render,
}: {
  path: string;
  render: (item: T) => React.ReactNode;
}) {
  const [items, setItems] = useState<T[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [busy, setBusy] = useState(true),
    [error, setError] = useState("");
  const [page, setPage] = useState<string | null>(null),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    api<Page<T>>(
      `${path}${path.includes("?") ? "&" : "?"}limit=20${page ? "&cursor=" + encodeURIComponent(page) : ""}`,
      { signal: controller.signal },
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        setItems((old) =>
          page
            ? [
                ...new Map(
                  [...old, ...data.items].map((item) => [item.id, item]),
                ).values(),
              ]
            : data.items,
        );
        setCursor(data.next_cursor);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [path, page, revision]);
  return (
    <>
      <div className="channel-list">
        {items.map((item) => (
          <article key={item.id}>{render(item)}</article>
        ))}
      </div>
      {busy && <p role="status">Загрузка…</p>}
      {error && (
        <p role="alert">
          {error}
          <button onClick={() => setRevision((n) => n + 1)}>Повторить</button>
        </p>
      )}
      {!busy && !error && !items.length && <p>Здесь пока ничего нет.</p>}
      {cursor && !error && (
        <button disabled={busy} onClick={() => setPage(cursor)}>
          Показать ещё
        </button>
      )}
    </>
  );
}
function PlaylistPreview({
  item,
  edit = false,
}: {
  item: Playlist;
  edit?: boolean;
}) {
  const { value } = useResource<Playlist>(`/playlists/${item.id}?limit=1`);
  return (
    <div className="playlist-card">
      <Link
        to={edit ? `/studio/playlists/${item.id}` : `/playlists/${item.id}`}
      >
        <div className="playlist-cover-stack">
          <Thumbnail
            key={value?.items[0]?.id || "empty"}
            video={value?.items[0] || { id: "", title: "" }}
          />
          <span className="playlist-badge">
            <Icon name="playlist" size={16} />
            Плейлист
          </span>
        </div>
        <h3>{item.title}</h3>
      </Link>
      <p className="muted">{item.description || "Коллекция видео"}</p>
      <Link
        className="small-link"
        to={edit ? `/studio/playlists/${item.id}` : `/playlists/${item.id}`}
      >
        {edit ? "Редактировать" : "Открыть плейлист"}
      </Link>
    </div>
  );
}
export function ChannelPage() {
  const { username } = useParams();
  return <ChannelContent key={username} username={username || ""} />;
}
function ChannelContent({ username }: { username: string }) {
  const { hash } = useLocation();
  const activeTab = hash === "#channel-playlists" ? "playlists" : "videos";
  const { session } = useAuth(),
    write = useWrite(),
    {
      value: channel,
      error,
      confirm,
    } = useResource<Channel>("/channels/" + encodeURIComponent(username));
  const [busy, setBusy] = useState(false),
    [actionError, setActionError] = useState("");
  async function subscribe() {
    if (!channel) return;
    setBusy(true);
    setActionError("");
    try {
      await write(
        `/channels/${encodeURIComponent(username)}/subscription`,
        channel.subscribed_by_me ? "DELETE" : "PUT",
      );
      window.dispatchEvent(new Event("subscriptions-changed"));
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (error && !channel) return <p role="alert">{error}</p>;
  if (!channel) return <p>Загрузка канала…</p>;
  return (
    <section className="channel-page">
      {error && <p role="alert">{error}</p>}
      <header className="channel-header">
        <Avatar name={channel.username} size={128} />
        <div className="channel-info">
          <h1>{channel.title}</h1>
          <p className="muted">
            @{channel.username} · Подписчиков:{" "}
            <span data-testid="subscribers-count">
              {channel.subscribers_count}
            </span>
          </p>
          <p className="video-description">{channel.description}</p>
          {!channel.owned_by_me &&
            (session ? (
              <button
                disabled={busy}
                className={channel.subscribed_by_me ? "secondary" : ""}
                onClick={() => void subscribe()}
              >
                {channel.subscribed_by_me ? "Отписаться" : "Подписаться"}
              </button>
            ) : (
              <Link className="button-link" to="/login">
                Войдите, чтобы подписаться
              </Link>
            ))}
        </div>
      </header>
      {actionError && <p role="alert">{actionError}</p>}
      {channel.owned_by_me && (
        <details className="channel-edit">
          <summary>Редактировать канал</summary>
          <MetadataForm
            title={channel.title}
            description={channel.description}
            label="Сохранить канал"
            onSave={async (data) => {
              const saved = (await write(
                "/me/channel",
                "PATCH",
                data,
              )) as Channel;
              confirm(saved);
            }}
          />
          <Link className="small-link" to="/studio/playlists">
            Управлять плейлистами
          </Link>
        </details>
      )}
      <div className="channel-tabs" role="tablist" aria-label="Разделы канала">
        {(["videos", "playlists"] as const).map((tab) => (
          <Link
            key={tab}
            id={`channel-tab-${tab}`}
            to={`#channel-${tab}`}
            role="tab"
            aria-selected={activeTab === tab}
            aria-controls={`channel-${tab}`}
            tabIndex={activeTab === tab ? 0 : -1}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End", " "].includes(event.key)
              )
                return;
              event.preventDefault();
              const next =
                event.key === " " ? tab
                  : event.key === "Home" ? "videos"
                    : event.key === "End" ? "playlists"
                      : tab === "videos" ? "playlists" : "videos";
              const target = document.getElementById(`channel-tab-${next}`);
              target?.focus();
              target?.click();
            }}
          >
            {tab === "videos" ? "Видео" : "Плейлисты"}
          </Link>
        ))}
      </div>
      <section
        id="channel-videos"
        role="tabpanel"
        aria-labelledby="channel-tab-videos"
        hidden={activeTab !== "videos"}
        tabIndex={0}
      >
        <Listing<Video>
          path={`/channels/${encodeURIComponent(username)}/videos`}
          render={(item) => <VideoCard video={item} />}
        />
      </section>
      <section
        id="channel-playlists"
        role="tabpanel"
        aria-labelledby="channel-tab-playlists"
        hidden={activeTab !== "playlists"}
        tabIndex={0}
      >
        <Listing<Playlist>
          path={`/channels/${encodeURIComponent(username)}/playlists`}
          render={(item) => <PlaylistPreview item={item} />}
        />
      </section>
    </section>
  );
}
export function SubscriptionsPage() {
  const { session, loading } = useAuth();
  if (loading) return <p>Загрузка…</p>;
  return (
    <section>
      <h1>Подписки</h1>
      {session ? (
        <Listing<Channel>
          key={session.user.id}
          path="/me/subscriptions"
          render={(item) => (
            <Link
              className="subscription-card"
              to={`/channels/${item.username}`}
            >
              <Avatar name={item.username} size={72} />
              <span>
                {item.title} — @{item.username}
              </span>
            </Link>
          )}
        />
      ) : (
        <LoginRequired />
      )}
    </section>
  );
}
export function PlaylistPage() {
  const { id } = useParams(),
    { session } = useAuth();
  return (
    <PlaylistContent
      key={`${id}:${session?.user.id || "guest"}`}
      id={id || ""}
    />
  );
}
function PlaylistContent({ id }: { id: string }) {
  const { value, error } = useResource<Playlist>("/playlists/" + id);
  const [items, setItems] = useState<Video[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [pageError, setPageError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setItems(value?.items || []);
    setCursor(value?.next_cursor || null);
  }, [value]);
  async function more() {
    if (!cursor) return;
    const started = ++generation.current;
    setBusy(true);
    setPageError("");
    try {
      const next = await api<Playlist>(
        `/playlists/${id}?cursor=${encodeURIComponent(cursor)}`,
      );
      if (started === generation.current) {
        setItems((old) => [
          ...new Map([...old, ...next.items].map((v) => [v.id, v])).values(),
        ]);
        setCursor(next.next_cursor);
      }
    } catch (e) {
      if (started === generation.current) {
        setPageError((e as Error).message);
        if (e instanceof HttpError && [401, 403, 404].includes(e.status)) {
          setItems([]);
          setCursor(null);
        }
      }
    } finally {
      setBusy(false);
    }
  }
  if (error && !value) return <p role="alert">{error}</p>;
  if (!value) return <p role="status">Загрузка плейлиста…</p>;
  return (
    <section className="playlist-page public-playlist">
      <header className="playlist-summary">
        <Thumbnail
          key={items[0]?.id || "empty"}
          video={items[0] || { id: "", title: "" }}
        />
        <div className="playlist-summary-copy">
          <p className="playlist-kind">
            <Icon name="playlist" size={18} />
            Плейлист
          </p>
          <h1>{value.title}</h1>
          <Link className="playlist-owner" to={`/channels/${value.username}`}>
            <Avatar name={value.username} size={28} />
            {value.username}
          </Link>
          <p className="muted">
            {cursor
              ? `Показано видео: ${items.length}`
              : `Доступных видео: ${items.length}`}
          </p>
          <p className="video-description">{value.description}</p>
          {items[0] && (
            <Link
              className="button-link"
              to={items[0].watch_url || `/watch/${items[0].id}`}
            >
              <Icon name="play" size={20} />
              Смотреть
            </Link>
          )}
          {value.owned_by_me && (
            <Link className="playlist-edit-link" to={`/studio/playlists/${id}`}>
              Редактировать плейлист
            </Link>
          )}
        </div>
      </header>
      <div className="playlist-videos">
        {error && <p role="alert">{error}</p>}
        <ol className="playlist-rows">
          {items.map((item, index) => (
            <li className="playlist-video-row" key={item.id}>
              <span className="playlist-position">{index + 1}</span>
              <Link
                className="playlist-video-link"
                to={item.watch_url || `/watch/${item.id}`}
              >
                <Thumbnail video={item} />
                <div>
                  <h2>{item.title}</h2>
                  <p className="muted">{value.username}</p>
                </div>
              </Link>
            </li>
          ))}
        </ol>
        {!items.length && (
          <div className="empty-state">
            <Icon name="playlist" size={48} />
            <h2>В плейлисте пока нет доступных видео</h2>
            <p>Здесь появятся видео, которые можно посмотреть.</p>
          </div>
        )}
        {pageError && <p role="alert">{pageError}</p>}
        {cursor && (
          <button
            className="secondary"
            disabled={busy}
            onClick={() => void more()}
          >
            {busy ? "Загрузка…" : "Показать ещё"}
          </button>
        )}
      </div>
    </section>
  );
}

async function all<T>(path: string): Promise<T[]> {
  let cursor: string | null = null;
  const result: T[] = [];
  do {
    const page: Page<T> = await api<Page<T>>(
      path +
        (path.includes("?") ? "&" : "?") +
        "limit=100" +
        (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
    );
    result.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return result;
}
export function PlaylistStudio() {
  const { session, loading } = useAuth();
  const { id } = useParams();
  if (loading) return <p>Загрузка…</p>;
  if (!session) return <LoginRequired />;
  return id ? (
    <PlaylistEditor key={`${id}:${session.user.id}`} id={id} />
  ) : (
    <PlaylistCreate key={session.user.id} username={session.user.username} />
  );
}
function PlaylistCreate({ username }: { username: string }) {
  const write = useWrite(),
    navigate = useNavigate();
  return (
    <section className="playlist-page">
      <h1>Мои плейлисты</h1>
      <MetadataForm
        label="Создать плейлист"
        onSave={async (data) => {
          const created = (await write("/playlists", "POST", data)) as Playlist;
          navigate("/studio/playlists/" + created.id);
        }}
      />
      <Listing<Playlist>
        path={`/channels/${username}/playlists`}
        render={(item) => <PlaylistPreview item={item} edit />}
      />
    </section>
  );
}
function PlaylistEditor({ id }: { id: string }) {
  const write = useWrite(),
    navigate = useNavigate(),
    { value, error, reload, confirm } = useResource<Playlist>(
      "/me/playlists/" + id,
    );
  const [items, setItems] = useState<Video[]>([]),
    [videos, setVideos] = useState<Video[]>([]),
    [ready, setReady] = useState(false),
    [actionError, setActionError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setReady(false);
    if (value)
      Promise.all([all<Video>("/me/playlists/" + id), all<Video>("/me/videos")])
        .then(([entries, own]) => {
          if (active) {
            setItems(entries);
            setVideos(own);
            setReady(true);
          }
        })
        .catch((e) => {
          if (active) setActionError(e.message);
        });
    return () => {
      active = false;
    };
  }, [id, value]);
  async function action(path: string, method: string, body?: unknown) {
    setBusy(true);
    setActionError("");
    try {
      await write(path, method, body);
      reload();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function move(index: number, step: number) {
    const ordered = [...items];
    [ordered[index], ordered[index + step]] = [
      ordered[index + step],
      ordered[index],
    ];
    await action(`/playlists/${id}/order`, "PUT", {
      video_ids: ordered.map((item) => item.id),
    });
  }
  if (error && !value) return <p role="alert">{error}</p>;
  if (!value) return <p>Загрузка…</p>;
  return (
    <section className="playlist-page">
      <h1>Редактировать плейлист</h1>
      {error && <p role="alert">{error}</p>}
      <Link to={`/playlists/${id}`}>Открыть плейлист</Link>
      <MetadataForm
        title={value.title}
        description={value.description}
        label="Сохранить плейлист"
        inlineActions
        onSave={async (data) => {
          await write("/playlists/" + id, "PATCH", data);
          confirm(data);
        }}
      />
      {actionError && <p role="alert">{actionError}</p>}
      {!ready && <p role="status">Загрузка элементов…</p>}
      <fieldset disabled={busy || !ready}>
        <legend>Видео плейлиста</legend>
        <ol className="playlist-editor">
          {items.map((item, index) => (
            <li key={item.id}>
              <span className="editor-position">{index + 1}</span>
              {item.status === "ready" && item.moderation_state !== "hidden" ? (
                <Thumbnail video={item} />
              ) : (
                <div className="editor-thumbnail-placeholder">
                  <Icon name="video" />
                </div>
              )}
              <span className="editor-video-title">
                {item.title} ({statusLabel[item.status || ""] || "Недоступно"})
              </span>
              <div className="editor-item-actions">
                <button
                  aria-label={`Выше: ${item.title}`}
                  disabled={!index}
                  onClick={() => void move(index, -1)}
                >
                  <Icon name="up" size={20} />
                </button>
                <button
                  aria-label={`Ниже: ${item.title}`}
                  disabled={index === items.length - 1}
                  onClick={() => void move(index, 1)}
                >
                  <Icon name="down" size={20} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`Удалить из плейлиста: ${item.title}`}
                  onClick={() =>
                    void action(`/playlists/${id}/items/${item.id}`, "DELETE")
                  }
                >
                  <Icon name="trash" size={20} />
                </button>
              </div>
            </li>
          ))}
        </ol>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const video = String(
              new FormData(event.currentTarget).get("video"),
            );
            if (video) void action(`/playlists/${id}/items/${video}`, "PUT");
          }}
        >
          <label>
            Своё видео
            <select name="video" required>
              <option value="">Выберите видео</option>
              {videos
                .filter((video) => !items.some((item) => item.id === video.id))
                .map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.title}
                  </option>
                ))}
            </select>
          </label>
          <button>Добавить видео</button>
        </form>
      </fieldset>
      <button
        className="delete-playlist"
        disabled={busy}
        onClick={async () => {
          if (!window.confirm("Удалить плейлист? Видео сохранятся.")) return;
          setBusy(true);
          try {
            await write("/playlists/" + id, "DELETE");
            navigate("/studio/playlists");
          } catch (e) {
            setActionError((e as Error).message);
            setBusy(false);
          }
        }}
      >
        Удалить плейлист
      </button>
    </section>
  );
}
