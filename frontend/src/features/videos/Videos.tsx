import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../auth/Auth';
type Video = {
    id: string;
    title: string;
    description: string;
    visibility: 'public' | 'private';
    status: string;
    error_code: string | null;
    source_accepted: boolean;
    author: string;
    duration_ms: number | null;
};
const errors: Record<string, string> = { invalid_media: 'Файл повреждён или не содержит поддерживаемого видео', unsupported_format: 'Неподдерживаемый формат видео', media_limits: 'Превышены ограничения длительности, разрешения или частоты кадров', source_corrupt: 'Проверка целостности исходника не пройдена', processing_timeout: 'Превышено время обработки', dependency_unavailable: 'Временная недоступность БД или хранилища; обработка будет повторена, если остались попытки', storage_unavailable: 'Хранилище недоступно; повторите отправку', upload_expired: 'Срок загрузки истёк; отправьте файл повторно', upload_interrupted: 'Загрузка прервана; повторите отправку', empty_file: 'Нельзя загрузить пустой файл', file_too_large: 'Файл превышает допустимый размер', lease_expired: 'Обработка прервана; задача восстанавливается, если остались попытки' };
const message = (code: string) => errors[code] || 'Не удалось обработать файл. Попробуйте другой исходник.';
const labels: Record<string, string> = { awaiting_upload: 'Ожидает файл', uploading: 'Отправляется', queued: 'В очереди', processing: 'Обрабатывается', ready: 'Готово', failed: 'Ошибка' };
async function api(path: string, init?: RequestInit) {
    const response = await fetch(`/api${path}`, { credentials: 'same-origin', ...init });
    if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(response.status === 404 ? 'Видео не найдено или недоступно' : error?.message || `Ошибка запроса (${response.status})`);
    }
    return response.status === 204 ? null : response.json();
}
function useWrite() {
    const { session } = useAuth();
    return (path: string, method: string, body?: unknown) => api(path, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session?.csrf_token || '' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
function LoginRequired() { return <p>Для загрузки и управления видео <Link to="/login">войдите в аккаунт</Link>.</p>; }
function MetadataFields({ video }: {
    video?: Video;
}) {
    return <>
    <label>Название<input name="title" required maxLength={200} defaultValue={video?.title || ''}/></label>
    <label>Описание<textarea name="description" maxLength={5000} defaultValue={video?.description || ''}/></label>
    <label>Доступ<select aria-label="Доступ" name="visibility" defaultValue={video?.visibility || 'private'}><option value="private">Приватное</option><option value="public">Публичное</option></select></label>
  </>;
}
function metadata(form: HTMLFormElement) {
    const data = new FormData(form);
    return { title: String(data.get('title')).trim(), description: String(data.get('description')), visibility: String(data.get('visibility')) };
}
function uploadFile(id: string, file: File, csrf: string, progress: (value: number) => void) {
    return new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', `/api/videos/${id}/source`);
        xhr.timeout = 900000;
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.setRequestHeader('X-CSRF-Token', csrf);
        xhr.upload.onprogress = event => { if (event.lengthComputable)
            progress(Math.round(event.loaded / event.total * 100)); };
        xhr.onload = () => {
            if (xhr.status === 202)
                resolve();
            else {
                let message = 'Не удалось отправить файл';
                try {
                    message = JSON.parse(xhr.responseText).message || message;
                }
                catch { /* Nginx errors may use HTML. */ }
                reject(new Error(message));
            }
        };
        xhr.onerror = xhr.ontimeout = () => reject(new Error('Соединение прервано; статус будет проверен в Studio'));
        xhr.send(file);
    });
}
export function UploadPage() {
    const { session, loading } = useAuth();
    const write = useWrite();
    const [draft, setDraft] = useState<Video | null>(null);
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState(0);
    const [phase, setPhase] = useState('');
    const [error, setError] = useState('');
    useEffect(() => {
        if (!draft || !['queued', 'processing'].includes(draft.status))
            return;
        let active = true;
        const timer = setInterval(() => { api(`/me/videos/${draft.id}`).then(result => { if (active) {
            setDraft(result);
            setError('');
        } }).catch(e => { if (active)
            setError(e.message); }); }, 2000);
        return () => { active = false; clearInterval(timer); };
    }, [draft]);
    if (loading)
        return <p>Загрузка…</p>;
    if (!session)
        return <LoginRequired />;
    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const form = event.currentTarget;
        const file = (form.elements.namedItem('file') as HTMLInputElement).files?.[0];
        if (!file || !file.size || file.size > 500 * 1024 * 1024) {
            setError('Выберите непустой файл размером до 500 MiB');
            return;
        }
        setBusy(true);
        setError('');
        setProgress(0);
        setPhase('Создание черновика');
        let video: Video | null = draft;
        try {
            if (video)
                video = await api(`/me/videos/${video.id}`);
            if (video?.source_accepted) {
                setDraft(video);
                return;
            }
            video = video ? await write(`/videos/${video.id}`, 'PATCH', metadata(form)) : await write('/videos', 'POST', metadata(form));
            setDraft(video);
            setPhase('Отправка файла');
            await uploadFile(video!.id, file, session!.csrf_token, setProgress);
            setPhase('Файл отправлен. Ожидание обработки');
            setDraft(await api(`/me/videos/${video!.id}`));
        }
        catch (e) {
            setError((e as Error).message);
            if (video) {
                try {
                    setDraft(await api(`/me/videos/${video.id}`));
                }
                catch { /* Keep original upload error. */ }
            }
        }
        finally {
            setBusy(false);
        }
    }
    const accepted = draft?.source_accepted;
    return <section className="video-form"><h1>Загрузка видео</h1><p>MP4, MOV, WebM или MKV · до 500 MiB · от 1 до 600 секунд</p>
    {!accepted && <form onSubmit={submit}><MetadataFields /><label>Видеофайл<input name="file" type="file" accept=".mp4,.mov,.webm,.mkv" required disabled={busy}/></label><button disabled={busy}>{busy ? 'Отправка…' : 'Загрузить видео'}</button></form>}
    {phase && <p role="status">{phase}</p>}{busy && <><progress aria-label="Прогресс отправки" value={progress} max={100}/><span>{progress}% отправлено — это ещё не готовое видео</span></>}
    {draft && <p role="status">Статус: {labels[draft.status] || draft.status}{draft.error_code && ` — ${message(draft.error_code)}`}</p>}
    {draft?.status === 'ready' && <p><Link to={`/watch/${draft.id}`}>Смотреть готовое видео</Link></p>}
    {draft && <Link to="/studio">Перейти в Studio — управление доступом и метаданными</Link>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
function AccessEditor({ id }: {
    id: string;
}) {
    const write = useWrite();
    const [users, setUsers] = useState<{
        username: string;
    }[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => { let active = true; api(`/videos/${id}/access`).then(value => { if (active)
        setUsers(value); }).catch(e => { if (active)
        setError(e.message); }); return () => { active = false; }; }, [id]);
    async function change(username: string, remove = false) {
        setBusy(true);
        setError('');
        try {
            await write(`/videos/${id}/access/${encodeURIComponent(username)}`, remove ? 'DELETE' : 'PUT');
            setUsers(await api(`/videos/${id}/access`));
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    return <div className="access-editor"><h3>Разрешения</h3><form onSubmit={e => { e.preventDefault(); const form = e.currentTarget; void change(String(new FormData(form).get('username'))); }}><label>Username получателя<input name="username" required minLength={3} maxLength={32}/></label><button disabled={busy}>Предоставить доступ</button></form><ul>{users.map(user => <li key={user.username}>{user.username} <button disabled={busy} onClick={() => void change(user.username, true)}>Отозвать доступ</button></li>)}</ul><label>Ссылка для получателя<input aria-label="Ссылка для получателя" readOnly value={`${window.location.origin}/watch/${id}`}/></label>{error && <p role="alert">{error}</p>}</div>;
}
function StudioItem({ video, onUpdate }: {
    video: Video;
    onUpdate: () => void;
}) {
    const write = useWrite();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    async function save(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); setError(''); try {
        await write(`/videos/${video.id}`, 'PATCH', metadata(event.currentTarget));
        onUpdate();
    }
    catch (e) {
        setError((e as Error).message);
    }
    finally {
        setBusy(false);
    } }
    return <article className="studio-item"><h2>{video.title}</h2><p>{labels[video.status] || video.status} · {video.visibility === 'private' ? 'Приватное' : 'Публичное'}{video.error_code && ` · ${message(video.error_code)}`}</p>{video.status === 'ready' && <Link to={`/watch/${video.id}`}>Смотреть</Link>}{video.status === 'failed' && !video.source_accepted && <p>Исходник не принят. Повторите загрузку в форме ниже.</p>}
    <details><summary>Метаданные и доступ</summary><form key={`${video.id}-${video.visibility}-${video.title}`} onSubmit={save}><MetadataFields video={video}/><button disabled={busy}>Сохранить</button></form><AccessEditor id={video.id}/></details>{error && <p role="alert">{error}</p>}
    {video.status === 'failed' && !video.source_accepted && <RetrySource video={video} onUpdate={onUpdate}/>}
  </article>;
}
function RetrySource({ video, onUpdate }: {
    video: Video;
    onUpdate: () => void;
}) {
    const { session } = useAuth();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [progress, setProgress] = useState(0);
    async function retry(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const file = (event.currentTarget.elements.namedItem('retry') as HTMLInputElement).files?.[0]; if (!file)
        return; setBusy(true); setError(''); try {
        await uploadFile(video.id, file, session!.csrf_token, setProgress);
        onUpdate();
    }
    catch (e) {
        setError((e as Error).message);
        onUpdate();
    }
    finally {
        setBusy(false);
    } }
    return <form onSubmit={retry}><label>Повторная отправка<input name="retry" type="file" required disabled={busy}/></label><button disabled={busy}>Повторить загрузку</button>{busy && <progress value={progress} max={100}/>} {error && <p role="alert">{error}</p>}</form>;
}
export function StudioPage() {
    const {session, loading} = useAuth();
    const [videos, setVideos] = useState<Video[]>([]);
    const [error, setError] = useState('');
    const [cursor, setCursor] = useState<string | null>(null);
    const [busy, setBusy] = useState(true);
    const generation = useRef(0);
    const polling = useRef(true);

    useEffect(() => {
        let active = true;
        generation.current += 1;
        polling.current = true;
        setVideos([]);
        setCursor(null);
        setBusy(true);
        if (!session) return;
        const load = () => {
            if (!polling.current) return;
            const started = generation.current;
            api('/me/videos?limit=100').then(value => {
                if (active && generation.current === started && polling.current) {
                    setVideos(value.items);
                    setCursor(value.next_cursor);
                    setError('');
                }
            }).catch(e => {if (active && generation.current === started) setError(e.message)})
              .finally(() => {if (active && generation.current === started) setBusy(false)});
        };
        load();
        const timer = setInterval(load, 3000);
        return () => {active = false; clearInterval(timer)};
    }, [session]);

    async function loadPage(append: boolean) {
        const started = ++generation.current;
        polling.current = false;
        setBusy(true);
        try {
            const value = await api('/me/videos?limit=100' + (append ? `&cursor=${cursor}` : ''));
            if (generation.current !== started) return;
            setVideos(previous => append ? [...previous, ...value.items] : value.items);
            setCursor(value.next_cursor);
            setError('');
        } catch (e) {
            if (generation.current === started) setError((e as Error).message);
        } finally {
            if (generation.current === started) {
                setBusy(false);
                polling.current = !append;
            }
        }
    }
    if (loading) return <p>Загрузка…</p>;
    if (!session) return <LoginRequired/>;
    return <section><h1>Studio</h1><p><Link to="/upload">Загрузить видео</Link></p>
      <button disabled={busy} onClick={() => void loadPage(false)}>Обновить список</button>
      {error && <p role="alert">{error}</p>}
      {!videos.length && !busy && <p>У вас пока нет видео.</p>}
      {videos.map(video => <StudioItem key={video.id} video={video} onUpdate={() => void loadPage(false)}/>)}
      {cursor && <button disabled={busy} onClick={() => void loadPage(true)}>Ещё видео</button>}
    </section>;
}
export function WatchPage() {
    const { id } = useParams();
    const { session } = useAuth();
    const [video, setVideo] = useState<Video | null>(null);
    const [error, setError] = useState('');
    const [playError, setPlayError] = useState('');
    useEffect(() => { let active = true; setVideo(null); setError(''); setPlayError(''); api(`/videos/${id}`).then(value => { if (active)
        setVideo(value); }).catch(e => { if (active)
        setError(e.message); }); return () => { active = false; }; }, [id, session]);
    if (error)
        return <p role="alert">{error}</p>;
    if (!video)
        return <p>Загрузка видео…</p>;
    return <section className="watch-page"><video key={id} controls playsInline preload="metadata" poster={`/api/videos/${id}/thumbnail`} src={`/api/videos/${id}/media`} onError={() => setPlayError('Не удалось воспроизвести видео. Возможно, доступ изменился; обновите страницу.')} aria-label={video.title}/>{playError && <p role="alert">{playError}</p>}<h1>{video.title}</h1><p>Автор: {video.author}</p><p className="video-description">{video.description}</p>{session?.user.username === video.author && <Link to="/studio">Управлять видео в Studio</Link>}</section>;
}
