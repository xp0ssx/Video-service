import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import type { Page } from '../../api/client'
import { useAuth } from '../auth/Auth'

const statusLabel:Record<string,string>={ready:'Готово',awaiting_upload:'Ожидает файл',uploading:'Отправляется',queued:'В очереди',processing:'Обрабатывается',failed:'Ошибка'}

type Channel = {id:string; username:string; title:string; description:string; owned_by_me:boolean; subscribers_count:number; subscribed_by_me:boolean}
type Video = {id:string; title:string; watch_url?:string; thumbnail_url?:string; status?:string}
type Playlist = {id:string; title:string; description:string; username:string; owned_by_me:boolean; items:Video[]; next_cursor:string|null}
function useWrite() {
  const {session}=useAuth()
  return (path:string,method:string,body?:unknown)=>api(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':session?.csrf_token||''},...(body===undefined?{}:{body:JSON.stringify(body)})})
}
function useResource<T>(path:string) {
  const [value,setValue]=useState<T|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0)
  const {session}=useAuth()
  const reload=()=>setRevision(n=>n+1)
  useEffect(()=>{const controller=new AbortController();setValue(null);setError('');api<T>(path,{signal:controller.signal}).then(data=>{if(!controller.signal.aborted)setValue(data)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)});return()=>controller.abort()},[path,revision,session])
  useEffect(()=>{const refresh=()=>setRevision(n=>n+1);window.addEventListener('focus',refresh);window.addEventListener('subscriptions-changed',refresh);return()=>{window.removeEventListener('focus',refresh);window.removeEventListener('subscriptions-changed',refresh)}},[])
  return {value,error,reload}
}
function LoginRequired(){return <p><Link to="/login">Войдите в аккаунт</Link>, чтобы управлять каналом и плейлистами.</p>}
function MetadataForm({title='',description='',onSave,label}:{title?:string;description?:string;onSave:(data:{title:string;description:string})=>Promise<void>;label:string}) {
  const [error,setError]=useState(''),[busy,setBusy]=useState(false)
  async function submit(event:FormEvent<HTMLFormElement>){event.preventDefault();const form=new FormData(event.currentTarget);setBusy(true);setError('');try{await onSave({title:String(form.get('title')).trim(),description:String(form.get('description'))})}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  return <form className="metadata-form" onSubmit={submit}><label>Название<input name="title" required maxLength={200} defaultValue={title}/></label><label>Описание<textarea name="description" maxLength={5000} defaultValue={description}/></label>{error&&<p role="alert">{error}</p>}<button disabled={busy}>{label}</button></form>
}
function Listing<T extends {id:string}>({path,render}:{path:string;render:(item:T)=>React.ReactNode}) {
  const [items,setItems]=useState<T[]>([]),[cursor,setCursor]=useState<string|null>(null),[busy,setBusy]=useState(true),[error,setError]=useState('')
  const [page,setPage]=useState<string|null>(null),[revision,setRevision]=useState(0)
  useEffect(()=>{const controller=new AbortController();setBusy(true);setError('');api<Page<T>>(`${path}${path.includes('?')?'&':'?'}limit=20${page?'&cursor='+encodeURIComponent(page):''}`,{signal:controller.signal}).then(data=>{if(controller.signal.aborted)return;setItems(old=>page?[...new Map([...old,...data.items].map(item=>[item.id,item])).values()]:data.items);setCursor(data.next_cursor)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)}).finally(()=>{if(!controller.signal.aborted)setBusy(false)});return()=>controller.abort()},[path,page,revision])
  return <><div className="channel-list">{items.map(item=><article key={item.id}>{render(item)}</article>)}</div>{busy&&<p role="status">Загрузка…</p>}{error&&<p role="alert">{error}<button onClick={()=>setRevision(n=>n+1)}>Повторить</button></p>}{!busy&&!error&&!items.length&&<p>Здесь пока ничего нет.</p>}{cursor&&!error&&<button disabled={busy} onClick={()=>setPage(cursor)}>Показать ещё</button>}</>
}
const playlistLink=(item:Playlist)=><Link to={`/playlists/${item.id}`}>{item.title}</Link>
export function ChannelPage(){const {username}=useParams();return <ChannelContent key={username} username={username||''}/>}
function ChannelContent({username}:{username:string}) {
  const {session}=useAuth(),write=useWrite(),{value:channel,error,reload}=useResource<Channel>('/channels/'+encodeURIComponent(username))
  const [busy,setBusy]=useState(false),[actionError,setActionError]=useState('')
  async function subscribe(){if(!channel)return;setBusy(true);setActionError('');try{await write(`/channels/${encodeURIComponent(username)}/subscription`,channel.subscribed_by_me?'DELETE':'PUT');window.dispatchEvent(new Event('subscriptions-changed'))}catch(e){setActionError((e as Error).message)}finally{setBusy(false)}}
  if(error)return <p role="alert">{error}</p>;if(!channel)return <p>Загрузка канала…</p>
  return <section className="channel-page"><h1>{channel.title}</h1><p>Автор: {channel.username}</p><p className="video-description">{channel.description}</p><p>Подписчиков: <span data-testid="subscribers-count">{channel.subscribers_count}</span></p>{!channel.owned_by_me&&(session?<button disabled={busy} onClick={()=>void subscribe()}>{channel.subscribed_by_me?'Отписаться':'Подписаться'}</button>:<Link to="/login">Войдите, чтобы подписаться</Link>)}{actionError&&<p role="alert">{actionError}</p>}{channel.owned_by_me&&<details><summary>Редактировать канал</summary><MetadataForm title={channel.title} description={channel.description} label="Сохранить канал" onSave={async data=>{await write('/me/channel','PATCH',data);reload()}}/><Link to="/studio/playlists">Управлять плейлистами</Link></details>}<h2>Видео</h2><Listing<Video> path={`/channels/${encodeURIComponent(username)}/videos`} render={item=><Link to={item.watch_url||`/watch/${item.id}`}><img className="channel-thumbnail" src={item.thumbnail_url} alt=""/>{item.title}</Link>}/><h2>Плейлисты</h2><Listing<Playlist> path={`/channels/${encodeURIComponent(username)}/playlists`} render={playlistLink}/></section>
}
export function SubscriptionsPage(){const {session,loading}=useAuth();if(loading)return <p>Загрузка…</p>;return <section><h1>Подписки</h1>{session?<Listing<Channel> key={session.user.id} path="/me/subscriptions" render={item=><Link to={`/channels/${item.username}`}>{item.title} — @{item.username}</Link>}/>:<LoginRequired/>}</section>}
export function PlaylistPage(){const {id}=useParams();return <PlaylistContent key={id} id={id||''}/>}
function PlaylistContent({id}:{id:string}) {
  const {value,error}=useResource<Playlist>('/playlists/'+id)
  if(error)return <p role="alert">{error}</p>;if(!value)return <p>Загрузка плейлиста…</p>
  return <section className="playlist-page"><h1>{value.title}</h1><p className="video-description">{value.description}</p><Link to={`/channels/${value.username}`}>Канал: {value.username}</Link>{value.owned_by_me&&<p><Link to={`/studio/playlists/${id}`}>Редактировать плейлист</Link></p>}<Listing<Video> key={value.id} path={`/playlists/${id}`} render={item=><Link to={`/watch/${item.id}`}>{item.title}</Link>}/></section>
}
async function all<T>(path:string):Promise<T[]> {let cursor:string|null=null;const result:T[]=[];do{const page:Page<T>=await api<Page<T>>(path+(path.includes('?')?'&':'?')+'limit=100'+(cursor?'&cursor='+encodeURIComponent(cursor):''));result.push(...page.items);cursor=page.next_cursor}while(cursor);return result}
export function PlaylistStudio(){const {session,loading}=useAuth();const {id}=useParams();if(loading)return <p>Загрузка…</p>;if(!session)return <LoginRequired/>;return id?<PlaylistEditor key={`${id}:${session.user.id}`} id={id}/>:<PlaylistCreate key={session.user.id} username={session.user.username}/>}
function PlaylistCreate({username}:{username:string}){const write=useWrite(),navigate=useNavigate();return <section className="playlist-page"><h1>Мои плейлисты</h1><MetadataForm label="Создать плейлист" onSave={async data=>{const created=await write('/playlists','POST',data) as Playlist;navigate('/studio/playlists/'+created.id)}}/><Listing<Playlist> path={`/channels/${username}/playlists`} render={item=><Link to={`/studio/playlists/${item.id}`}>{item.title}</Link>}/></section>}
function PlaylistEditor({id}:{id:string}) {
  const write=useWrite(),navigate=useNavigate(),{value,error,reload}=useResource<Playlist>('/me/playlists/'+id)
  const [items,setItems]=useState<Video[]>([]),[videos,setVideos]=useState<Video[]>([]),[ready,setReady]=useState(false),[actionError,setActionError]=useState(''),[busy,setBusy]=useState(false)
  useEffect(()=>{let active=true;setReady(false);if(value)Promise.all([all<Video>('/me/playlists/'+id),all<Video>('/me/videos')]).then(([entries,own])=>{if(active){setItems(entries);setVideos(own);setReady(true)}}).catch(e=>{if(active)setActionError(e.message)});return()=>{active=false}},[id,value])
  async function action(path:string,method:string,body?:unknown){setBusy(true);setActionError('');try{await write(path,method,body);reload()}catch(e){setActionError((e as Error).message)}finally{setBusy(false)}}
  async function move(index:number,step:number){const ordered=[...items];[ordered[index],ordered[index+step]]=[ordered[index+step],ordered[index]];await action(`/playlists/${id}/order`,'PUT',{video_ids:ordered.map(item=>item.id)})}
  if(error)return <p role="alert">{error}</p>;if(!value)return <p>Загрузка…</p>
  return <section className="playlist-page"><h1>Редактировать плейлист</h1><Link to={`/playlists/${id}`}>Открыть плейлист</Link><MetadataForm key={value.title+value.description} title={value.title} description={value.description} label="Сохранить плейлист" onSave={async data=>{await write('/playlists/'+id,'PATCH',data);reload()}}/>{actionError&&<p role="alert">{actionError}</p>}{!ready&&<p role="status">Загрузка элементов…</p>}<fieldset disabled={busy||!ready}><legend>Видео плейлиста</legend><ol className="playlist-editor">{items.map((item,index)=><li key={item.id}><span>{item.title} ({statusLabel[item.status||'']||'Недоступно'})</span><button aria-label={`Выше: ${item.title}`} disabled={!index} onClick={()=>void move(index,-1)}>↑</button><button aria-label={`Ниже: ${item.title}`} disabled={index===items.length-1} onClick={()=>void move(index,1)}>↓</button><button onClick={()=>void action(`/playlists/${id}/items/${item.id}`,'DELETE')}>Удалить из плейлиста: {item.title}</button></li>)}</ol><form onSubmit={event=>{event.preventDefault();const video=String(new FormData(event.currentTarget).get('video'));if(video)void action(`/playlists/${id}/items/${video}`,'PUT')}}><label>Своё видео<select name="video" required><option value="">Выберите видео</option>{videos.filter(video=>!items.some(item=>item.id===video.id)).map(video=><option key={video.id} value={video.id}>{video.title}</option>)}</select></label><button>Добавить видео</button></form></fieldset><button className="delete-playlist" disabled={busy} onClick={async()=>{if(!window.confirm('Удалить плейлист? Видео сохранятся.'))return;setBusy(true);try{await write('/playlists/'+id,'DELETE');navigate('/studio/playlists')}catch(e){setActionError((e as Error).message);setBusy(false)}}}>Удалить плейлист</button></section>
}
