import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api, HttpError } from '../../api/client'
import type { Page } from '../../api/client'
import { useAuth } from '../auth/Auth'

type Comment = {id: string; body: string; created_at: string; author: {id: string; username: string}}
type LikeState = {likes_count: number; liked_by_me: boolean}
function merge(previous: Comment[], incoming: Comment[]) {
  return [...new Map([...previous, ...incoming].map(item => [item.id, item])).values()].sort((a,b) => a.created_at === b.created_at ? a.id < b.id ? -1 : a.id === b.id ? 0 : 1 : a.created_at < b.created_at ? -1 : 1)
}
export function VideoInteractions({id, initial, onUnavailable}: {id: string; initial: LikeState; onUnavailable: () => void}) {
  const {session} = useAuth()
  const [likes, setLikes] = useState(initial)
  const [comments, setComments] = useState<Comment[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [likeBusy, setLikeBusy] = useState(false)
  const [postBusy, setPostBusy] = useState(false)
  const [body, setBody] = useState('')
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(false)
  const pending = useRef<AbortController | null>(null)
  function failure(e: unknown, list = false) {
    if (!mounted.current) return
    if (e instanceof HttpError && e.status === 404) {setComments([]); onUnavailable(); return}
    const message = e instanceof HttpError && e.status === 401 ? 'Сессия истекла. Войдите снова.' : (e as Error).message
    if (list) setListError(message); else setError(message)
  }
  async function load(after: string | null) {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setLoading(true); setListError('')
    const params = new URLSearchParams({limit: '20'})
    if (after) params.set('cursor', after)
    try {
      const page = await api<Page<Comment>>(`/videos/${id}/comments?${params}`, {signal: controller.signal})
      if (!mounted.current || controller.signal.aborted) return
      setComments(previous => merge(previous, page.items)); setCursor(page.next_cursor)
    } catch (e) {if (!controller.signal.aborted) failure(e, true)}
    finally {if (mounted.current && !controller.signal.aborted) setLoading(false)}
  }
  useEffect(() => {mounted.current = true; void load(null); return () => {mounted.current = false; pending.current?.abort()}}, [id])
  async function toggle() {
    if (!session) return
    setLikeBusy(true); setError('')
    try {
      const result = await api<LikeState>(`/videos/${id}/like`, {method: likes.liked_by_me ? 'DELETE' : 'PUT', headers: {'X-CSRF-Token': session.csrf_token}})
      if (mounted.current) setLikes(result)
    } catch (e) {failure(e)} finally {if (mounted.current) setLikeBusy(false)}
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!session) return
    const text = body.trim()
    if (!text || text.length > 2000) {setError('Комментарий должен содержать от 1 до 2000 символов'); return}
    setPostBusy(true); setError(''); setNotice('')
    try {
      const comment = await api<Comment>(`/videos/${id}/comments`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token}, body: JSON.stringify({body: text})})
      if (mounted.current) {setComments(previous => merge(previous, [comment])); setBody(''); setNotice('Комментарий добавлен')}
    } catch (e) {failure(e)} finally {if (mounted.current) setPostBusy(false)}
  }
  return <section className="video-interactions" aria-label="Лайки и комментарии">
    <div className="like-row"><button type="button" className="like-button" aria-label="Нравится" aria-pressed={likes.liked_by_me} disabled={!session || likeBusy} onClick={() => void toggle()}>♡ <span data-testid="likes-count">{likes.likes_count}</span></button><span>{session ? `Вы вошли как ${session.user.username}` : <Link to="/login">Войдите, чтобы поставить лайк и оставить комментарий</Link>}</span></div>
    {error && <p role="alert">{error}</p>}
    <h2>Комментарии</h2>
    {session && <form className="comment-form" onSubmit={submit}><label>Ваш комментарий<textarea name="comment" required maxLength={2000} value={body} onChange={e => setBody(e.target.value)} disabled={postBusy}/></label><button disabled={postBusy || !body.trim()}>{postBusy ? 'Отправка…' : 'Отправить комментарий'}</button></form>}
    {notice && <p role="status">{notice}</p>}
    <ol className="comments-list">{comments.map(comment => <li key={comment.id} className="comment"><div className="comment-meta"><strong>{comment.author.username}</strong><time dateTime={comment.created_at}>{new Date(comment.created_at).toLocaleString('ru-RU')}</time></div><p className="comment-body">{comment.body}</p></li>)}</ol>
    {loading && <p role="status">Загрузка комментариев…</p>}
    {listError && <div role="alert"><p>{listError}</p><button onClick={() => void load(cursor)}>Повторить загрузку комментариев</button></div>}
    {!loading && !listError && !comments.length && <p>Пока нет комментариев. Начните обсуждение.</p>}
    {cursor && !listError && <button disabled={loading} onClick={() => void load(cursor)}>Ещё комментарии</button>}
  </section>
}
