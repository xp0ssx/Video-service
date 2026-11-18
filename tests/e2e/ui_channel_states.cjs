// Focused UI regressions with controlled responses; real API coverage lives in channels_playlists/social.
const { chromium } = require("playwright-core");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { fixture } = require("./ui_fixture.cjs");
const base = "http://localhost:8080";
const output = process.env.ARTIFACT_DIR || ".local/channel-polish";
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    for (const theme of ["light", "dark"]) {
      for (const width of [390, 1440]) {
        const context = await browser.newContext({ viewport: { width, height: 1000 } });
        await context.addInitScript((t) => localStorage.setItem("video-service-theme", t), theme);
        const page = await context.newPage(), errors = [];
        page.on("pageerror", (e) => errors.push(e.message));
        page.on("console", (m) => {
          if (m.type() === "error" && !(/503|404/.test(m.text()) && /\/api\/(videos\?|channels\/)/.test(m.location().url))) errors.push(m.text());
        });
        const { username, cards } = await fixture(page);
        const channelUrl = `/channels/${username}`;
        const button = (name) => page.getByRole("button", { name, exact: true });
        const tab = (name) => page.getByRole("tab", { name, exact: true });
        const panel = (name) => page.getByRole("tabpanel", { name, exact: true });
        async function layout() {
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${theme}/${width}: overflow`);
        }
        async function selected(name) {
          await page.waitForFunction((label) => [...document.querySelectorAll('[role="tab"]')].some((el) => el.textContent === label && el.getAttribute("aria-selected") === "true"), name);
          await panel(name).waitFor();
          assert.equal(await page.getByRole("tabpanel").count(), 1);
          assert(await panel(name).isVisible());
          assert.equal(await tab(name).getAttribute("tabindex"), "0");
          const id = await tab(name).getAttribute("aria-controls");
          assert.equal(await panel(name).getAttribute("id"), id);
          assert.equal(await panel(name).getAttribute("aria-labelledby"), await tab(name).getAttribute("id"));
        }
        async function capture(name) {
          await page.locator("img:visible").evaluateAll((images) => images.forEach((img) => img.loading = "eager"));
          await page.waitForFunction(() => [...document.images].filter((img) => img.getClientRects().length).every((img) => img.complete && img.naturalWidth));
          await page.screenshot({ path: path.join(output, name) });
        }
        await page.route(`**/api/channels/${username}/videos?*`, (route) => route.fulfill({ json: {
          items: new URL(route.request().url()).searchParams.has("cursor") ? cards.slice(2, 4) : cards.slice(0, 2),
          next_cursor: new URL(route.request().url()).searchParams.has("cursor") ? null : "next",
        } }));
        await page.goto(base + channelUrl);
        await selected("Видео");
        await panel("Видео").locator(".video-card").first().waitFor();
        await page.evaluate(() => window.__tabDocument = "preserved");
        if (theme === "light" && width === 1440) await capture("channel-videos-1440.png");
        await panel("Видео").getByRole("button", { name: "Показать ещё", exact: true }).click();
        await page.waitForFunction(() => document.querySelectorAll('#channel-videos .video-card').length === 4);
        await tab("Видео").focus();
        await page.keyboard.press("ArrowRight");
        await selected("Плейлисты");
        assert(await tab("Плейлисты").evaluate((el) => document.activeElement === el));
        assert.equal(await page.evaluate(() => window.__tabDocument), "preserved");
        assert(page.url().endsWith("#channel-playlists"));
        await panel("Плейлисты").locator(".playlist-card").waitFor();
        await layout();
        if (theme === "light" && width === 1440) await capture("channel-playlists-1440.png");
        await page.keyboard.press("Home");
        await selected("Видео");
        assert.equal(await panel("Видео").locator(".video-card").count(), 4, "Pagination preserved across tabs");
        await page.keyboard.press("End");
        await selected("Плейлисты");
        await page.keyboard.press("ArrowLeft");
        await selected("Видео");
        await tab("Плейлисты").click();
        await page.reload();
        await selected("Плейлисты");
        await tab("Видео").focus();
        await page.keyboard.press("Space");
        await selected("Видео");
        await layout();

        let entries = cards.slice(0, 3).map((v) => ({ ...v, status: "ready", moderation_state: "visible" }));
        const orders = [];
        await page.route("**/api/me/playlists/p1**", (route) => route.fulfill({ json: {
          id: "p1", title: "Маленькие истории", description: "Коллекция наблюдений", username,
          owned_by_me: true, items: entries, next_cursor: null,
        } }));
        await page.route("**/api/playlists/p1/order", (route) => {
          assert.equal(route.request().method(), "PUT");
          const ids = route.request().postDataJSON().video_ids;
          orders.push(ids);
          assert.equal(new Set(ids).size, entries.length);
          entries = ids.map((id) => entries.find((v) => v.id === id));
          return route.fulfill({ status: 204 });
        });
        await page.goto(base + "/studio/playlists/p1");
        const actions = page.locator(".editor-item-actions");
        await actions.first().waitFor();
        const move = button("Ниже: " + cards[0].title);
        await page.waitForFunction(() => !document.querySelector('.playlist-page fieldset').disabled);
        for (const control of await actions.locator("button").all()) {
          const box = await control.boundingBox();
          assert(box.width >= 44 && box.height >= 44, "Editor touch target >=44px");
          assert(await control.getAttribute("aria-label"));
        }
        const gaps = await actions.first().locator("button").evaluateAll((buttons) => {
          const boxes = buttons.map((b) => b.getBoundingClientRect());
          return [boxes[1].left - boxes[0].right, boxes[2].left - boxes[1].right];
        });
        assert(gaps[0] >= 8 && gaps[1] >= gaps[0] + 12, "Deletion separated from ordering");
        const save = button("Сохранить плейлист"), cancel = button("Отменить изменения");
        assert.equal(await cancel.getAttribute("class"), "secondary");
        assert.notEqual(await save.evaluate((el) => getComputedStyle(el).backgroundColor), await cancel.evaluate((el) => getComputedStyle(el).backgroundColor));
        if (width === 1440) assert.equal((await save.boundingBox()).y, (await cancel.boundingBox()).y);
        await move.click();
        await page.waitForFunction((title) => document.querySelector('.editor-video-title')?.textContent.startsWith(title), cards[1].title);
        assert.deepEqual(orders, [["2", "1", "3"]], "One atomic complete order request");
        await page.reload();
        await actions.first().waitFor();
        assert.deepEqual(await page.locator('.editor-video-title').allTextContents(), entries.map((v) => `${v.title} (Готово)`));
        await layout();
        if (theme === "dark" && width === 390) await capture("playlist-editor-390.png");

        const recUrl = "**/api/videos?limit=16";
        const channelApi = `**/api/channels/${username}`;
        let recMode = "hold", channelMode = "hold", recArrived = deferred(), channelArrived = deferred();
        let recHeld = [], channelHeld = [];
        let subscribed = false;
        const channelData = () => ({ title: "Студия света", subscribers_count: subscribed ? 13 : 12, subscribed_by_me: subscribed, owned_by_me: false });
        await page.route(recUrl, (route) => {
          if (recMode === "hold") { recHeld.push(route); recArrived.resolve(); return; }
          if (recMode === "error") return route.fulfill({ status: 503, json: { message: "Hidden upstream details" } });
          return route.fulfill({ json: { items: recMode === "empty" ? [] : cards.slice(0, 4), next_cursor: null } });
        });
        await page.route(channelApi, (route) => {
          if (channelMode === "hold") { channelHeld.push(route); channelArrived.resolve(); return; }
          if (channelMode === "error") return route.fulfill({ status: 404, json: { message: "Unavailable channel details" } });
          return route.fulfill({ json: channelData() });
        });
        await page.route(`**/api/channels/${username}/subscription`, (route) => {
          subscribed = route.request().method() === "PUT";
          return route.fulfill({ json: channelData() });
        });
        const recommendations = page.getByRole("complementary", { name: "Рекомендации", exact: true });
        const watchChannel = page.locator(".watch-channel");
        const empty = recommendations.getByText("Других публичных видео пока нет.", { exact: true });
        await page.goto(base + "/watch/1");
        await recArrived.promise;
        await channelArrived.promise;
        await recommendations.getByRole("status").waitFor();
        await watchChannel.getByRole("status").waitFor();
        assert.equal(await empty.count(), 0);
        assert.equal(await watchChannel.getByRole("button", { name: /^(Подписаться|Отписаться)$/ }).count(), 0);
        recMode = "empty";
        for (const request of recHeld) await request.fulfill({ json: { items: [], next_cursor: null } });
        await empty.waitFor();
        assert.equal(await recommendations.getByRole("status").count(), 0);
        channelMode = "error";
        for (const request of channelHeld) await request.fulfill({ status: 404, json: { message: "Unavailable channel details" } });
        await watchChannel.getByRole("alert").waitFor();
        assert.equal(await watchChannel.getByText("Unavailable channel details").count(), 0);
        assert.equal(await watchChannel.getByRole("button", { name: /^(Подписаться|Отписаться)$/ }).count(), 0);
        await layout();
        channelMode = "hold"; channelHeld = []; channelArrived = deferred();
        await button("Повторить загрузку канала").click();
        await channelArrived.promise;
        await watchChannel.getByRole("status").waitFor();
        subscribed = true;
        channelMode = "success";
        for (const request of channelHeld) await request.fulfill({ json: channelData() });
        await button("Отписаться").waitFor();
        await button("Отписаться").click();
        await button("Подписаться").waitFor();
        assert.equal(subscribed, false);
        assert.equal(await watchChannel.getByRole("alert").count(), 0);
        recMode = "error"; channelMode = "success";
        await page.reload();
        await recommendations.getByRole("alert").waitFor();
        assert.equal(await empty.count(), 0);
        assert.equal(await recommendations.getByRole("status").count(), 0);
        recMode = "hold"; recHeld = []; recArrived = deferred();
        await recommendations.getByRole("button", { name: "Повторить", exact: true }).click();
        await recArrived.promise;
        await recommendations.getByRole("status").waitFor();
        assert.equal(await empty.count(), 0);
        assert.equal(await recommendations.getByRole("alert").count(), 0);
        recMode = "success";
        for (const request of recHeld) await request.fulfill({ json: { items: cards.slice(0, 4), next_cursor: null } });
        await recommendations.locator(".video-card").first().waitFor();
        assert.equal(await recommendations.locator(".video-card").count(), 3, "Current video excluded");
        await layout();
        if (theme === "dark" && width === 1440) await capture("watch-recommendations-1440.png");

        // A delayed channel GET from the old watch page cannot undo a newer subscription.
        recMode = "success"; channelMode = "hold"; channelHeld = []; channelArrived = deferred();
        await page.reload();
        await channelArrived.promise;
        channelMode = "success";
        await page.evaluate(() => { history.pushState(null, "", "/watch/2"); window.dispatchEvent(new PopStateEvent("popstate")); });
        await button("Подписаться").waitFor();
        await button("Подписаться").click();
        await button("Отписаться").waitFor();
        for (const request of channelHeld) await request.fulfill({ json: { ...channelData(), subscribed_by_me: false, subscribers_count: 12 } });
        await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
        assert(await button("Отписаться").isVisible());
        await layout();
        assert.deepEqual(errors, []);
        console.log(`OK ${theme} ${width}: tabs/hash/reload/keyboard/pagination, editor targets/order, recommendations loading/empty/error/retry, channel failure/retry/stale response`);
        await context.close();
      }
    }
    console.log("OK four screenshots saved; no unexpected browser errors");
  } finally { await browser.close(); }
})().catch((e) => { console.error(e.stack); process.exitCode = 1; });
