// Real HTMLMediaElement + API/worker/PostgreSQL on the isolated :8081 stack.
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.env.BASE_URL || "http://localhost:8081";
(async () => {
  const source = fs.readFileSync(path.join(__dirname, "../../frontend/src/components/views.ts"), "utf8");
  const { formatViews } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  for (const [count, text] of [[0,"0 просмотров"], [1,"1 просмотр"], [2,"2 просмотра"], [5,"5 просмотров"],
    [11,"11 просмотров"], [21,"21 просмотр"], [22,"22 просмотра"], [1200,"1,2 тыс. просмотров"],
    [1500000,"1,5 млн просмотров"]]) assert.equal(formatViews(count), text);
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const username = "views_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registered = await context.request.post(base + "/api/auth/register", {
      headers: { Origin: base }, data: { username, email: username + "@example.com", password: randomUUID() },
    });
    assert.equal(registered.status(), 201);
    const session = await registered.json(), headers = { Origin: base, "X-CSRF-Token": session.csrf_token };
    const title = "Просмотры синтетического клипа " + username;
    const draft = await context.request.post(base + "/api/videos", { headers, data: { title, visibility: "public" } });
    assert.equal(draft.status(), 201);
    const id = (await draft.json()).id;
    assert.equal((await context.request.put(`${base}/api/videos/${id}/source`, {
      headers: { ...headers, "Content-Type": "application/octet-stream" },
      data: fs.readFileSync(process.env.VIDEO_FIXTURE || "/fixtures/views.mp4"),
    })).status(), 202);
    const page = await context.newPage(), errors = [], starts = [], reports = [], ranges = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (request.method() === "POST" && request.url().endsWith(`/${id}/views`)) starts.push(request);
    });
    page.on("response", response => {
      if (response.url().includes(`/${id}/views/`)) reports.push(response.status());
      if (response.url().endsWith(`/${id}/media`)) ranges.push(response.status());
    });
    const ready = async identifier => {
      const deadline = Date.now() + 90000;
      while (Date.now() < deadline) {
        const response = await context.request.get(`${base}/api/me/videos/${identifier}`);
        assert.equal(response.status(), 200);
        const value = await response.json();
        assert.notEqual(value.status, "failed", value.error_code);
        if (value.status === "ready") return;
        await page.waitForTimeout(250);
      }
      throw new Error("Synthetic video processing timed out");
    };
    await ready(id);
    const count = async () => (await (await context.request.get(`${base}/api/videos/${id}`)).json()).views_count;
    await page.goto(`${base}/watch/${id}`);
    const video = page.locator("video");
    await page.waitForFunction(() => document.querySelector("video")?.readyState >= 1).catch(async error => {
      console.log("WATCH DIAGNOSTIC", await page.locator("body").innerText(), errors);
      console.log("MEDIA DIAGNOSTIC", await video.evaluateAll(items => items.map(el => ({ ready: el.readyState, network: el.networkState, error: el.error?.message }))));
      if (process.env.ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.ARTIFACT_DIR, "views-failure.png") });
      throw error;
    });
    assert(Math.abs(await video.evaluate(el => el.duration) - 16) < 0.15);
    assert.equal(await count(), 0);
    assert.equal(starts.length, 0, "Opening and media metadata requests never start a view");
    for (let attempt = 0; attempt < 2; attempt++) {
      assert.equal((await context.request.get(`${base}/api/videos/${id}/media`, {
        headers: { Range: "bytes=0-1023" },
      })).status(), 206);
    }
    assert.equal(await count(), 0, "Repeated HTTP Range requests never count");
    const player = page.getByRole("region", { name: /Видеоплеер:/ });
    const button = name => page.getByRole("button", { name, exact: true });
    await button("Воспроизвести видео").click();
    await page.waitForFunction(() => document.querySelector("video").currentTime > 0.6);
    await video.evaluate(el => el.pause());
    const paused = await video.evaluate(el => el.currentTime);
    await page.waitForTimeout(350); // Verify an actual pause, not a synthetic time increment.
    assert.equal(await video.evaluate(el => el.currentTime), paused);
    assert.equal(await count(), 0);
    assert.equal(starts.length, 1);
    // A seek to near the end plus its short real tail cannot reach ten played seconds.
    await video.evaluate(el => { el.currentTime = 15; });
    await page.waitForFunction(() => !document.querySelector("video").seeking);
    await video.evaluate(el => el.play());
    await page.waitForFunction(() => document.querySelector("video").ended);
    assert.equal(await count(), 0, "Seek did not count skipped seconds");
    await video.evaluate(el => { el.currentTime = 0; });
    await page.waitForFunction(() => !document.querySelector("video").seeking);
    await video.evaluate(el => el.play());
    await page.waitForFunction(() => document.querySelector(".watch-views")?.textContent === "1 просмотр", null, { timeout: 16000 });
    await video.evaluate(el => el.pause());
    assert.equal(await count(), 1);
    assert.equal(starts.length, 1, "Renders, pause, resume and seek reuse a session");
    assert(reports.length <= 14 && reports.every(status => status === 200), "Bounded, successful progress reports");
    assert(ranges.includes(206));
    console.log("OK real play/currentTime, pause, seek rejection, ten-second qualification, one start, bounded requests");

    await page.reload();
    await page.waitForFunction(() => document.querySelector("video")?.readyState >= 1);
    assert.equal(await page.locator(".watch-views").textContent(), "1 просмотр");
    await button("Воспроизвести видео").click();
    await page.waitForFunction(() => document.querySelector("video").currentTime > 0.4);
    await video.evaluate(el => el.pause());
    assert.equal(await count(), 1, "Reload and replay remain deduplicated");
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        assert.equal(await page.locator(".watch-views").textContent(), "1 просмотр");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        if (process.env.ARTIFACT_DIR) await page.screenshot({ path: path.join(process.env.ARTIFACT_DIR, `views-${width}-${theme}.png`) });
      }
    }
    await player.focus();
    await page.keyboard.press("t");
    assert.equal(await button("Широкий экран").getAttribute("aria-pressed"), "true");
    await page.keyboard.press("m");
    assert(await video.evaluate(el => el.muted));
    await page.keyboard.press("Home");
    await page.waitForFunction(() => !document.querySelector("video").seeking);
    assert((await video.evaluate(el => el.currentTime)) < 0.1);
    await button("Настройки воспроизведения").click();
    await page.getByRole("combobox", { name: "Скорость", exact: true }).selectOption("1.5");
    assert.equal(await video.evaluate(el => el.playbackRate), 1.5);
    await page.getByRole("slider", { name: "Громкость", exact: true }).fill("0.6");
    assert.equal(await video.evaluate(el => el.volume), 0.6);
    await button("Полноэкранный режим").click();
    await page.waitForFunction(() => !!document.fullscreenElement);
    await button("Выйти из полноэкранного режима").click();
    await page.waitForFunction(() => !document.fullscreenElement);
    console.log("OK reload dedup, light/dark 390/1440, fullscreen/theater/speed/volume/keyboard, Russian formatting");

    for (const destination of ["/", `/results?q=${encodeURIComponent(title)}`, `/channels/${username}`, "/studio"]) {
      await page.goto(base + destination);
      const scope = destination === "/studio" ? page.locator(".studio-item").filter({ hasText: title })
        : page.locator(".video-card").filter({ hasText: title });
      await scope.getByText("1 просмотр", { exact: true }).waitFor();
    }
    const playlist = await context.request.post(base + "/api/playlists", { headers, data: { title: "Синтетические просмотры" } });
    const pid = (await playlist.json()).id;
    assert.equal((await context.request.put(`${base}/api/playlists/${pid}/items/${id}`, { headers })).status(), 204);
    await page.goto(`${base}/playlists/${pid}`);
    await page.locator(".playlist-video-row").getByText("1 просмотр", { exact: true }).waitFor();
    // Recommendations use the same real feed projection.
    const second = await context.request.post(base + "/api/videos", { headers, data: { title: "Recommendation source", visibility: "public" } });
    const secondId = (await second.json()).id;
    assert.equal((await context.request.put(`${base}/api/videos/${secondId}/source`, {
      headers: { ...headers, "Content-Type": "application/octet-stream" },
      data: fs.readFileSync(process.env.VIDEO_FIXTURE || "/fixtures/views.mp4"),
    })).status(), 202);
    await ready(secondId);
    await page.goto(`${base}/watch/${secondId}`);
    await page.locator(".watch-recommendations .video-card").filter({ hasText: title }).getByText("1 просмотр", { exact: true }).waitFor();
    // Anonymous cookie identity is separate from the authenticated account.
    const guest = await browser.newContext(), guestPage = await guest.newPage();
    await guestPage.goto(`${base}/watch/${id}`);
    await guestPage.waitForFunction(() => document.querySelector("video")?.readyState >= 1);
    await guestPage.getByRole("button", { name: "Воспроизвести видео", exact: true }).click();
    await guestPage.waitForFunction(() => document.querySelector(".watch-views")?.textContent === "2 просмотра", null, { timeout: 16000 });
    await guestPage.locator("video").evaluate(el => el.pause());
    assert.equal(await count(), 2);
    const cookie = (await guest.cookies()).find(value => value.name === "video_viewer");
    assert(cookie?.httpOnly && cookie.sameSite === "Lax");
    await guestPage.reload();
    await guestPage.locator(".watch-views").getByText("2 просмотра", { exact: true }).waitFor();
    await guest.close();
    const failing = await browser.newContext(), failingPage = await failing.newPage();
    await failingPage.route(`**/api/videos/${id}/views`, route => route.fulfill({ status: 503, json: { message: "Synthetic analytics failure" } }));
    await failingPage.goto(`${base}/watch/${id}`);
    await failingPage.getByRole("button", { name: "Воспроизвести видео", exact: true }).click();
    await failingPage.waitForFunction(() => document.querySelector("video").currentTime > 0.6);
    assert.equal(await failingPage.locator(".player-error").count(), 0);
    await failing.close();
    assert.deepEqual(errors, []);
    console.log("OK counts on home/search/channel/playlist/Studio/recommendations; real anonymous playback, persistent cookie, playback survives analytics failure");
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
