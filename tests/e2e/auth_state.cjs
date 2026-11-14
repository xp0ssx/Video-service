// Deterministic browser regressions: HTTP response order is controlled explicitly.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const session = {user: {id: 'test-user', username: 'new_user', role: 'user'}, csrf_token: 'test-csrf'};
const json = (status, body) => ({status, contentType: 'application/json', body: JSON.stringify(body)});
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    for (const action of ['register', 'login']) {
      for (const lateStatus of [401, 200]) {
        const page = await browser.newPage();
        const pending = [];
        await page.route('**/api/auth/session', route => pending.push(route));
        await page.route(`**/api/auth/${action}`, route => route.fulfill(json(action === 'register' ? 201 : 200, session)));
        await page.goto(`http://localhost:8080/${action}`);
        await page.waitForFunction(() => document.querySelector('input[name=email]'));
        // GET must have started, and remains unanswered until after the form succeeds.
        await page.waitForTimeout(50);
        assert(pending.length > 0);
        await page.getByLabel('Email', {exact: true}).fill('test@example.com');
        if (action === 'register') await page.getByLabel('Имя пользователя').fill('new_user');
        await page.getByLabel('Пароль').fill('synthetic-test-password');
        await page.getByRole('button', {name: action === 'register' ? 'Зарегистрироваться' : 'Войти', exact: true}).click();
        await page.waitForURL('http://localhost:8080/');
        await page.getByRole('button', {name: 'Выйти', exact: true}).waitFor();
        let responses = 0;
        page.on('response', response => { if (response.url().endsWith('/api/auth/session')) responses++; });
        for (const route of pending) await route.fulfill(json(lateStatus, lateStatus === 401 ? {message: 'Требуется вход'} : {...session, user: {...session.user, username: 'old_user'}}));
        await page.waitForTimeout(150);
        assert(responses > 0, 'Delayed HTTP response must actually arrive');
        assert((await page.locator('.account').innerText()).includes('new_user'), 'Stale restoration replaced current session');
        assert.equal(await page.getByRole('button', {name: 'Выйти', exact: true}).count(), 1);
        console.log(`OK ${action}: late restore ${lateStatus} ignored`);
        await page.close();
      }
    }
    for (const status of [401, 403]) {
      const page = await browser.newPage();
      await page.route('**/api/auth/session', route => route.fulfill(json(200, session)));
      await page.route('**/api/auth/logout', route => route.fulfill(json(status, {message: 'Logout denied'})));
      await page.goto('http://localhost:8080/');
      await page.getByRole('button', {name: 'Выйти', exact: true}).click();
      if (status === 401) {
        await page.getByRole('link', {name: 'Войти', exact: true}).waitFor();
        assert.equal(await page.getByRole('button', {name: 'Выйти', exact: true}).count(), 0);
      } else {
        await page.getByRole('alert').waitFor();
        assert((await page.locator('.account').innerText()).includes('new_user'));
        assert.equal(await page.getByRole('button', {name: 'Выйти', exact: true}).count(), 1);
      }
      console.log(`OK logout ${status}: ${status === 401 ? 'session cleared' : 'session retained'}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exit(1); });
