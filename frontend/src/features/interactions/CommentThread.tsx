import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, HttpError } from "../../api/client";
import type { Page } from "../../api/client";
import { Avatar, Icon } from "../../components/Icon";
import { useAuth } from "../auth/Auth";

type LikeState = { likes_count: number; liked_by_me: boolean };
export type Comment = LikeState & {
  id: string;
  parent_id: string | null;
  replies_count: number;
  body: string;
  created_at: string;
  author: { id: string; username: string };
};

export function mergeComments(previous: Comment[], incoming: Comment[]) {
  return [...new Map([...previous, ...incoming].map((item) => [item.id, item])).values()]
    .sort((a, b) => a.created_at === b.created_at
      ? a.id < b.id ? -1 : a.id === b.id ? 0 : 1
      : a.created_at < b.created_at ? -1 : 1);
}

function message(error: unknown) {
  return error instanceof HttpError && error.status === 401
    ? "Сессия истекла. Войдите снова."
    : error instanceof Error ? error.message : "Не удалось выполнить запрос";
}

type ItemProps = {
  comment: Comment;
  videoId: string;
  onReply: (comment: Comment) => void;
  onUnavailable: () => void;
};
function CommentItem({ comment, videoId, onReply, onUnavailable }: ItemProps) {
  const { session } = useAuth();
  const [likes, setLikes] = useState<LikeState>(comment);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function toggleLike() {
    if (!session || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    try {
      const state = await api<LikeState>(`/videos/${videoId}/comments/${comment.id}/like`, {
        method: likes.liked_by_me ? "DELETE" : "PUT",
        headers: { "X-CSRF-Token": session.csrf_token },
        signal: controller.signal,
      });
      if (!controller.signal.aborted) setLikes(state);
    } catch (e) {
      if (!controller.signal.aborted) {
        if (e instanceof HttpError && e.status === 404) onUnavailable();
        else setError(message(e));
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      request.current = null;
    }
  }
  return (
    <>
      <Avatar name={comment.author.username} size={comment.parent_id ? 28 : 40} />
      <div className="comment-copy">
        <div className="comment-meta">
          <Link to={`/channels/${comment.author.username}`}>
            <strong>{comment.author.username}</strong>
          </Link>
          <time dateTime={comment.created_at}>
            {new Date(comment.created_at).toLocaleDateString("ru-RU", {
              day: "numeric", month: "long", year: "numeric",
            })}
          </time>
        </div>
        <p className="comment-body">{comment.body}</p>
        <div className="comment-actions">
          <button type="button" className="comment-like text-button"
            aria-label="Нравится комментарий" aria-pressed={likes.liked_by_me}
            disabled={!session || busy} onClick={() => void toggleLike()}>
            <Icon name="like" size={16} />
            <span>{likes.likes_count}</span>
          </button>
          {session && <button type="button" className="text-button"
            onClick={() => onReply(comment)}>Ответить</button>}
        </div>
        {error && <p role="alert">{error}</p>}
      </div>
    </>
  );
}

export function CommentThread({ comment, videoId, onUnavailable }: Omit<ItemProps, "onReply">) {
  const { session } = useAuth();
  const [replies, setReplies] = useState<Comment[]>([]);
  const [count, setCount] = useState(comment.replies_count);
  const [expanded, setExpanded] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [target, setTarget] = useState<Comment | null>(null);
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [postError, setPostError] = useState("");
  const [notice, setNotice] = useState("");
  const listRequest = useRef<AbortController | null>(null);
  const postRequest = useRef<AbortController | null>(null);
  const textarea = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => () => {
    listRequest.current?.abort();
    postRequest.current?.abort();
  }, []);
  useEffect(() => { if (target) textarea.current?.focus({ preventScroll: true }); }, [target]);
  function fail(e: unknown, list: boolean) {
    if (e instanceof HttpError && e.status === 404) onUnavailable();
    else if (list) setListError(message(e));
    else setPostError(message(e));
  }
  async function load(after: string | null) {
    if (listRequest.current) return;
    const controller = new AbortController();
    listRequest.current = controller;
    setLoading(true);
    setListError("");
    const params = new URLSearchParams({ limit: "20" });
    if (after) params.set("cursor", after);
    try {
      const page = await api<Page<Comment>>(
        `/videos/${videoId}/comments/${comment.id}/replies?${params}`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setReplies(previous => mergeComments(previous, page.items));
      setCursor(page.next_cursor);
      setLoaded(true);
    } catch (e) {
      if (!controller.signal.aborted) fail(e, true);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
      listRequest.current = null;
    }
  }
  function toggleReplies() {
    setExpanded(!expanded);
    if (!expanded && !loaded) void load(null);
  }
  function replyTo(item: Comment) {
    setTarget(item);
    setPostError("");
    setNotice("");
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !target || postRequest.current) return;
    if (!body.trim() || Array.from(body).length > 2000) {
      setPostError("Комментарий должен содержать от 1 до 2000 символов");
      return;
    }
    const controller = new AbortController();
    postRequest.current = controller;
    setPosting(true);
    setPostError("");
    try {
      const result = await api<Comment>(`/videos/${videoId}/comments`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
        body: JSON.stringify({ body, parent_id: target.id }),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setReplies(previous => mergeComments(previous, [result]));
      setCount(previous => previous + 1);
      setExpanded(true);
      setTarget(null);
      setBody("");
      setNotice("Ответ добавлен");
      if (!loaded) void load(null);
    } catch (e) {
      if (!controller.signal.aborted) fail(e, false);
    } finally {
      if (!controller.signal.aborted) setPosting(false);
      postRequest.current = null;
    }
  }
  return (
    <li className="comment-thread" data-comment-id={comment.id}>
      <div className="comment">
        <CommentItem comment={comment} videoId={videoId} onReply={replyTo} onUnavailable={onUnavailable} />
      </div>
      <div className="comment-discussion">
        {count > 0 && <button type="button" className="replies-toggle text-button"
          aria-expanded={expanded} aria-controls={`replies-${comment.id}`} onClick={toggleReplies}>
          <Icon name={expanded ? "up" : "down"} size={16} />
          {expanded ? "Скрыть ответы" : "Показать ответы"} ({count})
        </button>}
        <div id={`replies-${comment.id}`} hidden={!expanded}>
          <ol className="comment-replies" aria-label="Ответы на комментарий">
            {replies.map(reply => <li className="comment comment-reply" key={reply.id} data-comment-id={reply.id}>
              <CommentItem comment={reply} videoId={videoId} onReply={replyTo} onUnavailable={onUnavailable} />
            </li>)}
          </ol>
          {loading && <p role="status" className="muted">Загрузка ответов…</p>}
          {listError && <div role="alert"><p>{listError}</p>
            <button type="button" className="text-button" onClick={() => void load(cursor)}>
              Повторить загрузку ответов
            </button></div>}
          {loaded && cursor && !listError && <button type="button" className="text-button"
            disabled={loading} onClick={() => void load(cursor)}>Ещё ответы</button>}
        </div>
        {target && session && <form className="comment-form reply-form" onSubmit={submit}>
          <Avatar name={session.user.username} size={28} />
          <div className="comment-compose">
            <label>
              <span className="muted">Ответ для {target.author.username}</span>
              <span className="sr-only">Ваш ответ</span>
              <textarea ref={textarea} aria-label="Ваш ответ" placeholder="Введите ответ"
                required value={body} disabled={posting} onChange={event => setBody(event.target.value)} />
            </label>
            <div className="comment-form-actions">
              <button type="button" className="text-button" disabled={posting}
                onClick={() => { setTarget(null); setBody(""); setPostError(""); }}>Отмена</button>
              <button disabled={posting || !body.trim()}>{posting ? "Отправка…" : "Отправить ответ"}</button>
            </div>
            {postError && <p role="alert">{postError}</p>}
          </div>
        </form>}
        {notice && <p role="status">{notice}</p>}
      </div>
    </li>
  );
}
