// UI regressions against real API/media. Creates only synthetic users/videos/playlists.
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const base = "http://localhost:8080";
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      colorScheme: "light",
    }),
    page = await context.newPage(),
    errors = [],
    ranges = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("response", (r) => {
    if (r.url().endsWith("/media")) ranges.push(r.status());
  });
  try {
    const username = "polish_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registered = await context.request.post(base + "/api/auth/register", {
      headers: { Origin: base },
      data: {
        username,
        email: username + "@example.com",
        password: randomUUID(),
      },
    });
    assert.equal(registered.status(), 201);
    const session = await registered.json(),
      headers = { Origin: base, "X-CSRF-Token": session.csrf_token };
    await page.goto(base + "/");
    async function theme(value) {
      await page.getByRole("button", { name: "Аккаунт", exact: true }).click();
      await page.getByRole("radio", { name: value, exact: true }).check();
      await page.keyboard.press("Escape");
    }
    await theme("Тёмная");
    await page.waitForFunction(
      () => document.documentElement.dataset.theme === "dark",
    );
    await page.reload();
    await page.waitForFunction(
      () => document.documentElement.dataset.theme === "dark",
    );
    // Inspect DOM while JS bundles are delayed: bootstrap must set correct theme itself.
    const early = await context.newPage();
    await early.route("**/assets/*.js", (r) => r.abort());
    await early.goto(base + "/");
    assert.equal(
      await early.locator("html").getAttribute("data-theme"),
      "dark",
    );
    await early.close();
    await theme("Светлая");
    await page.waitForFunction(
      () => document.documentElement.dataset.theme === "light",
    );
    await theme("Как в системе");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForFunction(
      () => document.documentElement.dataset.theme === "dark",
    );
    await page.emulateMedia({ colorScheme: "light" });
    await page.waitForFunction(
      () => document.documentElement.dataset.theme === "light",
    );
    assert.deepEqual(await page.evaluate(() => Object.keys(localStorage)), [
      "video-service-theme",
    ]);
    assert.equal(await page.evaluate(() => sessionStorage.length), 0);
    console.log(
      "OK light/dark/system, reload, OS changes, pre-React theme, no auth material in storage",
    );
    const draft = await context.request.post(base + "/api/videos", {
      headers,
      data: {
        title: "Player controls " + randomUUID().slice(0, 8),
        description: "Synthetic UI verification",
        visibility: "public",
      },
    });
    assert.equal(draft.status(), 201);
    const id = (await draft.json()).id;
    assert.equal(
      (
        await context.request.put(`${base}/api/videos/${id}/source`, {
          headers: { ...headers, "Content-Type": "application/octet-stream" },
          data: fs.readFileSync(
            process.env.VIDEO_FIXTURE || "/fixtures/input.mp4",
          ),
        })
      ).status(),
      202,
    );
    // Wait for readiness using the owner API before opening the public watch page.
    for (let i = 0; i < 90; i++) {
      const state = await (
        await context.request.get(`${base}/api/me/videos/${id}`)
      ).json();
      if (state.status === "ready") break;
      assert.notEqual(state.status, "failed");
      await page.waitForTimeout(1000);
    }
    await page.goto(base + "/watch/" + id);
    await page.locator("video").waitFor();
    await page.waitForFunction(
      () => document.querySelector("video").readyState >= 1,
    );
    await page
      .getByRole("button", { name: "Воспроизвести видео", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector("video").currentTime > 0.4 &&
        !document.querySelector("video").paused,
    );
    await page.getByRole("button", { name: "Пауза", exact: true }).click();
    assert(await page.locator("video").evaluate((v) => v.paused));
    await page
      .getByRole("slider", { name: "Позиция воспроизведения", exact: true })
      .fill("8");
    await page.waitForFunction(
      () =>
        document.querySelector("video").currentTime >= 7.9 &&
        !document.querySelector("video").seeking,
    );
    await page
      .getByRole("slider", { name: "Громкость", exact: true })
      .fill("0.35");
    assert(
      Math.abs((await page.locator("video").evaluate((v) => v.volume)) - 0.35) <
        0.01,
    );
    await page
      .getByRole("button", { name: "Выключить звук", exact: true })
      .click();
    assert(await page.locator("video").evaluate((v) => v.muted));
    await page
      .getByRole("button", { name: "Включить звук", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Настройки воспроизведения", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "Скорость", exact: true })
      .selectOption("1.5");
    assert.equal(
      await page.locator("video").evaluate((v) => v.playbackRate),
      1.5,
    );
    await page
      .getByRole("button", { name: "Широкий экран", exact: true })
      .click();
    assert(
      await page
        .locator(".watch-page")
        .evaluate((el) => el.classList.contains("theater-mode")),
    );
    await page
      .getByRole("button", { name: "Полноэкранный режим", exact: true })
      .click();
    await page.waitForFunction(() => !!document.fullscreenElement);
    await page
      .getByRole("button", {
        name: "Выйти из полноэкранного режима",
        exact: true,
      })
      .click();
    await page.waitForFunction(() => !document.fullscreenElement);
    const player = page.getByRole("region", { name: /Видеоплеер:/ });
    await player.focus();
    await page.keyboard.press("Home");
    assert.equal(await page.locator("video").evaluate((v) => v.currentTime), 0);
    await page.keyboard.press("k");
    await page.waitForFunction(() => !document.querySelector("video").paused);
    await page.keyboard.press("k");
    await page.keyboard.press("l");
    assert((await page.locator("video").evaluate((v) => v.currentTime)) >= 10);
    await page.keyboard.press("m");
    assert(await page.locator("video").evaluate((v) => v.muted));
    await page.keyboard.press("t");
    assert.equal(
      await page
        .getByRole("button", { name: "Широкий экран", exact: true })
        .getAttribute("aria-pressed"),
      "false",
    );
    await page.keyboard.press("Home");
    await page.keyboard.press("k");
    await page.mouse.move(0, 0);
    await page.locator("h1").click();
    await page.waitForTimeout(2900);
    assert.equal(
      await page.locator(".video-player").getAttribute("class"),
      "video-player",
    );
    await player.focus();
    await page.keyboard.press("k"); // Reveal by keyboard and pause.
    await page
      .getByRole("slider", { name: "Позиция воспроизведения", exact: true })
      .focus();
    assert(
      await page
        .getByRole("slider", { name: "Позиция воспроизведения", exact: true })
        .evaluate((el) => el.matches(":focus-visible")),
    );
    await page.keyboard.press("ArrowLeft");
    assert(await page.locator(".player-controls").isVisible());
    await page.locator("video").evaluate((v) => {
      v.currentTime = v.duration - 0.3;
      v.playbackRate = 2;
      return v.play();
    });
    await page.waitForFunction(() => document.querySelector("video").ended);
    assert(
      await page
        .getByRole("button", {
          name: "Продолжить воспроизведение",
          exact: true,
        })
        .isVisible(),
    );
    assert(ranges.includes(206), "Real browser HTTP 206");
    console.log(
      "OK real playback/currentTime, seek/206, pause, volume/mute, speed, fullscreen/theater, keyboard, auto-hide, ended",
    );
    await page.getByLabel("Ваш комментарий").fill("<b>Plain text</b>");
    await page
      .getByRole("button", { name: "Отправить комментарий", exact: true })
      .click();
    await page.getByText("Комментарий добавлен", { exact: true }).waitFor();
    assert.equal(
      await page.locator(".comment-body").innerText(),
      "<b>Plain text</b>",
    );
    assert.equal(await page.locator(".comment-body b").count(), 0);
    const avatar = await page
      .locator(".comment [data-avatar]")
      .evaluate((el) => ({
        name: el.dataset.avatar,
        color: getComputedStyle(el).backgroundColor,
      }));
    assert.equal(avatar.name, username);
    assert.equal(
      await page
        .locator(".account-trigger [data-avatar]")
        .evaluate((el) => getComputedStyle(el).backgroundColor),
      avatar.color,
    );
    const playlist = await context.request.post(base + "/api/playlists", {
      headers,
      data: { title: "Polish playlist", description: "Own test playlist" },
    });
    assert.equal(playlist.status(), 201);
    const pid = (await playlist.json()).id;
    assert.equal(
      (
        await context.request.put(`${base}/api/playlists/${pid}/items/${id}`, {
          headers,
        })
      ).status(),
      204,
    );
    await page.goto(base + "/playlists/" + pid);
    await page.locator(".playlist-video-link img").waitFor();
    await page.waitForFunction(() =>
      Array.from(document.images).every((i) => i.complete),
    );
    assert(
      await page
        .locator(".playlist-video-link img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    );
    assert(
      await page
        .locator(".playlist-summary img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    );
    assert.equal(
      await page.locator(".playlist-video-link .duration").innerText(),
      "0:12",
    );
    await page.goto(base + "/studio/playlists/" + pid);
    await page.locator(".playlist-editor img").waitFor();
    await page.waitForFunction(() =>
      Array.from(document.images).every((i) => i.complete),
    );
    assert(
      await page
        .locator(".playlist-editor img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    );
    await page.goto(base + "/channels/" + username);
    await page.getByRole("tab", { name: "Плейлисты", exact: true }).click();
    await page.locator(".playlist-card img").waitFor();
    await page.waitForFunction(() =>
      Array.from(document.images).every((i) => i.complete),
    );
    assert(
      await page
        .locator(".playlist-card img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    );
    console.log(
      "OK escaped comments, stable avatars and real playlist covers/row/editor/channel thumbnails",
    );
    for (const width of [390, 768, 1440, 1920]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
    }
    await page.setViewportSize({ width: 390, height: 1000 });
    await page
      .getByRole("button", { name: "Меню навигации", exact: true })
      .click();
    await page.getByRole("link", { name: "Главная", exact: true }).waitFor();
    await page.keyboard.press("Shift+Tab");
    assert.equal(
      await page.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      "Закрыть меню",
    );
    await page.getByRole("link", { name: "Загрузить", exact: true }).focus();
    await page.keyboard.press("Tab");
    assert.equal(
      await page.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      "Закрыть меню",
    );
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () =>
        document.activeElement?.getAttribute("aria-label") === "Меню навигации",
    );
    console.log(
      "OK drawer focus trap/Escape/return focus and 390/768/1440/1920 widths",
    );
    assert.deepEqual(errors, []);
    console.log("OK no unexpected console errors/pageerror");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.stack);
  process.exitCode = 1;
});
