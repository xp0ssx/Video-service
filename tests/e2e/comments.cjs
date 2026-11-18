// Targeted comment regressions. Real API/PostgreSQL/media; synthetic fixtures only.
const { chromium } = require("playwright-core");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const base = process.env.BASE_URL || "http://localhost:8080";
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const name = "comments_" + randomUUID().replaceAll("-", "").slice(0, 12);
    const registered = await context.request.post(base + "/api/auth/register", {
      headers: { Origin: base },
      data: { username: name, email: name + "@example.com", password: randomUUID() },
    });
    assert.equal(registered.status(), 201);
    const session = await registered.json();
    const headers = { Origin: base, "X-CSRF-Token": session.csrf_token };
    const draft = await context.request.post(base + "/api/videos", {
      headers, data: { title: "Comment threads " + name, visibility: "public" },
    });
    assert.equal(draft.status(), 201);
    const id = (await draft.json()).id;
    assert.equal((await context.request.put(`${base}/api/videos/${id}/source`, {
      headers: { ...headers, "Content-Type": "application/octet-stream" },
      data: fs.readFileSync(process.env.VIDEO_FIXTURE || "/fixtures/input.mp4"),
    })).status(), 202);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    let ready = false;
    for (let attempt = 0; attempt < 90; attempt++) {
      const video = await (await context.request.get(`${base}/api/me/videos/${id}`)).json();
      assert.notEqual(video.status, "failed");
      if (video.status === "ready") { ready = true; break; }
      await page.waitForTimeout(1000);
    }
    assert(ready, "Synthetic video became ready");
    const roots = [];
    let replyGets = 0;
    page.on("request", request => {
      if (request.method() === "GET" && /\/replies\?/.test(request.url())) replyGets++;
    });
    const thread = value => page.locator(`.comment-thread[data-comment-id="${value}"]`);
    const replyItem = value => page.locator(`.comment-reply[data-comment-id="${value}"]`);
    const like = item => item.getByRole("button", { name: "Нравится комментарий", exact: true }).first();
    async function posted(action) {
      const response = page.waitForResponse(response => response.url().endsWith(`/videos/${id}/comments`) && response.request().method() === "POST");
      await action();
      const result = await response;
      assert.equal(result.status(), 201);
      return result.json();
    }
    async function postApi(body, parent_id = null) {
      const result = await context.request.post(`${base}/api/videos/${id}/comments`, { headers, data: { body, parent_id } });
      assert.equal(result.status(), 201);
      return result.json();
    }
    await page.goto(`${base}/watch/${id}`);
    await page.getByLabel("Ваш комментарий", { exact: true }).waitFor();
    const cases = [
      "Обычный русский текст", "<b>Текст</b>", "<script>window.__commentXss=1</script>",
      `& < > " '`, "  \nСтрока 1\n\nСтрока 2\t \n", "Эмодзи 😀 👩🏽‍💻 e\u0301 漢字", "&lt; и &amp;",
      String.raw`C:\path\file "quoted"`,
    ];
    for (const body of cases) {
      await page.getByLabel("Ваш комментарий", { exact: true }).fill(body);
      const result = await posted(() => page.getByRole("button", { name: "Отправить комментарий", exact: true }).click());
      roots.push(result.id);
      assert.equal(result.body, body);
      await thread(result.id).waitFor();
      assert.equal(await thread(result.id).locator(".comment-body").textContent(), body);
    }
    assert.equal(await page.locator(".comment-body b,.comment-body script,.comment-body img").count(), 0);
    assert.equal(await page.evaluate(() => window.__commentXss), undefined);
    assert.equal(await thread(roots[4]).locator(".comment-body").evaluate(el => getComputedStyle(el).whiteSpace), "pre-wrap");
    assert.equal(replyGets, 0, "Replies are not fetched for root lists");
    // Code point limit matches PostgreSQL and Pydantic, including astral emoji.
    await page.getByLabel("Ваш комментарий", { exact: true }).fill("😀".repeat(2000));
    const boundary = await posted(() => page.getByRole("button", { name: "Отправить комментарий", exact: true }).click());
    assert.equal(Array.from(boundary.body).length, 2000);
    await page.getByLabel("Ваш комментарий", { exact: true }).fill("😀".repeat(2001));
    await page.getByRole("button", { name: "Отправить комментарий", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "от 1 до 2000" }).waitFor();
    await page.getByLabel("Ваш комментарий", { exact: true }).fill("");
    console.log("OK textarea/JSON/API/React/DOM roundtrip, Unicode length, literal HTML/entities, whitespace and XSS");

    const root = thread(roots[0]);
    await root.getByRole("button", { name: "Ответить", exact: true }).first().click();
    const replyBody = "  Ответ <b>буквально</b> &amp;\nВторая строка 😀  ";
    await root.getByLabel("Ваш ответ", { exact: true }).fill(replyBody);
    const firstReply = await posted(() => root.getByRole("button", { name: "Отправить ответ", exact: true }).click());
    assert.equal(firstReply.parent_id, roots[0]);
    await replyItem(firstReply.id).waitFor();
    assert.equal(await replyItem(firstReply.id).locator(".comment-body").textContent(), replyBody);
    await replyItem(firstReply.id).getByRole("button", { name: "Ответить", exact: true }).click();
    await root.getByLabel("Ваш ответ", { exact: true }).fill("Ответ на ответ");
    const secondReply = await posted(() => root.getByRole("button", { name: "Отправить ответ", exact: true }).click());
    assert.equal(secondReply.parent_id, roots[0]);
    assert.equal(await root.locator(".comment-replies .comment-replies").count(), 0);
    await root.getByRole("button", { name: "Скрыть ответы (2)", exact: true }).click();
    assert.equal(await replyItem(firstReply.id).isVisible(), false);
    const previousGets = replyGets;
    await root.getByRole("button", { name: "Показать ответы (2)", exact: true }).click();
    await replyItem(firstReply.id).waitFor();
    assert.equal(replyGets, previousGets, "Collapse preserves loaded replies");

    await like(root).click();
    await page.waitForFunction(rootId => document.querySelector(`.comment-thread[data-comment-id="${rootId}"] .comment-like`).getAttribute("aria-pressed") === "true", roots[0]);
    await like(replyItem(firstReply.id)).click();
    await page.waitForFunction(replyId => document.querySelector(`.comment-reply[data-comment-id="${replyId}"] .comment-like`).getAttribute("aria-pressed") === "true", firstReply.id);
    assert.equal(await like(replyItem(firstReply.id)).innerText(), "1");
    await like(replyItem(firstReply.id)).click();
    await page.waitForFunction(replyId => document.querySelector(`.comment-reply[data-comment-id="${replyId}"] .comment-like`).getAttribute("aria-pressed") === "false", firstReply.id);
    assert.equal(await like(replyItem(firstReply.id)).innerText(), "0");

    // A held request proves duplicate clicks are blocked; errors preserve state.
    const likeUrl = `**/api/videos/${id}/comments/${firstReply.id}/like`;
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let requests = 0;
    await page.route(likeUrl, async route => { requests++; await gate; await route.continue(); });
    await like(replyItem(firstReply.id)).click();
    assert(await like(replyItem(firstReply.id)).isDisabled());
    await like(replyItem(firstReply.id)).evaluate(button => button.click());
    assert.equal(requests, 1);
    release();
    await page.waitForFunction(replyId => document.querySelector(`.comment-reply[data-comment-id="${replyId}"] .comment-like`).getAttribute("aria-pressed") === "true", firstReply.id);
    await page.unroute(likeUrl);
    await page.route(likeUrl, route => route.fulfill({ status: 503, json: { message: "Тестовая ошибка лайка" } }));
    await like(replyItem(firstReply.id)).click();
    await replyItem(firstReply.id).getByRole("alert").filter({ hasText: "Тестовая ошибка лайка" }).waitFor();
    assert.equal(await like(replyItem(firstReply.id)).getAttribute("aria-pressed"), "true");
    assert.equal(await like(replyItem(firstReply.id)).innerText(), "1");
    await page.unroute(likeUrl);
    await root.getByRole("button", { name: "Ответить", exact: true }).first().click();
    const draftText = "Черновик сохраняется после ошибки";
    await root.getByLabel("Ваш ответ", { exact: true }).fill(draftText);
    const commentUrl = `**/api/videos/${id}/comments`;
    await page.route(commentUrl, route => route.request().method() === "POST"
      ? route.fulfill({ status: 503, json: { message: "Тестовая ошибка ответа" } }) : route.continue());
    await root.getByRole("button", { name: "Отправить ответ", exact: true }).click();
    await root.getByRole("alert").filter({ hasText: "Тестовая ошибка ответа" }).waitFor();
    assert.equal(await root.getByLabel("Ваш ответ", { exact: true }).inputValue(), draftText);
    assert.equal(await root.getByRole("button", { name: "Скрыть ответы (2)", exact: true }).getAttribute("aria-expanded"), "true");
    await page.unroute(commentUrl);
    await posted(() => root.getByRole("button", { name: "Отправить ответ", exact: true }).click());
    console.log("OK flattened reply-to-reply, collapse, root/reply likes/unlike, duplicate prevention and recoverable errors");

    // Separate pagination for roots and replies; state survives loading either page.
    for (let index = 0; index < 22; index++) await postApi("Ответ страницы " + index, roots[0]);
    const secondRoot = roots[1];
    await postApi("Другая ветка", secondRoot);
    for (let index = 0; index < 22; index++) await postApi("Корень страницы " + index);
    await postApi("x".repeat(1900), roots[0]);
    const beforeReload = replyGets;
    await page.reload();
    await thread(roots[0]).waitFor();
    assert.equal(replyGets, beforeReload, "Reload does not fetch collapsed replies");
    assert.equal(await like(thread(roots[0])).getAttribute("aria-pressed"), "true");
    await thread(roots[0]).getByRole("button", { name: /^Показать ответы/ }).click();
    await replyItem(firstReply.id).waitFor();
    assert.equal(await like(replyItem(firstReply.id)).getAttribute("aria-pressed"), "true");
    assert.equal(await thread(roots[0]).locator(".comment-reply").count(), 20);
    await thread(roots[0]).getByRole("button", { name: "Ещё ответы", exact: true }).click();
    await page.waitForFunction(rootId => document.querySelectorAll(`.comment-thread[data-comment-id="${rootId}"] .comment-reply`).length === 26, roots[0]);
    const repliesUrl = `**/api/videos/${id}/comments/${secondRoot}/replies?*`;
    await page.route(repliesUrl, route => route.fulfill({ status: 503, json: { message: "Тестовая ошибка загрузки" } }));
    await thread(secondRoot).getByRole("button", { name: /^Показать ответы/ }).click();
    await thread(secondRoot).getByRole("alert").filter({ hasText: "Тестовая ошибка загрузки" }).waitFor();
    assert(await replyItem(firstReply.id).isVisible());
    await page.unroute(repliesUrl);
    await thread(secondRoot).getByRole("button", { name: "Повторить загрузку ответов", exact: true }).click();
    await thread(secondRoot).getByText("Другая ветка", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Ещё комментарии", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll(".comment-thread").length === 31);
    assert(await replyItem(firstReply.id).isVisible());
    assert(await thread(secondRoot).getByText("Другая ветка", { exact: true }).isVisible());
    await like(thread(roots[0])).scrollIntoViewIfNeeded();
    const scrollBefore = await page.evaluate(() => scrollY);
    await like(thread(roots[0])).click();
    await page.waitForFunction(rootId => document.querySelector(`.comment-thread[data-comment-id="${rootId}"] .comment-like`).getAttribute("aria-pressed") === "false", roots[0]);
    assert(Math.abs(await page.evaluate(() => scrollY) - scrollBefore) < 3, "Like preserves scroll position");
    console.log("OK lazy loading, independent pagination, retry, reload persistence and stable expanded threads/scroll");

    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const theme of ["light", "dark"]) {
        await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
        await replyItem(firstReply.id).getByRole("button", { name: "Ответить", exact: true }).click();
        await thread(roots[0]).getByLabel("Ваш ответ", { exact: true }).fill("Мобильный ответ\n" + "z".repeat(1500));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No overflow: ${width}/${theme}`);
        const bounds = await thread(roots[0]).getByLabel("Ваш ответ", { exact: true }).boundingBox();
        assert(bounds.width > 180 && bounds.x + bounds.width <= width);
        await like(replyItem(firstReply.id)).focus();
        assert(await like(replyItem(firstReply.id)).evaluate(button => button === document.activeElement));
        if (process.env.ARTIFACT_DIR) {
          await thread(roots[0]).scrollIntoViewIfNeeded();
          await page.screenshot({ path: path.join(process.env.ARTIFACT_DIR, `comments-${width}-${theme}.png`), fullPage: false });
        }
        await thread(roots[0]).getByRole("button", { name: "Отмена", exact: true }).click();
      }
    }
    const guest = await browser.newContext({ viewport: { width: 390, height: 1000 } });
    const guestPage = await guest.newPage();
    await guestPage.goto(`${base}/watch/${id}`);
    await guestPage.locator(`.comment-thread[data-comment-id="${roots[0]}"]`).waitFor();
    assert(await guestPage.getByRole("button", { name: "Нравится комментарий", exact: true }).first().isDisabled());
    assert.equal(await guestPage.getByRole("button", { name: "Ответить", exact: true }).count(), 0);
    await guestPage.getByRole("button", { name: /^Показать ответы/ }).first().click();
    await guestPage.locator(`.comment-reply[data-comment-id="${firstReply.id}"]`).waitFor();
    assert.equal(await guestPage.locator(`.comment-reply[data-comment-id="${firstReply.id}"] .comment-like`).getAttribute("aria-pressed"), "false");
    assert.equal(await guestPage.locator(`.comment-reply[data-comment-id="${firstReply.id}"] .comment-like`).innerText(), "1");
    assert.deepEqual(errors, []);
    console.log("OK comment/reply forms at 390/1440 in both themes, keyboard focus, long text, guest reading and no page errors");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
