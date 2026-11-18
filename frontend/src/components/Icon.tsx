import type { CSSProperties } from "react";
const paths = {
  menu: "M4 6h16M4 12h16M4 18h16",
  search: "m21 21-5-5M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15",
  home: "m3 10 9-7 9 7v10h-6v-7H9v7H3Z",
  subscriptions: "M5 3h14M3 7h18M3 11h18v10H3Zm7 3 5 2-5 2Z",
  playlist: "M3 5h14M3 10h14M3 15h8m5-1 6 4-6 4Z",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  studio: "M3 4h18v16H3Zm0 5h18M8 9v11m5-8 5 3-5 3Z",
  user: "M20 21v-2a8 8 0 0 0-16 0v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5",
  moon: "M21 13a9 9 0 0 1-10-10 9 9 0 1 0 10 10Z",
  monitor: "M3 3h18v14H3Zm5 18h8m-4-4v4",
  close: "m6 6 12 12M6 18 18 6",
  plus: "M12 4v16M4 12h16",
  play: "m8 4 13 8-13 8Z",
  pause: "M7 4v16M17 4v16",
  volume: "M3 9v6h4l5 5V4L7 9Zm13-2a7 7 0 0 1 0 10m3-13a11 11 0 0 1 0 16",
  mute: "M3 9v6h4l5 5V4L7 9Zm13 0 6 6m-6 0 6-6",
  fullscreen: "M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6",
  exitFullscreen: "M3 9h6V3m6 0v6h6M3 15h6v6m6 0v-6h6",
  theater: "M2 5h20v14H2Zm0 3h20M2 16h20",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 2h6l1 4 4 1 2 5-3 3-1 5-6 2-3-3-5-1-2-6 3-3 1-5Z",
  like: "M7 10h-4v11h4Zm0 0 5-8c3 0 3 3 1 7h6c2 0 3 1 2 4l-2 8H7Z",
  flag: "M5 22V3c5-3 9 3 15 0v11c-6 3-10-3-15 0",
  up: "m5 15 7-7 7 7",
  down: "m5 9 7 7 7-7",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  shield: "m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Zm-4 10 3 3 5-6",
  logout: "M9 3H3v18h6m5-15 6 6-6 6M8 12h12",
  video: "M3 5h13v14H3Zm13 5 6-4v12l-6-4",
  check: "m4 12 5 5L20 6",
  arrowRight: "M4 12h16m-6-6 6 6-6 6",
} as const;
export type IconName = keyof typeof paths;
export function Icon({ name, size = 24 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={paths[name]} />
    </svg>
  );
}
// Same username, same identity color everywhere. No external profile-image service.
export function Avatar({ name, size = 36 }: { name: string; size?: number }) {
  const hash = Array.from(name.toLowerCase()).reduce(
    (n, c) => (n * 31 + c.codePointAt(0)!) >>> 0,
    0,
  );
  return (
    <span
      className="avatar"
      data-avatar={name.toLowerCase()}
      style={
        {
          "--avatar-color": [
            "#385a87",
            "#73518b",
            "#8c4d36",
            "#34715e",
            "#8a3e61",
            "#58652d",
          ][hash % 6],
          width: size,
          height: size,
          fontSize: Math.round(size * 0.43),
        } as CSSProperties
      }
      aria-hidden="true"
    >
      {Array.from(name)[0]?.toUpperCase() || "?"}
    </span>
  );
}
