import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Page } from '../api/client'

type Card = {id: string; title: string; duration_ms: number | null; published_at: string; channel: {id: string; title: string; username: string}; thumbnail_url: string; watch_url: string}
function duration(value: number | null) {
  const seconds = Math.floor((value || 0) / 1000)
  const minutes = Math.floor(seconds / 60)
  return minutes >= 60 ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}` : `${minutes}:${String(seconds % 60).padStart(2,'0')}`
}
function Feed({query, results}: {query: string; results: boolean}) {
  const [items, setItems] = useState<Card[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const pending = useRef<AbortController | null>(null)
  async function load(after: string | null) {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setLoading(true); setError('')
    const params = new URLSearchParams({q: query, limit: '20'})
    if (after) params.set('cursor', after)
    try {
      const page = await api<Page<Card>>(`/videos?${params}`, {signal: controller.signal})
      if (controller.signal.aborted) return
      setItems(previous => after ? [...new Map([...previous, ...page.items].map(item => [item.id, item])).values()] : page.items)
      setCursor(page.next_cursor)
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message)
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }
  useEffect(() => { void load(null); return () => pending.current?.abort() }, [query])
  return <section className="feed-page" aria-busy={loading}>
    <div className="page-heading"><div><p className="eyebrow">СМОТРИТЕ. ДЕЛИТЕСЬ. НАХОДИТЕ СВОЁ.</p><h1>{results ? query ? `Результаты: ${query}` : 'Все видео' : 'Главная'}</h1></div><span className="category-label">{results ? 'Поиск' : 'Новые видео'}</span></div>
    {error && <div className="feed-error" role="alert"><p>{error}</p><button onClick={() => void load(items.length ? cursor : null)}>Повторить</button></div>}
    <div className="video-grid">{items.map(item => <article className="video-card" key={item.id}>
      <Link to={item.watch_url} aria-label={`Смотреть: ${item.title}`}><div className="card-thumbnail"><span className="thumbnail-placeholder" aria-hidden="true">▶</span><img src={item.thumbnail_url} alt="" loading="lazy" onError={e => {e.currentTarget.style.display = 'none'}}/><span className="duration">{duration(item.duration_ms)}</span></div><h2>{item.title}</h2></Link>
      <div className="card-channel"><span className="channel-avatar" aria-hidden="true">{item.channel.title.slice(0,1).toUpperCase()}</span><div><p><Link to={`/channels/${item.channel.username}`}>{item.channel.title}</Link></p><p className="card-date"><time dateTime={item.published_at}>{new Date(item.published_at).toLocaleDateString('ru-RU')}</time></p></div></div>
    </article>)}</div>
    {loading && <p role="status" className="feed-status">Загрузка видео…</p>}
    {!loading && !error && !items.length && <div className="empty-state"><div className="empty-art" aria-hidden="true"><span className="video-outline">▶</span></div><h2>{results ? 'Ничего не найдено' : 'Здесь пока нет видео'}</h2><p>{results ? 'Попробуйте другое название.' : 'Загрузите первое видео и поделитесь своей историей.'}</p><Link className="inline-action" to={results ? '/' : '/upload'}>{results ? 'На главную' : 'Загрузить видео'}</Link></div>}
    {cursor && !error && <button className="load-more" disabled={loading} onClick={() => void load(cursor)}>Ещё видео</button>}
  </section>
}
export function HomePage({results = false}: {results?: boolean}) {
  const [params] = useSearchParams()
  const query = results ? (params.get('q') || '').trim() : ''
  return <Feed key={`${results}:${query}`} query={query} results={results}/>
}
