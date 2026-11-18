// Own deterministic graphics and API data for visual/layout QA only.
// Never installed into the app, written to the database or used by production.
const fs = require("node:fs");
const titles = [
  "Свет и форма: маленькая история большого города",
  "Как рождается движение — студия в деталях",
  "Тихий маршрут. Утро за городом",
  "Архитектура простых вещей",
  "Цвет, ритм и немного вдохновения",
  "Собираем идеи в одну историю",
  "Пространство для эксперимента",
  "Новый взгляд на знакомые места",
  "Один день, одна камера",
  "Детали, которые мы не замечаем",
  "Звук и пространство",
  "Первые шаги в анимации",
];
const colors = [
  ["#143f48", "#83bfad"],
  ["#322653", "#b8a4df"],
  ["#925336", "#efb977"],
  ["#293449", "#8babc7"],
  ["#7e394e", "#e5a7b4"],
  ["#315647", "#98bbae"],
];
function svg(i) {
  const [a, b] = colors[(i - 1) % colors.length];
  return `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><defs><linearGradient id="g"><stop stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient></defs><rect width="640" height="360" fill="url(#g)"/><circle cx="510" cy="90" r="135" fill="${b}" opacity=".45"/><path d="M0 310 170 100 350 360H0M200 360 430 150 640 360" fill="${a}" opacity=".5"/><text x="38" y="65" fill="white" font-family="sans-serif" font-size="15" letter-spacing="5">VIDEO SERVICE / ORIGINAL</text><text x="38" y="275" fill="white" font-family="sans-serif" font-size="50" font-weight="bold">${["СВЕТ И ФОРМА", "В ДВИЖЕНИИ", "ТИХИЙ МАРШРУТ", "ПРОСТЫЕ ВЕЩИ", "ЦВЕТ И РИТМ", "НОВАЯ ИСТОРИЯ"][(i - 1) % 6]}</text><text x="40" y="316" fill="white" font-family="sans-serif" font-size="16">ЭТЮД ${String(i).padStart(2, "0")} / 2026</text></svg>`;
}
async function fixture(page, { long = false, guest = false } = {}) {
  const username = long ? "u".repeat(32) : "studio_light";
  const cards = titles.map((title, i) => ({
    id: String(i + 1),
    title: long ? "Д".repeat(200) : title,
    duration_ms: (i + 1) * 63000,
    published_at: "2026-11-16T12:00:00Z",
    channel: {
      id: "c" + i,
      title: long
        ? "К".repeat(200)
        : ["Студия света", "В движении", "Тихие истории"][i % 3],
      username:
        i % 3 === 0 ? username : ["studio_light", "motion", "stories"][i % 3],
    },
    thumbnail_url: `/ui-thumb/${i + 1}`,
    watch_url: `/watch/${i + 1}`,
  }));
  const description = long
    ? "Описание ".repeat(550)
    : "Коллекция наблюдений за светом, движением и пространством. Смотрите в своём ритме.";
  const playlist = {
    id: "p1",
    title: long ? "П".repeat(200) : "Маленькие истории",
    description,
    username,
    owned_by_me: !guest,
    items: cards.slice(0, 5),
    next_cursor: null,
  };
  const channel = {
    id: "c0",
    username,
    title: long ? "К".repeat(200) : "Студия света",
    description: long
      ? description
      : "Истории о свете, движении и простых вещах.",
    subscribers_count: 12,
    subscribed_by_me: false,
    owned_by_me: !guest,
  };
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url()),
      p = url.pathname;
    let data;
    if (p === "/api/auth/session")
      data = guest
        ? null
        : {
            user: { id: "u1", username, role: "admin" },
            csrf_token: "fixture",
          };
    else if (p === "/api/videos") data = { items: cards, next_cursor: null };
    else if (p === "/api/me/videos")
      data = {
        items: cards.map((card, i) => ({
          ...card,
          author: username,
          description: "Собственный синтетический клип",
          status: i < 3 ? "ready" : ["processing", "queued", "failed"][i % 3],
          visibility: i % 2 ? "private" : "public",
          moderation_state: "visible",
          source_accepted: true,
          error_code: i === 5 ? "invalid_media" : null,
        })),
        next_cursor: null,
      };
    else if (p.endsWith("/comments"))
      data = {
        items: [
          {
            id: "1",
            body: long
              ? "Текст ".repeat(330)
              : "Очень нравится, как свет меняет привычные вещи. Спасибо за эту историю!",
            created_at: "2026-11-16T13:20:00Z",
            author: { id: "u2", username: "anna" },
          },
          {
            id: "2",
            body: "Интересная работа с цветом и композицией. Хочется пересмотреть ещё раз.",
            created_at: "2026-11-16T14:00:00Z",
            author: { id: "u3", username: "alex" },
          },
          {
            id: "3",
            body: "<b>Пользовательский HTML остаётся текстом</b>",
            created_at: "2026-11-16T15:00:00Z",
            author: { id: "u2", username: "anna" },
          },
        ],
        next_cursor: null,
      };
    else if (/^\/api\/videos\/\d+$/.test(p))
      data = {
        ...cards[Number(p.split("/").pop()) - 1],
        description: long
          ? description
          : "Небольшая прогулка по городу и наблюдение за тем, как свет меняет знакомые места.\nСнято для Video-service. Своя история, свой взгляд.",
        author: username,
        likes_count: 7,
        liked_by_me: false,
      };
    else if (p.endsWith("/media"))
      return route.fulfill({
        contentType: "video/mp4",
        body: fs.readFileSync(
          process.env.VIDEO_FIXTURE || "/fixtures/input.mp4",
        ),
      });
    else if (p.endsWith("/thumbnail"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: svg(Number(p.split("/").at(-2)) || 1),
      });
    else if (
      p.startsWith("/api/channels/") &&
      !p.endsWith("/videos") &&
      !p.endsWith("/playlists")
    )
      data = channel;
    else if (p === "/api/me/subscriptions")
      data = { items: [channel], next_cursor: null };
    else if (p.endsWith("/playlists") && !p.includes("/me/"))
      data = { items: [playlist], next_cursor: null };
    else if (p.includes("/playlists/"))
      data = {
        ...playlist,
        items: p.includes("/me/")
          ? playlist.items.map((v) => ({
              ...v,
              status: "ready",
              moderation_state: "visible",
            }))
          : playlist.items,
      };
    else if (p === "/api/admin/users")
      data = [{ id: "u2", username: "u".repeat(32), role: "user" }];
    else if (p.startsWith("/api/moderation/"))
      data = { items: [], next_cursor: null };
    else if (p.endsWith("/access")) data = [];
    else data = { items: cards, next_cursor: null };
    return route.fulfill({ json: data });
  });
  await page.route("**/ui-thumb/*", (r) =>
    r.fulfill({
      contentType: "image/svg+xml",
      body: svg(Number(r.request().url().split("/").pop())),
    }),
  );
  return { username, cards };
}
module.exports = { fixture };
