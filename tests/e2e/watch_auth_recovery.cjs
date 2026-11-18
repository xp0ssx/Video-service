// Targeted Chromium regressions on synthetic data; safe for the main stand.
// VIDEO_FIXTURE must be a real video longer than 15 seconds. No existing media is deleted.
const { chromium } = require('playwright-core');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://localhost:8080';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const owner = await browser.newContext();
  const created = [], errors = [];
  let headers;
  async function context(authenticated = false) {
    const ctx = await browser.newContext(authenticated ? {storageState: await owner.storageState()} : {});
    return ctx;
  }
  async function page(ctx) {
    const p = await ctx.newPage();
    p.on('pageerror', e => errors.push(e.message));
    return p;
  }
  const ready = p => p.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
  const play = p => p.getByRole('button', {name: 'Воспроизвести видео', exact: true}).click();
  try {
    const username = 'restore_' + randomUUID().replaceAll('-', '').slice(0, 12);
    const password = randomUUID();
    const registered = await owner.request.post(base + '/api/auth/register', {headers: {Origin: base},
      data: {username, email: username + '@example.com', password}});
    assert.equal(registered.status(), 201);
    headers = {Origin: base, 'X-CSRF-Token': (await registered.json()).csrf_token};
    async function draft(visibility = 'public') {
      const response = await owner.request.post(base + '/api/videos', {headers,
        data: {title: 'Synthetic Watch/auth acceptance', visibility}});
      assert.equal(response.status(), 201);
      const id = (await response.json()).id;
      created.push(id);
      return id;
    }
    const id = await draft();
    assert.equal((await owner.request.put(`${base}/api/videos/${id}/source`, {
      headers: {...headers, 'Content-Type': 'application/octet-stream'},
      data: fs.readFileSync(process.env.VIDEO_FIXTURE),
    })).status(), 202);
    let video;
    for (let i = 0; i < 180; i++) {
      video = await (await owner.request.get(`${base}/api/me/videos/${id}`)).json();
      assert.notEqual(video.status, 'failed');
      if (video.status === 'ready') break;
      await sleep(500);
    }
    assert.equal(video.status, 'ready');
    assert(video.duration_ms > 15000);
    const count = async () => (await (await owner.request.get(`${base}/api/videos/${id}`)).json()).views_count;
    for (const mode of ['automatic', 'manual']) {
      const ctx = await context(), p = await page(ctx);
      let requests = 0;
      await p.route(`**/api/videos/${id}`, route => ++requests === 1
        ? route.fulfill({status: 503, json: {message: 'Synthetic metadata outage'}}) : route.continue());
      await p.goto(`${base}/watch/${id}`);
      await p.getByRole('button', {name: 'Повторить загрузку видео', exact: true}).waitFor();
      if (mode === 'manual') await p.getByRole('button', {name: 'Повторить загрузку видео', exact: true}).click();
      await ready(p);
      assert.equal(await p.getByRole('heading', {name: 'Видео недоступно'}).count(), 0);
      await play(p);
      await p.waitForFunction(() => document.querySelector('video').currentTime > 1);
      await p.getByRole('button', {name: 'Пауза', exact: true}).click();
      await p.getByRole('button', {name: 'Настройки воспроизведения', exact: true}).click();
      await p.getByLabel('Скорость', {exact: true}).selectOption('1.5');
      assert.equal(await p.locator('video').evaluate(el => el.playbackRate), 1.5);
      assert(requests >= 2);
      console.log(JSON.stringify({check: 'metadata-503-recovery', mode, requests, real_playback: true}));
      await ctx.close();
    }
    // 401/403/404 are terminal, including periodic/focus rechecks.
    for (const status of [401, 403, 404]) {
      const ctx = await context(), p = await page(ctx);
      let requests = 0;
      await p.route(`**/api/videos/${id}`, route => {requests++; return route.fulfill({status, json: {}});});
      await p.goto(`${base}/watch/${id}`);
      await p.getByRole('heading', {name: 'Видео недоступно'}).waitFor();
      await p.evaluate(() => window.dispatchEvent(new Event('focus')));
      await p.waitForTimeout(5200);
      assert.equal(requests, 1);
      assert.equal(await p.locator('video').count(), 0);
      console.log(JSON.stringify({check: 'metadata-terminal', status, requests}));
      await ctx.close();
    }
    const bounded = await context(), bp = await page(bounded);
    let metadataAttempts = 0, metadataHealthy = false;
    await bp.route(`**/api/videos/${id}`, route => {
      metadataAttempts++;
      return metadataHealthy ? route.continue() : route.fulfill({status:503,json:{}});
    });
    await bp.goto(`${base}/watch/${id}`);
    await bp.getByRole('button',{name:'Повторить загрузку видео',exact:true}).waitFor();
    for(let i=0;i<3;i++) {
      const response=bp.waitForResponse(r=>r.url().endsWith(`/videos/${id}`));
      await bp.evaluate(()=>window.dispatchEvent(new Event('focus')));
      await response;
    }
    await bp.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await bp.waitForTimeout(5200);
    assert.equal(metadataAttempts,4,'Metadata recovery is bounded across interval/focus events');
    metadataHealthy=true;
    await bp.getByRole('button',{name:'Повторить загрузку видео',exact:true}).click();
    await ready(bp);
    assert.equal(metadataAttempts,5);
    console.log(JSON.stringify({check:'metadata-exhaustion-manual-recovery',metadataAttempts}));
    await bounded.close();
    const unmount = await context(), up = await page(unmount);
    let held;
    const requested=new Promise(resolve=>{held=resolve;});
    await up.route(`**/api/videos/${id}`,route=>held(route));
    await up.goto(`${base}/watch/${id}`);
    const pending=await requested;
    await up.getByRole('link',{name:'Video-service — главная',exact:true}).click();
    await up.waitForURL(base+'/');
    await pending.continue();
    await up.waitForTimeout(250);
    assert.equal(await up.locator('video').count(),0);
    assert.equal(await up.getByRole('heading',{name:'Видео недоступно'}).count(),0);
    console.log(JSON.stringify({check:'metadata-unmount-pending-response',watch_mounted:false}));
    await unmount.close();
    // A failed session restore with a valid HttpOnly cookie must never POST as guest.
    const auth = await context(true), ap = await page(auth);
    let restores = 0, starts = 0, release;
    const gate = new Promise(resolve => {release = resolve;});
    await ap.route('**/api/auth/session', async route => {
      restores++;
      if (restores === 1) return route.fulfill({status: 503, json: {message: 'Synthetic auth outage'}});
      if (restores === 2) return route.abort('failed');
      await gate;
      return route.continue();
    });
    ap.on('request', request => {
      if (!request.url().includes(`/videos/${id}/views`)) return;
      assert(request.headers()['x-csrf-token'] === headers['X-CSRF-Token'], 'Views require the restored CSRF token');
      if (request.url().endsWith('/views')) starts++;
    });
    await ap.goto(`${base}/watch/${id}`);
    await ready(ap);
    await ap.getByText('Не удалось восстановить сессию. Повторите попытку.', {exact: true}).waitFor();
    await ap.locator('video').evaluate(el => {el.dataset.lifecycle = 'same-player';});
    await play(ap);
    await ap.waitForFunction(() => document.querySelector('video').currentTime > 3.5);
    assert.equal(starts, 0);
    assert.equal(await count(), 0);
    release();
    await ap.getByRole('button', {name: 'Аккаунт', exact: true}).waitFor();
    assert.equal(await ap.locator('video').getAttribute('data-lifecycle'), 'same-player');
    assert(await ap.locator('video').evaluate(el => !el.paused && el.currentTime > 3));
    await ap.waitForFunction(() => document.querySelector('.watch-views')?.textContent === '1 просмотр', null, {timeout: 18000});
    assert.equal(await count(), 1);
    assert.equal(starts, 1);
    console.log(JSON.stringify({check: 'auth-503-network-recovery', restores, starts, same_player: true, views_count: 1}));
    await auth.close();
    // Exhaustion remains unknown, has no guest UI/POST, and offers explicit recovery.
    const manual = await context(true), mp = await page(manual);
    let attempts = 0, healthy = false, premature = 0;
    await mp.route('**/api/auth/session', route => {
      attempts++;
      return healthy ? route.continue() : route.fulfill({status: 503, json: {}});
    });
    mp.on('request', r => {if (r.url().includes(`/videos/${id}/views`) && !healthy) premature++;});
    await mp.goto(`${base}/watch/${id}`);
    await ready(mp);
    await play(mp);
    const retry = mp.getByRole('button', {name: 'Повторить восстановление сессии', exact: true});
    await retry.waitFor();
    await mp.waitForFunction(() => [...document.querySelectorAll('button')].some(b =>
      b.textContent === 'Повторить восстановление сессии' && !b.disabled), null, {timeout: 12000});
    await mp.waitForTimeout(1500);
    assert.equal(attempts, 4);
    assert.equal(premature, 0);
    assert.equal(await mp.getByRole('link', {name: 'Войти', exact: true}).count(), 0);
    healthy = true;
    await retry.click();
    await mp.getByRole('button', {name: 'Аккаунт', exact: true}).waitFor();
    await mp.locator('video').evaluate(async el => {el.pause();el.currentTime=0;await el.play();});
    await mp.waitForFunction(() => document.querySelector('.watch-views')?.textContent === '1 просмотр', null, {timeout: 16000});
    await mp.waitForFunction(() => document.querySelector('video').currentTime > 10.5);
    assert.equal(await count(), 1, 'Restoration/remount/replay must keep 24h dedup');
    console.log(JSON.stringify({check: 'auth-exhaustion-manual-recovery', attempts, premature, views_count: 1}));
    await manual.close();
    for (const status of [403, 404]) {
      const ctx = await context(true), p = await page(ctx);
      let attempts = 0, posts = 0;
      await p.route('**/api/auth/session', route => {attempts++;return route.fulfill({status,json:{}});});
      p.on('request',r=>{if(r.url().includes(`/videos/${id}/views`))posts++;});
      await p.goto(`${base}/watch/${id}`);await ready(p);await play(p);await p.waitForTimeout(5200);
      assert.equal(attempts,1);assert.equal(posts,0);
      console.log(JSON.stringify({check:'auth-terminal',status,attempts,posts}));
      await ctx.close();
    }
    // Pending restore callbacks must not replace a newer login/logout identity.
    const swap = await context();
    const login = await swap.request.post(base + '/api/auth/login', {headers: {Origin: base},
      data: {email: username + '@example.com', password}});
    assert.equal(login.status(), 200);
    const sp = await page(swap);
    await sp.goto(`${base}/watch/${id}`);await ready(sp);
    await sp.getByRole('button',{name:'Аккаунт',exact:true}).click();
    await sp.getByRole('button',{name:'Выйти',exact:true}).click();
    await sp.getByRole('link',{name:'Войти',exact:true}).waitFor();
    await sp.goto(`${base}/watch/${id}`);await ready(sp);await play(sp);
    await sp.waitForFunction(()=>document.querySelector('.watch-views')?.textContent==='2 просмотра',null,{timeout:16000});
    assert.equal(await count(),2,'A genuine guest still qualifies after logout');
    console.log(JSON.stringify({check:'logout-to-guest',views_count:2}));
    await swap.close();
    const privateId=await draft('private'), outsider=await context();
    for(const suffix of ['', '/media', '/thumbnail'])assert.equal((await outsider.request.get(`${base}/api/videos/${privateId}${suffix}`)).status(),404);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({result:'PASS',checks:'Watch + auth restoration + privacy',browser_errors:0}));
  } finally {
    try {
      for (const id of created) {
        const response = await owner.request.delete(`${base}/api/videos/${id}`, {headers});
        console.log(JSON.stringify({cleanup:{video_id:id,status:response.status()}}));
        assert.equal(response.status(),204);
      }
    } finally { await browser.close(); }
  }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
