// Safe on the main stand: creates synthetic media only, deletes it via its owner API.
// Requires VIDEO_FIXTURE (>12 seconds), optionally SHORT_VIDEO_FIXTURE (<10 seconds).
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const base = process.env.BASE_URL || "http://localhost:8080";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  const owner = await browser.newContext();
  const created = [], responses = [], errors = [];
  let headers;
  try {
    const username = "viewfix_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registration = await owner.request.post(base + "/api/auth/register", {
      headers: { Origin: base }, data: { username, email: username + "@example.com", password: randomUUID() },
    });
    assert.equal(registration.status(), 201);
    headers = { Origin: base, "X-CSRF-Token": (await registration.json()).csrf_token };
    async function upload(file) {
      const draft = await owner.request.post(base + "/api/videos", {
        headers, data: { title: "Synthetic view recovery " + username, visibility: "public" },
      });
      assert.equal(draft.status(), 201);
      const id = (await draft.json()).id;
      created.push(id);
      assert.equal((await owner.request.put(`${base}/api/videos/${id}/source`, {
        headers: { ...headers, "Content-Type": "application/octet-stream" }, data: fs.readFileSync(file),
      })).status(), 202);
      for (let i = 0; i < 180; i++) {
        const video = await (await owner.request.get(`${base}/api/me/videos/${id}`)).json();
        assert.notEqual(video.status, "failed", video.error_code);
        if (video.status === "ready") return { id, duration: video.duration_ms };
        await sleep(500);
      }
      throw Error("Synthetic processing timeout");
    }
    const { id, duration } = await upload(process.env.VIDEO_FIXTURE);
    assert(duration > 12000);
    const count = async videoId => (await (await owner.request.get(`${base}/api/videos/${videoId}`)).json()).views_count;
    async function open(context, label, videoId = id) {
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(error.message));
      page.on("response", async response => {
        if (!response.url().includes(`/videos/${videoId}/views`)) return;
        const body = await response.json().catch(() => null);
        const value = { label, status: response.status(), kind: response.url().endsWith("/views") ? "start" : "progress" };
        if (response.status() === 200) for (const key of ["threshold_ms", "played_ms", "completed", "counted", "views_count"]) value[key] = body[key];
        responses.push(value);
        console.log(JSON.stringify({ http: value }));
      });
      await page.goto(`${base}/watch/${videoId}`);
      await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
      return page;
    }
    const play = page => page.getByRole("button", { name: "Воспроизвести видео", exact: true }).click();
    async function evidence(page, stage, expected, videoId = id) {
      await page.locator("video").evaluate(el => el.pause());
      const api = await count(videoId), ui = await page.locator(".watch-views").textContent();
      assert.equal(api, expected);
      assert.equal(ui, expected === 1 ? "1 просмотр" : expected < 5 && expected > 1 ? `${expected} просмотра` : `${expected} просмотров`);
      console.log(JSON.stringify({ evidence: { stage, video_id: videoId, api, ui } }));
      // Allows an external read-only PostgreSQL observer to sample this exact milestone.
      if (process.env.EVIDENCE_DELAY_MS) await sleep(Number(process.env.EVIDENCE_DELAY_MS));
    }
    const page = await open(owner, "auth-recovery");
    await evidence(page, "before", 0);
    let starts = 0, dropped = false;
    await page.route(`**/api/videos/${id}/views`, route => {
      starts++;
      return starts === 1 ? route.fulfill({ status: 503, json: { detail: "Synthetic temporary failure" } }) : route.continue();
    });
    await page.route(`**/api/videos/${id}/views/*`, async route => {
      if (!dropped) {
        dropped = true;
        // Server commits, but the client loses the response: recovery must deduplicate.
        await route.fetch();
        await route.abort("failed");
      } else await route.continue();
    });
    await play(page);
    await page.waitForFunction(() => document.querySelector("video").currentTime > 1);
    assert.equal(await page.locator(".player-error").count(), 0);
    await page.waitForFunction(() => document.querySelector(".watch-views")?.textContent === "1 просмотр", null, { timeout: 18000 });
    assert(dropped && starts >= 2 && starts <= 3);
    await evidence(page, "auth-after-temporary-failures", 1);
    await page.unrouteAll({ behavior: "wait" });
    await page.reload();
    await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
    await play(page);
    await page.waitForFunction(() => document.querySelector("video").currentTime > 10.5);
    await evidence(page, "auth-reload-replay", 1);
    const loading = await owner.newPage();
    let releaseAuth;
    const authGate = new Promise(resolve => { releaseAuth = resolve; });
    await loading.route("**/api/auth/session", async route => { await authGate; await route.continue(); });
    let earlyStarts = 0;
    loading.on("request", request => { if (request.url().endsWith(`/${id}/views`)) earlyStarts++; });
    await loading.goto(`${base}/watch/${id}`);
    await loading.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
    await play(loading);
    await loading.waitForFunction(() => document.querySelector("video").currentTime > 0.6);
    const beforeAuth = earlyStarts;
    releaseAuth();
    assert.equal(beforeAuth, 0, "Do not POST with a cookie before its CSRF token loads");
    await loading.getByRole("button", { name: "Аккаунт", exact: true }).waitFor();
    await loading.close();
    console.log(JSON.stringify({ check: "auth-loading", starts_before_csrf: beforeAuth }));
    const guest = await browser.newContext(), gp = await open(guest, "guest");
    for (let i = 0; i < 2; i++) assert.equal((await guest.request.get(`${base}/api/videos/${id}/media`, {
      headers: { Range: "bytes=0-1023" },
    })).status(), 206);
    assert.equal(await count(id), 1);
    await play(gp);
    await gp.waitForFunction(() => document.querySelector("video").currentTime > 0.6);
    await gp.locator("video").evaluate(el => el.pause());
    const pausedAt = await gp.locator("video").evaluate(el => el.currentTime);
    await gp.waitForTimeout(600);
    assert.equal(await gp.locator("video").evaluate(el => el.currentTime), pausedAt);
    await gp.locator("video").evaluate(el => { el.currentTime = el.duration - 0.5; });
    await gp.waitForFunction(() => !document.querySelector("video").seeking);
    await gp.locator("video").evaluate(el => el.play());
    await gp.waitForFunction(() => document.querySelector("video").ended);
    assert.equal(await count(id), 1, "Pause, Range and seek cannot qualify a view");
    await gp.locator("video").evaluate(el => { el.currentTime = 0; });
    await gp.waitForFunction(() => !document.querySelector("video").seeking);
    await gp.locator("video").evaluate(el => el.play());
    await gp.waitForFunction(() => document.querySelector(".watch-views")?.textContent === "2 просмотра", null, { timeout: 16000 });
    await evidence(gp, "new-guest", 2);
    const cookie = (await guest.cookies()).find(c => c.name === "video_viewer");
    assert(cookie?.httpOnly && cookie.sameSite === "Lax");
    assert.equal(await gp.evaluate(() => document.cookie.includes("video_viewer=")), false);
    await gp.reload();
    await gp.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
    await play(gp);
    await gp.waitForFunction(() => document.querySelector("video").currentTime > 10.5);
    await evidence(gp, "guest-reload-replay", 2);
    // A permanent failure must not produce unlimited requests, even on continuous playback.
    const outage = await browser.newContext(), op = await open(outage, "outage");
    let attempts = 0;
    await op.route(`**/api/videos/${id}/views`, route => { attempts++; return route.fulfill({ status: 500, json: {} }); });
    await play(op);
    await op.waitForFunction(() => document.querySelector("video").ended, null, { timeout: 20000 });
    assert(attempts >= 2 && attempts <= 4, `Bounded recovery, received ${attempts} attempts`);
    assert.equal(await count(id), 2);
    assert.equal(await op.locator(".player-error").count(), 0);
    console.log(JSON.stringify({ check: "persistent-outage", attempts, views_count: 2 }));
    await outage.close();
    // Replaced sessions must not fight across tabs by repeatedly starting new sessions.
    const competitor = await browser.newContext(), cp = await open(competitor, "concurrent-old");
    await Promise.all([cp.waitForResponse(r => r.url().endsWith(`/${id}/views`)), play(cp)]);
    const newer = await competitor.newPage();
    await newer.goto(`${base}/watch/${id}`);
    await newer.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
    const replaced = cp.waitForResponse(r => r.url().includes(`/${id}/views/`) && r.status() === 404);
    await Promise.all([newer.waitForResponse(r => r.url().endsWith(`/${id}/views`)), play(newer)]);
    await newer.locator("video").evaluate(el => el.pause());
    await cp.bringToFront();
    assert.equal((await replaced).status(), 404);
    await cp.waitForTimeout(1000);
    assert.equal(responses.filter(r => r.label === "concurrent-old" && r.kind === "start").length, 1);
    await competitor.close();
    for (const status of [400, 403, 404, 409, 422]) {
      const denied = await browser.newContext(), dp = await open(denied, `terminal-${status}`);
      let requests = 0;
      await dp.route(`**/api/videos/${id}/views`, route => { requests++; return route.fulfill({ status, json: {} }); });
      await play(dp);
      await dp.waitForFunction(() => document.querySelector("video").currentTime > 2.5);
      assert.equal(requests, 1, `HTTP ${status} must not cause retry loops`);
      assert.equal(await count(id), 2);
      await denied.close();
    }
    if (process.env.SHORT_VIDEO_FIXTURE) {
      const short = await upload(process.env.SHORT_VIDEO_FIXTURE);
      assert(short.duration < 10000);
      const sp = await open(owner, "short", short.id);
      await play(sp);
      await sp.waitForFunction(() => document.querySelector("video").ended);
      await evidence(sp, "short-real-playback", 1, short.id);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: "PASS", base, synthetic_video: id, browser_errors: errors.length }));
  } finally {
    for (const id of created) {
      const response = await owner.request.delete(`${base}/api/videos/${id}`, { headers });
      console.log(JSON.stringify({ cleanup: { video_id: id, status: response.status() } }));
      assert.equal(response.status(), 204);
    }
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
