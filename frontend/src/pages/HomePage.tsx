import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Page } from "../api/client";

import { VideoCard, VideoSkeletons } from "../components/VideoCard";
import type { VideoCardData as Card } from "../components/VideoCard";
import { Icon } from "../components/Icon";
function Feed({ query, results }: { query: string; results: boolean }) {
  const [items, setItems] = useState<Card[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  async function load(after: string | null) {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ q: query, limit: "20" });
    if (after) params.set("cursor", after);
    try {
      const page = await api<Page<Card>>(`/videos?${params}`, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setItems((previous) =>
        after
          ? [
              ...new Map(
                [...previous, ...page.items].map((item) => [item.id, item]),
              ).values(),
            ]
          : page.items,
      );
      setCursor(page.next_cursor);
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  useEffect(() => {
    void load(null);
    return () => pending.current?.abort();
  }, [query]);
  return (
    <section
      className={`feed-page${results ? " search-results" : ""}`}
      aria-busy={loading}
    >
      {results ? (
        <div className="page-heading">
          <h1>{query ? `Результаты: ${query}` : "Все видео"}</h1>
        </div>
      ) : (
        <h1 className="sr-only">Главная</h1>
      )}
      {error && (
        <div className="feed-error" role="alert">
          <p>{error}</p>
          <button onClick={() => void load(items.length ? cursor : null)}>
            Повторить
          </button>
        </div>
      )}
      <div className="video-grid">
        {items.map((item) => (
          <VideoCard key={item.id} video={item} />
        ))}
      </div>
      {loading && <VideoSkeletons />}
      {!loading && !error && !items.length && (
        <div className="empty-state">
          <span className="empty-icon">
            <Icon name={results ? "search" : "video"} size={56} />
          </span>
          <h2>{results ? "Ничего не найдено" : "Здесь пока нет видео"}</h2>
          <p>
            {results
              ? "Попробуйте другое название."
              : "Загрузите первое видео и поделитесь своей историей."}
          </p>
          <Link className="button-link" to={results ? "/" : "/upload"}>
            {results ? "На главную" : "Загрузить видео"}
          </Link>
        </div>
      )}
      {cursor && !error && (
        <button
          className="load-more secondary"
          disabled={loading}
          onClick={() => void load(cursor)}
        >
          Ещё видео
        </button>
      )}
    </section>
  );
}
export function HomePage({ results = false }: { results?: boolean }) {
  const [params] = useSearchParams();
  const query = results ? (params.get("q") || "").trim() : "";
  return <Feed key={`${results}:${query}`} query={query} results={results} />;
}
