import { useEffect, useId, useRef, useState } from "react";
import { api, HttpError } from "../../api/client";
import { useAuth } from "../auth/Auth";

export function DeleteVideoDialog({ id, title, onCancel, onDeleted }: {
  id: string;
  title: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const { session } = useAuth();
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const labelId = useId(), descriptionId = useId();
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  function confirmed() {
    dialog.current?.close();
    // Notify an already open watch tab, as well as the current SPA.
    window.dispatchEvent(new CustomEvent("video-deleted", { detail: id }));
    try { localStorage.setItem("video-service:deleted", JSON.stringify({ id, at: Date.now() })); }
    catch { /* Other clients also revalidate with the server. */ }
    onDeleted();
  }

  function cancel() {
    if (pending.current) return;
    dialog.current?.close();
    onCancel();
  }

  async function remove() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await api(`/videos/${id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": session?.csrf_token || "" },
      });
      confirmed();
    } catch (failure) {
      // An interrupted response may follow a successful commit. Only an
      // authenticated Studio 404 confirms absence; failed reconciliation keeps it.
      try {
        await api(`/me/videos/${id}`);
        setError(failure instanceof HttpError
          ? failure.message
          : "Связь прервалась. Видео ещё доступно в Studio. Повторите удаление.");
      } catch (check) {
        if (check instanceof HttpError && check.status === 404) {
          confirmed();
          return;
        }
        setError("Не удалось проверить результат удаления. Видео сохранено в списке до сверки с сервером. Повторите попытку.");
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return (
    <dialog ref={dialog} className="delete-video-dialog" aria-labelledby={labelId}
      aria-describedby={descriptionId} aria-busy={busy}
      onKeyDown={event => {
        if (event.key !== "Tab") return;
        const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"));
        const first = buttons[0], last = buttons.at(-1);
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }}
      onCancel={event => { event.preventDefault(); cancel(); }}>
      <h2 id={labelId}>Удалить видео?</h2>
      <p className="delete-video-title">«{title}»</p>
      <p id={descriptionId}>Видео, его файлы, комментарии и лайки будут удалены без возможности восстановления.
        Оно также исчезнет из всех плейлистов.</p>
      {error && <p role="alert">{error}</p>}
      <div className="delete-video-actions">
        <button className="secondary" autoFocus disabled={busy} onClick={cancel}>Отмена</button>
        <button className="danger-button" disabled={busy} onClick={() => void remove()}>
          {busy ? "Удаление…" : "Удалить навсегда"}
        </button>
      </div>
    </dialog>
  );
}
