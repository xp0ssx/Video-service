// Synthetic resources only; run against the isolated deletion stack on :8081.
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const path = require("node:path");
const base = process.env.BASE_URL || "http://localhost:8081";
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const name = "delete_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registered = await context.request.post(base + "/api/auth/register", {
      headers: { Origin: base },
      data: { username: name, email: name + "@example.com", password: randomUUID() },
    });
    assert.equal(registered.status(), 201);
    const session = await registered.json();
    const headers = { Origin: base, "X-CSRF-Token": session.csrf_token };
    const page = await context.newPage();
    const errors = [], consoleErrors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => {
      // Deliberately injected HTTP/network failures are asserted below.
      if (message.type() === "error" && !/Failed to load resource|net::ERR_FAILED/.test(message.text()))
        consoleErrors.push(message.text());
    });
    const create = async (title, ready = false) => {
      const draft = await context.request.post(base + "/api/videos", {
        headers, data: { title, visibility: "public" },
      });
      assert.equal(draft.status(), 201);
      const id = (await draft.json()).id;
      if (ready) {
        assert.equal((await context.request.put(`${base}/api/videos/${id}/source`, {
          headers: { ...headers, "Content-Type": "application/octet-stream" },
          data: fs.readFileSync(process.env.VIDEO_FIXTURE || "/fixtures/input.mp4"),
        })).status(), 202);
        let processed = false;
        for (let attempt = 0; attempt < 90; attempt++) {
          const state = await (await context.request.get(`${base}/api/me/videos/${id}`)).json();
          assert.notEqual(state.status, "failed", state.error_code);
          if (state.status === "ready") { processed = true; break; }
          await page.waitForTimeout(1000);
        }
        assert(processed, "Synthetic video is ready");
      }
      return id;
    };
    const itemFor = title => page.locator(".studio-item").filter({
      has: page.getByRole("heading", { name: title, exact: true }),
    });
    const dialog = page.getByRole("dialog", { name: "Удалить видео?", exact: true });
    const open = async title => {
      await itemFor(title).getByRole("button", { name: "Удалить видео", exact: true }).click();
      await dialog.waitFor();
      assert(await dialog.getByText(`«${title}»`, { exact: true }).isVisible());
      assert(await dialog.getByText(/без возможности восстановления/).isVisible());
    };
    const confirm = () => dialog.getByRole("button", { name: "Удалить навсегда", exact: true });
    const title = "Удаление синтетического видео " + name;
    const id = await create(title, true);
    const watched = await context.newPage();
    await watched.goto(`${base}/watch/${id}`);
    await watched.locator("video").waitFor();
    await page.goto(base + "/studio");
    await itemFor(title).waitFor();

    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate(value => document.documentElement.dataset.theme = value, theme);
        await open(title);
        const cancel = dialog.getByRole("button", { name: "Отмена", exact: true });
        assert(await cancel.evaluate(button => button === document.activeElement), "Initial safe focus");
        await page.keyboard.press("Shift+Tab");
        assert(await confirm().evaluate(button => button === document.activeElement), "Native focus trap");
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width}/${theme}: no overflow`);
        const bounds = await dialog.boundingBox();
        assert(bounds.x >= 0 && bounds.x + bounds.width <= width);
        if (process.env.ARTIFACT_DIR)
          await page.screenshot({ path: path.join(process.env.ARTIFACT_DIR, `delete-${width}-${theme}.png`) });
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "hidden" });
        assert(await itemFor(title).getByRole("button", { name: "Удалить видео", exact: true })
          .evaluate(button => button === document.activeElement), "Focus restored after Escape");
      }
    }
    await open(title);
    await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
    assert.equal((await context.request.get(`${base}/api/videos/${id}`)).status(), 200);
    console.log("OK 390/1440, light/dark, title, irreversible warning, focus trap, Escape and cancellation");

    const url = `**/api/videos/${id}`;
    await page.route(url, route => route.request().method() === "DELETE"
      ? route.fulfill({ status: 503, json: { message: "Тестовая ошибка удаления" } }) : route.continue());
    await open(title);
    await confirm().click();
    await dialog.getByRole("alert").filter({ hasText: "Тестовая ошибка удаления" }).waitFor();
    assert(await itemFor(title).isVisible(), "HTTP failure retains video");
    await page.unroute(url);
    await page.route(url, route => route.request().method() === "DELETE"
      ? route.abort("failed") : route.continue());
    await confirm().click();
    await dialog.getByRole("alert").filter({ hasText: "Видео ещё доступно" }).waitFor();
    assert(await itemFor(title).isVisible(), "Network failure before commit retains video");
    await page.unroute(url);

    let requests = 0, release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route(url, async route => {
      if (route.request().method() !== "DELETE") return route.continue();
      requests++;
      await gate;
      await route.continue();
    });
    await confirm().click();
    assert(await dialog.getByRole("button", { name: "Удаление…", exact: true }).isDisabled());
    await dialog.getByRole("button", { name: "Удаление…", exact: true }).evaluate(button => {
      button.click(); button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    assert.equal(requests, 1, "Only one DELETE is in flight");
    release();
    await itemFor(title).waitFor({ state: "detached" });
    await watched.getByRole("heading", { name: "Видео недоступно", exact: true }).waitFor({ timeout: 10000 });
    assert.equal(await watched.locator("video").count(), 0);
    await page.unroute(url);
    assert(await page.getByRole("heading", { name: "Studio", exact: true })
      .evaluate(element => element === document.activeElement), "Focus after successful removal");
    for (const kind of ["media", "thumbnail"]) {
      assert.equal((await context.request.get(`${base}/api/videos/${id}/${kind}`)).status(), 404);
      assert.equal((await context.request.head(`${base}/api/videos/${id}/${kind}`)).status(), 404);
    }
    await watched.reload();
    await watched.getByRole("heading", { name: "Видео недоступно", exact: true }).waitFor();
    console.log("OK error recovery, no duplicate DELETE, Studio removal, open watch tab and old media denial");

    // The server commits DELETE but the browser loses the response.
    const uncertainTitle = "Ответ потерян " + name;
    const uncertainId = await create(uncertainTitle);
    await page.reload();
    await itemFor(uncertainTitle).waitFor();
    await page.route(`**/api/videos/${uncertainId}`, async route => {
      if (route.request().method() !== "DELETE") return route.continue();
      const response = await route.fetch();
      assert.equal(response.status(), 204);
      await route.abort("failed");
    });
    await open(uncertainTitle);
    await confirm().click();
    await itemFor(uncertainTitle).waitFor({ state: "detached" });
    assert.equal((await context.request.get(`${base}/api/me/videos/${uncertainId}`)).status(), 404);
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    console.log("OK lost response reconciles against authenticated Studio API; no unexpected console/page errors");
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
