import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";
import { Icon } from "./Icon";
import { duration as formatDuration } from "./VideoCard";
import { useVideoViews } from "../features/videos/useVideoViews";
export function VideoPlayer({
  id,
  title,
  theater,
  mobile,
  onTheater,
  onViewCount,
}: {
  id: string;
  title: string;
  theater: boolean;
  mobile: boolean;
  onTheater: () => void;
  onViewCount?: (count: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null),
    root = useRef<HTMLDivElement>(null),
    settingsButton = useRef<HTMLButtonElement>(null),
    settingsPanel = useRef<HTMLDivElement>(null),
    timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useVideoViews(video, id, onViewCount);
  const [playing, setPlaying] = useState(false),
    [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [volume, setVolume] = useState(1),
    [muted, setMuted] = useState(false),
    [speed, setSpeed] = useState(1),
    [buffering, setBuffering] = useState(false),
    [error, setError] = useState(""),
    [visible, setVisible] = useState(true),
    [full, setFull] = useState(false),
    [settings, setSettings] = useState(false),
    [started, setStarted] = useState(false);
  const rateOptions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  const settingsId = useId();
  function closeSettings() {
    settingsButton.current?.focus();
    setSettings(false);
  }
  function reveal() {
    setVisible(true);
    clearTimeout(timer.current);
    if (mobile) return;
    timer.current = setTimeout(() => {
      if (
        video.current &&
        !video.current.paused &&
        !root.current?.querySelector(".player-controls :focus-visible") &&
        !settings
      )
        setVisible(false);
    }, 2600);
  }
  useEffect(() => {
    const changed = () => setFull(document.fullscreenElement === root.current);
    document.addEventListener("fullscreenchange", changed);
    return () => {
      document.removeEventListener("fullscreenchange", changed);
      clearTimeout(timer.current);
    };
  }, []);
  useEffect(() => {
    reveal();
    return () => clearTimeout(timer.current);
  }, [playing, settings, mobile]);
  useEffect(() => {
    if (settings)
      settingsPanel.current?.querySelector<HTMLElement>("button,select")?.focus();
  }, [settings, mobile]);
  async function toggle() {
    const el = video.current;
    if (!el) return;
    try {
      if (el.paused) {
        await el.play();
      } else el.pause();
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setError("Не удалось начать воспроизведение. Попробуйте ещё раз.");
    }
    reveal();
  }
  function seek(value: number) {
    const el = video.current;
    if (el && Number.isFinite(el.duration)) {
      el.currentTime = Math.min(Math.max(0, value), el.duration);
      setTime(el.currentTime);
    }
    reveal();
  }
  function changeVolume(value: number) {
    const el = video.current;
    if (!el) return;
    el.volume = Math.min(1, Math.max(0, value));
    if (el.volume > 0) el.muted = false;
  }
  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (root.current?.requestFullscreen)
        await root.current.requestFullscreen();
      else {
        const el = video.current as HTMLVideoElement & {
          webkitEnterFullscreen?: () => void;
        };
        el?.webkitEnterFullscreen?.();
      }
    } catch {
      setError("Полноэкранный режим недоступен в этом браузере.");
    }
    reveal();
  }
  function changeRate(value: number) {
    if (video.current) {
      video.current.playbackRate = value;
      setSpeed(value);
    }
  }
  function keyboard(e: KeyboardEvent<HTMLDivElement>) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "Escape" && settings) {
      e.preventDefault();
      e.stopPropagation();
      closeSettings();
      return;
    }
    const target = e.target as HTMLElement;
    if (target.matches("select,input,textarea")) return;
    const key = e.key.toLowerCase();
    if ((key === " " || key === "enter") && target.closest("button")) return;
    if (
      [
        " ",
        "k",
        "m",
        "f",
        "t",
        "j",
        "l",
        "arrowleft",
        "arrowright",
        "arrowup",
        "arrowdown",
        "home",
        "end",
        "<",
        ">",
      ].includes(key) ||
      /^\d$/.test(key)
    ) {
      e.preventDefault();
      reveal();
    } else return;
    if (key === " " || key === "k") void toggle();
    else if (key === "m" && video.current)
      video.current.muted = !video.current.muted;
    else if (key === "f") void fullscreen();
    else if (key === "t" && !mobile) onTheater();
    else if (["j", "l", "arrowleft", "arrowright"].includes(key))
      seek(
        time +
          (["j", "arrowleft"].includes(key) ? -1 : 1) *
            (["j", "l"].includes(key) ? 10 : 5),
      );
    else if (key === "arrowup" || key === "arrowdown")
      changeVolume(volume + (key === "arrowup" ? 0.05 : -0.05));
    else if (key === "home") seek(0);
    else if (key === "end") seek(duration);
    else if (/^\d$/.test(key)) seek((Number(key) / 10) * duration);
    else if (key === ">" || key === "<")
      changeRate(
        rateOptions[
          Math.max(
            0,
            Math.min(
              rateOptions.length - 1,
              rateOptions.indexOf(speed) + (key === ">" ? 1 : -1),
            ),
          )
        ],
      );
  }
  return (
    <div
      ref={root}
      className={`video-player${mobile || visible || !playing || settings ? " controls-visible" : ""}${full ? " is-fullscreen" : ""}`}
      tabIndex={0}
      role="region"
      aria-label={`Видеоплеер: ${title}`}
      onKeyDown={keyboard}
      onPointerMove={reveal}
      onPointerLeave={() => {
        if (
          playing &&
          !mobile &&
          !settings &&
          !root.current?.querySelector(".player-controls :focus-visible")
        )
          setVisible(false);
      }}
      onFocus={reveal}
    >
      <div className="player-stage">
        <video
          ref={video}
          playsInline
          preload="metadata"
          poster={`/api/videos/${id}/thumbnail`}
          src={`/api/videos/${id}/media`}
          aria-label={title}
          onClick={() => {
            if (!visible) {
              reveal();
              return;
            }
            void toggle();
          }}
          onDoubleClick={() => void fullscreen()}
          onLoadedMetadata={() => setDuration(video.current?.duration || 0)}
          onDurationChange={() =>
            setDuration(
              Number.isFinite(video.current?.duration)
                ? video.current!.duration
                : 0,
            )
          }
          onTimeUpdate={() => setTime(video.current?.currentTime || 0)}
          onPlay={() => {
            setPlaying(true);
            setStarted(true);
            setError("");
          }}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false);
            setVisible(true);
          }}
          onWaiting={() => setBuffering(true)}
          onSeeking={() => setBuffering(true)}
          onSeeked={() => setBuffering(false)}
          onCanPlay={() => setBuffering(false)}
          onPlaying={() => setBuffering(false)}
          onVolumeChange={() => {
            setVolume(video.current?.volume ?? 1);
            setMuted(!!video.current?.muted);
          }}
          onRateChange={() => setSpeed(video.current?.playbackRate || 1)}
          onError={() => {
            setBuffering(false);
            setError(
              "Не удалось воспроизвести видео. Возможно, доступ изменился; обновите страницу.",
            );
          }}
        />
        {!playing && !error && !buffering && (
          <button
            className="player-center"
            aria-label={
              started ? "Продолжить воспроизведение" : "Воспроизвести видео"
            }
            onClick={() => void toggle()}
          >
            <Icon name="play" size={36} />
          </button>
        )}
        {buffering && !error && (
          <span
            className="player-spinner"
            role="status"
            aria-label="Буферизация"
          />
        )}
        {error && (
          <div className="player-error" role="alert">
            {error}
            <button
              onClick={() => {
                setError("");
                video.current?.load();
              }}
            >
              Повторить воспроизведение
            </button>
          </div>
        )}
      </div>
      <div
        className="player-controls"
        inert={!(mobile || visible || !playing || settings)}
      >
        <input
          className="seek-bar"
          type="range"
          aria-label="Позиция воспроизведения"
          min={0}
          max={duration || 1}
          step={0.1}
          value={Math.min(time, duration || 1)}
          disabled={!duration}
          onChange={(e) => seek(Number(e.target.value))}
          aria-valuetext={`${formatDuration(time * 1000)} из ${formatDuration(duration * 1000)}`}
          style={
            {
              "--progress": `${duration ? (time / duration) * 100 : 0}%`,
            } as CSSProperties
          }
        />
        <div className="player-control-row">
          <button
            className="player-button"
            aria-label={playing ? "Пауза" : "Воспроизвести"}
            title={playing ? "Пауза (K)" : "Воспроизвести (K)"}
            onClick={() => void toggle()}
          >
            <Icon name={playing ? "pause" : "play"} />
          </button>
          {!mobile && (
            <>
              <button
                className="player-button"
                aria-label={
                  muted || volume === 0 ? "Включить звук" : "Выключить звук"
                }
                title="Звук (M)"
                onClick={() => {
                  if (video.current) {
                    if (volume === 0) changeVolume(1);
                    else video.current.muted = !video.current.muted;
                  }
                }}
              >
                <Icon name={muted || volume === 0 ? "mute" : "volume"} />
              </button>
              <input
                className="volume-bar"
                aria-label="Громкость"
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={muted ? 0 : volume}
                onChange={(e) => changeVolume(Number(e.target.value))}
              />
            </>
          )}
          <span className="player-time" aria-live="off">
            {formatDuration(time * 1000)} / {formatDuration(duration * 1000)}
          </span>
          <span className="player-spacer" />
          <div className="player-settings-wrap">
            <button
              ref={settingsButton}
              className="player-button"
              aria-label="Настройки воспроизведения"
              aria-expanded={settings}
              aria-controls={settings ? settingsId : undefined}
              onClick={() => setSettings((v) => !v)}
              title="Настройки воспроизведения"
            >
              <Icon name="settings" />
              {speed !== 1 && <span className="speed-badge">{speed}×</span>}
            </button>
            {settings && (
              <div
                className="player-settings"
                ref={settingsPanel}
                id={settingsId}
                role="group"
                aria-label="Настройки воспроизведения"
              >
                {mobile && (
                  <div className="player-volume-settings">
                    <button
                      className="player-button"
                      aria-label={
                        muted || volume === 0 ? "Включить звук" : "Выключить звук"
                      }
                      onClick={() => {
                        if (video.current) {
                          if (volume === 0) changeVolume(1);
                          else video.current.muted = !video.current.muted;
                        }
                      }}
                    >
                      <Icon
                        name={muted || volume === 0 ? "mute" : "volume"}
                      />
                    </button>
                    <label>
                      Громкость
                      <input
                        className="volume-bar"
                        aria-label="Громкость"
                        type="range"
                        min={0}
                        max={1}
                        step={0.05}
                        value={muted ? 0 : volume}
                        onChange={(e) => changeVolume(Number(e.target.value))}
                      />
                    </label>
                  </div>
                )}
                <label>
                  Скорость
                  <select
                    aria-label="Скорость"
                    value={speed}
                    onChange={(e) => {
                      changeRate(Number(e.target.value));
                      closeSettings();
                    }}
                  >
                    {rateOptions.map((value) => (
                      <option value={value} key={value}>
                        {value === 1 ? "Обычная" : `${value}×`}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </div>
          {!mobile && (
            <button
              className="player-button theater-button"
              aria-label="Широкий экран"
              aria-pressed={theater}
              title="Широкий экран (T)"
              onClick={onTheater}
            >
              <Icon name="theater" />
            </button>
          )}
          <button
            className="player-button"
            aria-label={
              full ? "Выйти из полноэкранного режима" : "Полноэкранный режим"
            }
            title="Полноэкранный режим (F)"
            onClick={() => void fullscreen()}
          >
            <Icon name={full ? "exitFullscreen" : "fullscreen"} />
          </button>
        </div>
      </div>
    </div>
  );
}
