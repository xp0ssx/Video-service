import { useState } from "react";
import { Link } from "react-router-dom";
import { Avatar, Icon } from "./Icon";
export type VideoCardData = {
  id: string;
  title: string;
  duration_ms?: number | null;
  published_at?: string;
  channel?: { id?: string; title: string; username: string };
  thumbnail_url?: string;
  watch_url?: string;
};
export function duration(value: number | null | undefined) {
  const seconds = Math.floor((value || 0) / 1000),
    minutes = Math.floor(seconds / 60);
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
    : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}
export function Thumbnail({ video }: { video: VideoCardData }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="card-thumbnail">
      <span className="thumbnail-placeholder">
        <Icon name="video" size={36} />
      </span>
      {video.thumbnail_url && !failed && (
        <img
          src={video.thumbnail_url}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
        />
      )}
      {video.duration_ms != null && (
        <span className="duration">{duration(video.duration_ms)}</span>
      )}
    </div>
  );
}
export function VideoCard({
  video,
  compact = false,
}: {
  video: VideoCardData;
  compact?: boolean;
}) {
  const channel = video.channel;
  return (
    <article className={`video-card${compact ? " compact-card" : ""}`}>
      <Link
        className="thumbnail-link"
        to={video.watch_url || `/watch/${video.id}`}
        aria-label={`Смотреть: ${video.title}`}
        tabIndex={-1}
      >
        <Thumbnail key={video.thumbnail_url} video={video} />
      </Link>
      <div className="card-details">
        {channel && !compact && (
          <Link
            className="card-avatar"
            to={`/channels/${channel.username}`}
            aria-label={`Канал: ${channel.title}`}
            tabIndex={-1}
          >
            <Avatar name={channel.username} />
          </Link>
        )}
        <div className="card-copy">
          <h2>
            <Link to={video.watch_url || `/watch/${video.id}`}>
              {video.title}
            </Link>
          </h2>
          {channel && (
            <p className="card-channel">
              <Link to={`/channels/${channel.username}`}>
                <span className="inline-channel-avatar">
                  <Avatar name={channel.username} size={24} />
                </span>
                {channel.title}
              </Link>
            </p>
          )}
          {video.published_at && (
            <p className="card-date">
              <time dateTime={video.published_at}>
                {new Date(video.published_at).toLocaleDateString("ru-RU", {
                  day: "numeric",
                  month: "long",
                  year: "numeric",
                })}
              </time>
            </p>
          )}
        </div>
      </div>
    </article>
  );
}
export function VideoSkeletons() {
  return (
    <div
      className="video-grid skeleton-grid"
      role="status"
      aria-label="Загрузка видео…"
    >
      {Array.from({ length: 6 }, (_, i) => (
        <div className="skeleton-card" key={i}>
          <div className="skeleton skeleton-thumb" />
          <div className="skeleton skeleton-line" />
          <div className="skeleton skeleton-line short" />
        </div>
      ))}
    </div>
  );
}
