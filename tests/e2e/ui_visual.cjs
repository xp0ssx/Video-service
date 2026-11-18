const { chromium } = require("playwright-core");
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const { fixture } = require("./ui_fixture.cjs");
const artifacts = process.env.ARTIFACT_DIR || "/artifacts",
  phase = process.env.PHASE || "after";
fs.mkdirSync(path.join(artifacts, phase), { recursive: true });
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage({
        viewport: { width: 1440, height: 1000 },
        timezoneId: "Europe/Moscow",
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await fixture(page);
    for (const theme of ["light", "dark"]) {
      await page.addInitScript(
        (t) => localStorage.setItem("video-service-theme", t),
        theme,
      );
      for (const width of [1440, 390, 768, 1920]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const [name, url] of [
          ["home", "/"],
          ["watch", "/watch/1"],
          ["playlist", "/playlists/p1"],
          ["search", "/results?q=свет"],
          ["channel", "/channels/studio_light"],
          ["playlist-editor", "/studio/playlists/p1"],
          ["playlists", "/studio/playlists"],
          ["subscriptions", "/subscriptions"],
          ["upload", "/upload"],
          ["studio", "/studio"],
          ["login", "/login"],
          ["register", "/register"],
          ["moderation", "/moderation"],
          ["admin", "/admin"],
        ]) {
          await page.goto("http://localhost:8080" + url);
          await page.waitForTimeout(250);
          await page
            .locator("img")
            .evaluateAll((images) =>
              images.forEach((img) => (img.loading = "eager")),
            );
          await page.waitForFunction(() =>
            Array.from(document.images).every((img) => img.complete),
          );
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
            `${name} ${theme} ${width}: horizontal overflow`,
          );
          if (
            [1440, 390].includes(width) ||
            ["home", "watch", "playlist", "search", "channel"].includes(name)
          )
            await page.screenshot({
              path: path.join(
                artifacts,
                phase,
                `${name}-${theme}-${width}.png`,
              ),
              fullPage: true,
            });
          if (name === "watch") {
            await page
              .getByRole("heading", { name: "Комментарии", exact: true })
              .scrollIntoViewIfNeeded();
            await page.screenshot({
              path: path.join(
                artifacts,
                phase,
                `comments-${theme}-${width}.png`,
              ),
            });
          }
        }
      }
    }
    assert.deepEqual(errors, []);
    console.log(
      "OK 14 routes × 4 widths × 2 themes, no overflow/console errors/pageerror; visual artifacts saved",
    );
    const longPage = await browser.newPage();
    await fixture(longPage, { long: true });
    for (const width of [390, 768, 1440, 1920]) {
      await longPage.setViewportSize({ width, height: 1000 });
      for (const url of [
        "/",
        "/results?q=" + encodeURIComponent("Д".repeat(200)),
        "/watch/1",
        "/playlists/p1",
        "/channels/" + "u".repeat(32),
        "/studio/playlists/p1",
      ]) {
        await longPage.goto("http://localhost:8080" + url);
        await longPage.waitForTimeout(100);
        assert(
          await longPage.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `Long content overflow: ${width} ${url}`,
        );
      }
    }
    console.log("OK long titles/descriptions/usernames at all 4 widths");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.stack);
  process.exitCode = 1;
});
