export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {credentials: 'same-origin', ...init})
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new HttpError(response.status, response.status === 429 ? 'Слишком много запросов. Попробуйте позже.' : body?.message || 'Сервис временно недоступен')
  }
  return response.status === 204 ? undefined as T : response.json()
}

export type Page<T> = {items: T[]; next_cursor: string | null}
