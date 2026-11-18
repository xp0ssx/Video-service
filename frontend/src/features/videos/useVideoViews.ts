import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { useAuth } from "../auth/Auth";

type Evidence = { position_ms: number; rate: number };
type ViewState = { session_id: string; threshold_ms: number; completed: boolean; views_count: number };
type PlaybackEvent = "progress" | "pause" | "resume" | "seek" | "rate";

// Driven by real media events; no idle timer or polling. The server caps every
// interval against its clock and owns both qualification and deduplication.
export function useVideoViews(
  media: RefObject<HTMLVideoElement | null>,
  id: string,
  onCount?: (count: number) => void,
) {
  const { session } = useAuth();
  const onCountRef = useRef(onCount);
  onCountRef.current = onCount;
  const csrf = session?.csrf_token;
  useEffect(() => {
    const el = media.current;
    if (!el) return;
    const controller = new AbortController();
    let state: ViewState | undefined, sequence = 0, stopped = false;
    let queue = Promise.resolve(), lastSent = 0;
    const evidence = (): Evidence => ({
      position_ms: Math.max(0, Math.round(el.currentTime * 1000)),
      rate: el.playbackRate,
    });
    const post = async (path: string, body: object): Promise<ViewState> => {
      const response = await fetch(`/api/videos/${id}/views${path}`, {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error("View registration unavailable");
      return response.json();
    };
    const send = (event: PlaybackEvent) => {
      if (stopped || controller.signal.aborted) return;
      const data = evidence();
      // Only an actual playing event can initiate a session.
      if (!state && event !== "resume" && sequence === 0) return;
      const currentSequence = ++sequence;
      lastSent = performance.now();
      queue = queue.then(async () => {
        if (stopped || controller.signal.aborted) return;
        if (!state) state = await post("", data);
        else state = await post(`/${state.session_id}`, { ...data, sequence: currentSequence, event });
        if (!controller.signal.aborted && state.completed) {
          stopped = true;
          onCountRef.current?.(state.views_count);
        }
      }).catch(() => {
        // Analytics failure must never interrupt playback or produce retries on
        // every render. A later page visit can attempt registration again.
        stopped = true;
      });
    };
    const playing = () => send("resume");
    const pause = () => send("pause");
    const seek = () => send("seek");
    const seeked = () => { if (!el.paused) send("resume"); };
    const rate = () => send("rate");
    const progress = () => {
      if (el.paused || el.seeking || el.readyState < 2) return;
      const interval = Math.min(2000, Math.max(100, (state?.threshold_ms ?? 1000) / 2));
      if (performance.now() - lastSent >= interval) send("progress");
    };
    el.addEventListener("playing", playing);
    el.addEventListener("timeupdate", progress);
    el.addEventListener("pause", pause);
    el.addEventListener("ended", pause);
    el.addEventListener("waiting", pause);
    el.addEventListener("seeking", seek);
    el.addEventListener("seeked", seeked);
    el.addEventListener("ratechange", rate);
    return () => {
      controller.abort();
      el.removeEventListener("playing", playing);
      el.removeEventListener("timeupdate", progress);
      el.removeEventListener("pause", pause);
      el.removeEventListener("ended", pause);
      el.removeEventListener("waiting", pause);
      el.removeEventListener("seeking", seek);
      el.removeEventListener("seeked", seeked);
      el.removeEventListener("ratechange", rate);
    };
  }, [id, csrf, media]);
}
