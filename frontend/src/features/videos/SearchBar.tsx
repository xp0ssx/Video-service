import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

export function SearchBar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  useEffect(() => {setQuery(new URLSearchParams(location.search).get('q') || '')}, [location.pathname, location.search])
  return <form className="search-bar" role="search" onSubmit={e => {e.preventDefault(); const value = query.trim(); navigate(value ? `/results?${new URLSearchParams({q: value})}` : '/')}}>
    <input aria-label="Поиск видео" placeholder="Введите название видео" name="q" type="search" maxLength={200} value={query} onChange={e => setQuery(e.target.value)}/><button aria-label="Найти видео" type="submit">⌕</button>
  </form>
}
