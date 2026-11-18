// Targeted real-media player regression. Exactly four screenshots, no visual matrix.
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.env.BASE_URL || "http://localhost:8080";
const output = process.env.SCREENSHOT_DIR || ".local/mobile-player";
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
  });
  const page = await context.newPage();
  const errors = [], ranges = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("response", (r) => { if (r.url().endsWith("/media")) ranges.push(r.status()); });
  const button = (name) => page.getByRole("button", { name, exact: true });
  const slider = (name) => page.getByRole("slider", { name, exact: true });
  const settings = button("Настройки воспроизведения");
  const speed = page.getByRole("combobox", { name: "Скорость", exact: true });
  const player = page.getByRole("region", { name: /Видеоплеер:/ });
  const media = page.locator("video");
  async function focused(locator) {
    assert(await locator.evaluate((el) => el === document.activeElement), "Expected keyboard focus");
  }
  async function noOverflow() {
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Horizontal overflow");
    for (const locator of [player, page.locator(".player-controls"), settings]) {
      const box = await locator.boundingBox();
      assert(box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1);
    }
  }
  async function target(locator, minWidth = 44) {
    const box = await locator.boundingBox();
    assert(box && box.width >= minWidth && box.height >= 44, "Touch target at least 44px");
    assert(await locator.isVisible() && await locator.isEnabled());
  }
  async function capture(name) {
    await page.screenshot({ path: path.join(output, name) });
  }
  try {
    const username = "player_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registered = await context.request.post(base + "/api/auth/register", {
      headers: { Origin: base },
      data: { username, email: username + "@example.com", password: randomUUID() },
    });
    assert.equal(registered.status(), 201);
    const headers = { Origin: base, "X-CSRF-Token": (await registered.json()).csrf_token };
    const draft = await context.request.post(base + "/api/videos", {
      headers, data: { title: "Mobile player · synthetic clip", visibility: "public" },
    });
    assert.equal(draft.status(), 201);
    const id = (await draft.json()).id;
    const uploaded = await context.request.put(`${base}/api/videos/${id}/source`, {
      headers: { ...headers, "Content-Type": "application/octet-stream" },
      data: fs.readFileSync(process.env.VIDEO_FIXTURE || "/fixtures/input.mp4"),
    });
    assert.equal(uploaded.status(), 202);
    let ready = false;
    for (let i = 0; i < 90; i++) {
      const state = await (await context.request.get(`${base}/api/me/videos/${id}`)).json();
      assert.notEqual(state.status, "failed");
      if (state.status === "ready") { ready = true; break; }
      await page.waitForTimeout(1000);
    }
    assert(ready, "Synthetic video ready");
    await page.goto(base + "/watch/" + id);
    await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
    assert(Math.abs(await media.evaluate((v) => v.duration) - 12) < 0.1);
    for (const theme of ["light", "dark"]) {
      await button("Аккаунт").tap();
      await page.getByRole("radio", { name: theme === "light" ? "Светлая" : "Тёмная", exact: true }).check();
      await page.keyboard.press("Escape");
      await page.waitForFunction((value) => document.documentElement.dataset.theme === value, theme);
      assert.equal(await button("Широкий экран").count(), 0);
      assert.equal(await slider("Громкость").count(), 0);
      for (const control of [button("Воспроизвести"), settings, button("Полноэкранный режим"), button("Воспроизвести видео"), slider("Позиция воспроизведения")]) await target(control);
      const boxes = await page.locator(".player-control-row").evaluate((row) =>
        [...row.children].filter((el) => el.tagName === "BUTTON" || el.classList.contains("player-settings-wrap"))
          .map((el) => { const b = el.getBoundingClientRect(); return { x: b.x, right: b.right }; }));
      for (let i = 1; i < boxes.length; i++) assert(boxes[i].x - boxes[i - 1].right >= 8);
      assert(await page.locator(".player-time").evaluate((el) => parseFloat(getComputedStyle(el).fontSize) >= 14));
      const videoBox = await media.boundingBox(), controlsBox = await page.locator(".player-controls").boundingBox();
      assert(controlsBox.y >= videoBox.y + videoBox.height - 1, "Controls below video");
      await button("Воспроизвести").tap();
      await page.waitForFunction(() => !document.querySelector("video").paused && document.querySelector("video").currentTime > 0.3);
      await button("Пауза").tap();
      assert(await media.evaluate((v) => v.paused));
      // Exercise the actual touch area near its lower edge, not fill() alone.
      const seek = slider("Позиция воспроизведения"), box = await seek.boundingBox();
      await page.touchscreen.tap(box.x + box.width * 0.65, box.y + box.height - 5);
      await page.waitForFunction(() => document.querySelector("video").currentTime > 7 && !document.querySelector("video").seeking);
      await seek.focus();
      const before = await media.evaluate((v) => v.currentTime);
      await page.keyboard.press("ArrowLeft");
      assert((await media.evaluate((v) => v.currentTime)) < before);
      await seek.fill("4");
      await page.waitForFunction(() => !document.querySelector("video").seeking);
      await noOverflow();
      await capture(`player-390-${theme}.png`);
      await settings.focus();
      await page.keyboard.press("Enter");
      assert.equal(await settings.getAttribute("aria-expanded"), "true");
      const mute = button((await media.evaluate((v) => v.muted)) ? "Включить звук" : "Выключить звук");
      await focused(mute);
      await target(mute);
      await target(slider("Громкость"));
      await target(speed);
      const panel = await page.locator(".player-settings").boundingBox();
      assert(panel.x >= 0 && panel.x + panel.width <= 390 && panel.y >= videoBox.y);
      await button("Выключить звук").tap();
      assert(await media.evaluate((v) => v.muted));
      await button("Включить звук").tap();
      assert.equal(await media.evaluate((v) => v.muted), false);
      const volumeBox = await slider("Громкость").boundingBox();
      await page.touchscreen.tap(volumeBox.x + volumeBox.width * 0.35, volumeBox.y + volumeBox.height - 4);
      assert(Math.abs(await media.evaluate((v) => v.volume) - 0.35) < 0.06);
      await button("Выключить звук").focus();
      await page.keyboard.press("Tab");
      await focused(slider("Громкость"));
      // volume changes synchronously; the controlled range follows volumechange.
      // Wait for that render before testing the native keyboard increment.
      await page.waitForFunction(() => Math.abs(Number(document.querySelector('input[aria-label="Громкость"]').value) - document.querySelector("video").volume) < 0.001);
      const previousVolume = await media.evaluate((v) => v.volume);
      await page.keyboard.press("ArrowRight");
      await page.waitForFunction(previous => Math.abs(document.querySelector("video").volume - previous - 0.05) < 0.001, previousVolume);
      assert(Math.abs(await media.evaluate((v) => v.volume) - previousVolume - 0.05) < 0.001);
      await page.keyboard.press("Tab");
      await focused(speed);
      // Native select keyboard interaction must close the panel and return focus.
      await page.keyboard.press("Home");
      await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Настройки воспроизведения");
      assert.equal(await media.evaluate((v) => v.playbackRate), 0.25);
      await focused(settings);
      assert.equal(await settings.getAttribute("aria-expanded"), "false");
      await page.keyboard.press("Enter");
      await speed.focus();
      await speed.selectOption("1.5");
      assert.equal(await media.evaluate((v) => v.playbackRate), 1.5);
      await focused(settings);
      await page.keyboard.press("Enter");
      assert.equal(await speed.inputValue(), "1.5");
      await speed.focus();
      await page.keyboard.press("Escape");
      await focused(settings);
      assert.equal(await settings.getAttribute("aria-expanded"), "false");
      await page.keyboard.press("Enter");
      await slider("Громкость").focus();
      await page.keyboard.press("Escape");
      await focused(settings);
      await settings.tap();
      if (theme === "dark") await capture("player-390-settings.png");
      await page.keyboard.press("Escape");
      await noOverflow();
      await button("Полноэкранный режим").tap();
      await page.waitForFunction(() => !!document.fullscreenElement);
      await button("Выйти из полноэкранного режима").tap();
      await page.waitForFunction(() => !document.fullscreenElement);
      await player.focus();
      await page.keyboard.press("t");
      assert.equal(await page.locator(".watch-page.theater-mode").count(), 0);
      await player.press("Home");
      // Reset to the initial paused state without remounting the player.
      await page.reload();
      await page.waitForFunction(() => document.querySelector("video")?.readyState >= 2);
      console.log(`OK mobile 390 ${theme}: touch play/seek/volume, speed, focus/Escape/reopen, fullscreen, targets, no overflow`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await button("Широкий экран").waitFor();
    assert.equal(await button("Широкий экран").getAttribute("aria-pressed"), "false");
    await settings.focus();
    await page.keyboard.press("Enter");
    await focused(speed);
    await speed.selectOption("2");
    await focused(settings);
    assert.equal(await media.evaluate((v) => v.playbackRate), 2);
    await page.keyboard.press("Enter");
    await page.keyboard.press("Escape");
    await focused(settings);
    await button("Широкий экран").click();
    assert.equal(await button("Широкий экран").getAttribute("aria-pressed"), "true");
    await slider("Громкость").fill("0.6");
    await slider("Позиция воспроизведения").fill("5");
    await page.waitForFunction(() => !document.querySelector("video").seeking);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => !document.querySelector(".theater-mode"));
    assert.equal(await button("Широкий экран").count(), 0);
    assert.equal(await media.evaluate((v) => v.playbackRate), 2);
    assert(Math.abs(await media.evaluate((v) => v.currentTime) - 5) < 0.1);
    assert.equal(await media.evaluate((v) => v.volume), 0.6);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await button("Широкий экран").waitFor();
    assert.equal(await button("Широкий экран").getAttribute("aria-pressed"), "false");
    await noOverflow();
    await capture("player-1440-desktop.png");
    assert(ranges.includes(206), "Real media HTTP 206");
    assert.deepEqual(errors, []);
    console.log("OK desktop focus/theater, responsive state, real media 206, no console/page errors; four screenshots saved");
  } finally { await browser.close(); }
})().catch((e) => { console.error(e.stack); process.exitCode = 1; });
