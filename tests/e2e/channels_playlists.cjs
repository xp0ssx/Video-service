// Real two-user flow, synthetic video only. Credentials remain in memory.
const {chromium}=require('playwright-core');
const {randomUUID}=require('node:crypto');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='http://localhost:8080';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required']});
 const errors=[];
 async function user(prefix){
  const context=await browser.newContext({viewport:{width:1440,height:1000},timezoneId:'Europe/Moscow'}),page=await context.newPage(),name=prefix+randomUUID().replaceAll('-','').slice(0,12);
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!(m.location().url===base+'/api/auth/session'&&/status of 401/.test(m.text())))errors.push(m.text())});
  await page.goto(base+'/register');await page.getByLabel('Email',{exact:true}).fill(name+'@example.com');await page.getByLabel('Имя пользователя').fill(name);await page.getByLabel('Пароль').fill(randomUUID());await page.getByRole('button',{name:'Зарегистрироваться',exact:true}).click();await page.getByRole('button',{name:'Аккаунт',exact:true}).waitFor();
  const session=await(await context.request.get(base+'/api/auth/session')).json();
  return {context,page,name,headers:{Origin:base,'X-CSRF-Token':session.csrf_token}};
 }
 async function layout(page,name){for(const width of [390,1440]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' overflow');if(process.env.ARTIFACT_DIR)await page.screenshot({path:path.join(process.env.ARTIFACT_DIR,`${name}-${width}.png`),fullPage:true})}}
 try{
  const owner=await user('owner5_'),viewer=await user('viewer5_');
  const p=owner.page,v=viewer.page;
  async function upload(title,visibility){await p.goto(base+'/upload');await p.getByLabel('Название',{exact:true}).fill(title);await p.getByLabel('Описание',{exact:true}).fill('Synthetic stage five');await p.getByLabel('Доступ',{exact:true}).selectOption(visibility);await p.getByLabel('Видеофайл',{exact:true}).setInputFiles(process.env.VIDEO_FIXTURE||'/fixtures/input.mp4');await p.getByRole('button',{name:'Загрузить видео',exact:true}).click();const ready=p.getByRole('link',{name:'Смотреть готовое видео',exact:true});await ready.waitFor({timeout:90000});return (await ready.getAttribute('href')).split('/').pop()}
  const a=await upload('First clip','public'),b=await upload('Private clip','private');
  console.log('OK two registrations and two uploads through real FFmpeg');
  await p.getByRole('button',{name:'Аккаунт',exact:true}).click();await p.getByRole('link',{name:owner.name,exact:true}).click();await p.getByText('Редактировать канал',{exact:true}).click();await p.getByLabel('Название',{exact:true}).fill('Канал автора');await p.getByLabel('Описание',{exact:true}).fill('Описание канала');await p.getByRole('button',{name:'Сохранить канал'}).click();await p.getByRole('heading',{name:'Канал автора',exact:true}).waitFor();
  await p.goto(base+'/studio/playlists');await p.getByLabel('Название',{exact:true}).fill('Плейлист E2E');await p.getByLabel('Описание',{exact:true}).fill('Два ролика');await p.getByRole('button',{name:'Создать плейлист',exact:true}).click();await p.getByRole('heading',{name:'Редактировать плейлист',exact:true}).waitFor();const pid=p.url().split('/').pop();
  for(const [id,title] of [[a,'First clip'],[b,'Private clip']]){await p.getByLabel('Своё видео').selectOption(id);await p.getByRole('button',{name:'Добавить видео',exact:true}).click();await p.getByRole('button',{name:'Удалить из плейлиста: '+title,exact:true}).waitFor()}
  await p.reload();await p.getByRole('button',{name:'Удалить из плейлиста: Private clip',exact:true}).waitFor();await layout(p,'playlist-editor');
  await v.goto(base+'/channels/'+owner.name);await v.getByRole('button',{name:'Подписаться',exact:true}).click();await v.getByRole('button',{name:'Отписаться',exact:true}).waitFor();await v.reload();await v.getByRole('button',{name:'Отписаться',exact:true}).waitFor();assert.equal(await v.getByTestId('subscribers-count').innerText(),'1');await layout(v,'channel');
  await v.goto(base+'/subscriptions');await v.getByRole('link',{name:'Канал автора — @'+owner.name,exact:true}).waitFor();await layout(v,'subscriptions');
  async function visible(ids){const response=await viewer.context.request.get(`${base}/api/playlists/${pid}`);assert.equal(response.status(),200);const body=await response.json();assert.deepEqual(body.items.map(item=>item.id),ids);assert(!('total' in body));return body}
  await visible([a]);await v.goto(`${base}/playlists/${pid}`);await v.getByRole('heading',{name:'First clip',exact:true}).click();await v.locator('video').evaluate(video=>{video.muted=true;return video.play()});await v.waitForFunction(()=>document.querySelector('video').currentTime>.3);await v.locator('video').evaluate(video=>video.pause());
  assert.equal((await owner.context.request.patch(`${base}/api/videos/${b}`,{headers:owner.headers,data:{visibility:'public'}})).status(),200);
  await p.getByRole('button',{name:'Ниже: First clip',exact:true}).click();await p.getByRole('button',{name:'Выше: Private clip',exact:true}).waitFor();
  await p.waitForFunction(()=>document.querySelector('.playlist-editor li .editor-video-title')?.textContent.startsWith('Private clip'));
  await visible([b,a]);await v.goto(`${base}/playlists/${pid}`);await v.getByRole('heading',{name:'Private clip',exact:true}).waitFor();await v.reload();await v.getByRole('heading',{name:'Private clip',exact:true}).waitFor();assert.deepEqual(await v.locator('.playlist-video-link h2').allTextContents(),['Private clip','First clip']);await layout(v,'playlist');
  assert.equal((await owner.context.request.patch(`${base}/api/videos/${b}`,{headers:owner.headers,data:{visibility:'private'}})).status(),200);await visible([a]);
  assert.equal((await owner.context.request.put(`${base}/api/videos/${b}/access/${viewer.name}`,{headers:owner.headers})).status(),204);await visible([b,a]);
  assert.equal((await viewer.context.request.delete(`${base}/api/channels/${owner.name}/subscription`,{headers:viewer.headers})).status(),200);await visible([b,a]);
  assert.equal((await owner.context.request.delete(`${base}/api/videos/${b}/access/${viewer.name}`,{headers:owner.headers})).status(),204);await visible([a]);assert.equal((await viewer.context.request.get(`${base}/api/videos/${b}/media`,{headers:{Range:'bytes=0-10'}})).status(),404);
  await v.reload();await v.getByRole('heading',{name:'First clip',exact:true}).waitFor();assert.equal(await v.getByRole('heading',{name:'Private clip',exact:true}).count(),0);
  assert.equal((await viewer.context.request.put(`${base}/api/playlists/${pid}/order`,{headers:viewer.headers,data:{video_ids:[a,b]}})).status(),404);
  console.log('OK channel edit, subscription/reload, playlist create/add/reload/reorder, actual playback and grant/revoke privacy');
  assert.deepEqual(errors,[]);console.log('OK console and layouts at 390/1440');
 }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exitCode=1});
