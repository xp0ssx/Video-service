// Full real flow, synthetic FFmpeg fixture only; no credentials are printed.
const {chromium}=require('playwright-core');
const {randomUUID}=require('node:crypto');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const base=process.env.BASE_URL||'http://localhost:8080';
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required']});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[];
  context.on('page',trackErrors);trackErrors(page);
  function trackErrors(current){
    current.on('pageerror',e=>errors.push(e.message));
    current.on('console',message=>{
      if(message.type()!=='error')return;
      // The session probe intentionally returns 401 before registration.
      if(message.location().url===base+'/api/auth/session'&&/status of 401/.test(message.text()))return;
      errors.push(message.text());
    });
  }
  async function layout(name){
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${name}: horizontal overflow at ${width}`);
      assert(await page.getByRole('searchbox',{name:'Поиск видео'}).isVisible());
      assert(await page.getByRole('button',{name:'Найти видео'}).isVisible());
      await page.screenshot({path:path.join(process.env.ARTIFACT_DIR||os.tmpdir(),`social-${name}-${width}.png`),fullPage:true});
    }
  }
  try{
    const suffix=randomUUID().replaceAll('-','').slice(0,12);
    const username='social_'+suffix;
    const title='Кириллица 100%_! '+suffix;
    const body='Проверка комментария <img src=x onerror="window.__commentXss=1"><script>window.__commentXss=2</script>';
    await page.goto(base+'/register');
    await page.getByLabel('Email',{exact:true}).fill(username+'@example.com');
    await page.getByLabel('Имя пользователя').fill(username);
    await page.getByLabel('Пароль').fill(randomUUID());
    await page.getByRole('button',{name:'Зарегистрироваться',exact:true}).click();
    await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();
    await page.goto(base+'/upload');
    await page.getByLabel('Название',{exact:true}).fill(title);
    await page.getByLabel('Описание',{exact:true}).fill('Synthetic end-to-end clip');
    await page.getByLabel('Доступ',{exact:true}).selectOption('public');
    await page.getByLabel('Видеофайл',{exact:true}).setInputFiles(process.env.VIDEO_FIXTURE||'/fixtures/input.mp4');
    await page.getByRole('button',{name:'Загрузить видео',exact:true}).click();
    const ready=page.getByRole('link',{name:'Смотреть готовое видео',exact:true});
    await ready.waitFor({timeout:90000});
    const href=await ready.getAttribute('href');const id=href.split('/').pop();
    await ready.click();
    await page.locator('video').waitFor();
    await page.locator('video').evaluate(video=>{video.muted=true;return video.play()});
    await page.waitForFunction(()=>document.querySelector('video').currentTime>.3);
    await page.locator('video').evaluate(video=>video.pause());
    console.log('OK register -> upload -> real FFmpeg -> actual playback');
    const like=page.getByRole('button',{name:'Нравится',exact:true});
    await like.click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="Нравится"]').getAttribute('aria-pressed')==='true');
    assert.equal(await page.getByTestId('likes-count').innerText(),'1');
    await page.getByLabel('Ваш комментарий').fill(body);
    await page.getByRole('button',{name:'Отправить комментарий',exact:true}).click();
    await page.getByText('Комментарий добавлен',{exact:true}).waitFor();
    assert.equal(await page.locator('.comment-body').innerText(),body);
    assert.equal(await page.locator('.comments-list img, .comments-list script').count(),0);
    assert.equal(await page.evaluate(()=>window.__commentXss),undefined);
    await page.reload();
    await page.waitForFunction(()=>{const button=document.querySelector('[aria-label="Нравится"]');return button&&!button.disabled&&button.getAttribute('aria-pressed')==='true'});
    await page.locator('.comment-body').filter({hasText:body}).waitFor();
    assert.equal(await page.getByTestId('likes-count').innerText(),'1');
    assert.equal(await page.locator('.comments-list img, .comments-list script').count(),0);
    assert.equal(await page.evaluate(()=>window.__commentXss),undefined);
    console.log('OK like/comment survive reload; user HTML remains text and does not execute');
    await layout('watch');
    const auth=await(await context.request.get(base+'/api/auth/session')).json();
    const headers={Origin:base,'X-CSRF-Token':auth.csrf_token};
    for(let n=0;n<2;n++)assert.deepEqual(await(await context.request.put(`${base}/api/videos/${id}/like`,{headers})).json(),{likes_count:1,liked_by_me:true});
    await page.getByRole('searchbox',{name:'Поиск видео'}).fill(title.toUpperCase());
    await page.getByRole('button',{name:'Найти видео'}).click();
    await page.getByRole('link',{name:'Смотреть: '+title,exact:true}).waitFor();
    assert.equal(await page.locator('.video-card').count(),1);
    await page.locator('.video-card img').evaluate(image=>image.complete?Promise.resolve():new Promise(resolve=>image.addEventListener('load',resolve,{once:true})));
    assert((await page.locator('.video-card img').evaluate(image=>image.naturalWidth))>0);
    await layout('search');
    await page.goto(base+'/');
    await page.getByRole('link',{name:'Смотреть: '+title,exact:true}).waitFor();
    await layout('feed');
    console.log('OK feed, Cyrillic/literal search, real thumbnails, layouts at 390/1440');
    const guest=await browser.newContext();guest.on('page',trackErrors);const guestPage=await guest.newPage();
    await guestPage.goto(base+href);
    await guestPage.locator('.comment-body').filter({hasText:body}).waitFor();
    assert(await guestPage.getByRole('button',{name:'Нравится',exact:true}).isDisabled());
    assert.equal(await guestPage.getByLabel('Ваш комментарий').count(),0);
    assert.equal((await guest.request.put(`${base}/api/videos/${id}/like`,{headers:{Origin:base}})).status(),401);
    assert.equal((await context.request.patch(`${base}/api/videos/${id}`,{headers,data:{visibility:'private'}})).status(),200);
    assert.equal((await guest.request.get(`${base}/api/videos/${id}/comments`)).status(),404);
    const recipient=await browser.newContext();const name='grant_'+suffix;
    const registered=await recipient.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username:name,email:name+'@example.com',password:randomUUID()}});
    assert.equal(registered.status(),201);const recipientHeaders={Origin:base,'X-CSRF-Token':(await registered.json()).csrf_token};
    assert.equal((await context.request.put(`${base}/api/videos/${id}/access/${name}`,{headers})).status(),204);
    assert.equal((await recipient.request.get(`${base}/api/videos/${id}/comments`)).status(),200);
    assert.equal((await recipient.request.put(`${base}/api/videos/${id}/like`,{headers:recipientHeaders})).status(),200);
    assert.equal((await context.request.delete(`${base}/api/videos/${id}/access/${name}`,{headers})).status(),204);
    assert.equal((await recipient.request.get(`${base}/api/videos/${id}/comments`)).status(),404);
    assert.equal((await recipient.request.delete(`${base}/api/videos/${id}/like`,{headers:recipientHeaders})).status(),404);
    assert.equal((await recipient.request.post(`${base}/api/videos/${id}/comments`,{headers:recipientHeaders,data:{body:'denied'}})).status(),404);
    assert.equal((await context.request.patch(`${base}/api/videos/${id}`,{headers,data:{visibility:'public'}})).status(),200);
    console.log('OK guest restrictions, private interactions, grants and revoke');
    assert.deepEqual(errors,[]);
    console.log('OK no unexpected browser console errors or uncaught exceptions');
  }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exit(1)});
