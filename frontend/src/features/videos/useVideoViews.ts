import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import { useAuth } from "../auth/Auth";
import { HttpError } from "../../api/client";

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
  const { session, loading, restoreError } = useAuth();
  const onCountRef = useRef(onCount);
  onCountRef.current = onCount;
  const csrf = session?.csrf_token;
  useEffect(() => {
    const el = media.current;
    // The session cookie can already exist while its CSRF token is still loading.
    if (!el || loading || restoreError) return;
    const controller = new AbortController();
    let state: ViewState | undefined, sequence = 0, stopped = false;
    let queue = Promise.resolve(), lastSent = 0;
    let failures = 0, retryAfter = 0, generation = 0, recovering = false;
    const evidence = (): Evidence => ({
      position_ms: Math.max(0, Math.round(el.currentTime * 1000)),
      rate: el.playbackRate,
    });
    const post = async (path: string, body: object): Promise<ViewState> => {
      const response = await fetch(`/api/videos/${id}/views${path}`, {
        method: "POST", credentials: "same-origin",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
        headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new HttpError(response.status, "View registration unavailable");
      return response.json();
    };
    const send = (event: PlaybackEvent) => {
      if (stopped || controller.signal.aborted || performance.now() < retryAfter) return;
      const data = evidence();
      // Only an actual playing event can initiate a session.
      // After a transient failure, live progress may establish a fresh baseline.
      if (!state && sequence === 0 && event !== "resume" &&
          !(recovering && event === "progress")) return;
      const currentSequence = ++sequence;
      const currentGeneration = generation;
      lastSent = performance.now();
      queue = queue.then(async () => {
        if (stopped || controller.signal.aborted || currentGeneration !== generation) return;
        if (!state) state = await post("", data);
        else state = await post(`/${state.session_id}`, { ...data, sequence: currentSequence, event });
        if (!controller.signal.aborted && state.completed) {
          stopped = true;
          onCountRef.current?.(state.views_count);
        }
      }).catch((error: unknown) => {
        if (controller.signal.aborted || currentGeneration !== generation) return;
        const transient = error instanceof HttpError
          ? error.status === 408 || error.status === 429 || error.status >= 500
          : error instanceof TypeError || (error instanceof DOMException && error.name === "TimeoutError");
        failures++;
        generation++;
        // Discard queued evidence and uncertain intervals (e.g. a lost pause or
        // response). Never credit outage time. At most three event-driven retries
        // per mounted player, spaced 1/2/4 seconds apart; no timer or polling.
        state = undefined;
        sequence = 0;
        recovering = transient && failures <= 3;
        stopped = !recovering;
        retryAfter = performance.now() + 1000 * 2 ** (failures - 1);
        // Status only: do not log cookies, tokens, request bodies or session IDs.
        console.warn("Video view registration unavailable", {
          status: error instanceof HttpError ? error.status : "network",
          recovery: recovering ? "next playback event" : "stopped",
        });
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
    // Restore accounting if auth finished loading during real playback.
    if (!el.paused && !el.seeking && el.readyState >= 2) playing();
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
  }, [id, session?.user.id, csrf, loading, restoreError, media]);
}
