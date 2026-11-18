// Deterministic response-order and pagination regressions without external libraries.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const base='http://localhost:8080';
const json=(status,body)=>({status,contentType:'application/json',body:JSON.stringify(body)});
const card=(id,title)=>({id,title,channel:{id:'channel',title:'Канал',username:'author'},duration_ms:2000,published_at:'2026-11-15T00:00:00Z',thumbnail_url:'/mock-thumbnail',watch_url:'/watch/'+id});
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    const page=await browser.newPage();const pending=[];let ready;const started=new Promise(resolve=>ready=resolve);let errors=0;
    await page.route('**/api/auth/session',route=>route.fulfill(json(401,{message:'Требуется вход'})));
    await page.route('**/mock-thumbnail',route=>route.fulfill({status:404,body:''}));
    await page.route('**/api/videos?*',route=>{
      const query=new URL(route.request().url()).searchParams;
      const q=query.get('q');
      if(q==='alpha'){pending.push(route);ready();return}
      if(q==='missing')return route.fulfill(json(200,{items:[],next_cursor:null}));
      if(q==='error'&&errors++===0)return route.fulfill(json(503,{message:'Синтетическая ошибка'}));
      if(q==='pages')return route.fulfill(json(200,query.get('cursor')?{items:[card('one','Первый'),card('two','Второй')],next_cursor:null}:{items:[card('one','Первый')],next_cursor:'second'}));
      return route.fulfill(json(200,{items:[card('beta','Новый результат')],next_cursor:null}));
    });
    await page.goto(base+'/results?q=alpha');await started;
    if (!await page.getByRole('searchbox',{name:'Поиск видео'}).isVisible()) await page.getByRole('button',{name:'Открыть поиск',exact:true}).click();
    await page.getByRole('searchbox',{name:'Поиск видео'}).fill('beta');await page.getByRole('button',{name:'Найти видео'}).click();
    await page.getByRole('link',{name:'Смотреть: Новый результат',exact:true}).waitFor();
    for(const route of pending)await route.fulfill(json(200,{items:[card('alpha','Устаревший результат')],next_cursor:null}));
    await page.waitForTimeout(100);
    assert.equal(await page.getByRole('link',{name:'Смотреть: Устаревший результат',exact:true}).count(),0);
    assert.equal(await page.getByRole('link',{name:'Смотреть: Новый результат',exact:true}).count(),1);
    console.log('OK intentionally delayed search response cannot overwrite newer query');
    await page.goto(base+'/results?q=missing');await page.getByRole('heading',{name:'Ничего не найдено'}).waitFor();
    await page.goto(base+'/results?q=error');await page.getByRole('alert').waitFor();await page.getByRole('button',{name:'Повторить',exact:true}).click();await page.getByRole('link',{name:'Смотреть: Новый результат',exact:true}).waitFor();
    await page.goto(base+'/results?q=pages');await page.getByRole('button',{name:'Ещё видео'}).click();await page.getByRole('link',{name:'Смотреть: Второй',exact:true}).waitFor();assert.equal(await page.locator('.video-card').count(),2);
    console.log('OK search empty/error/retry states and pagination deduplication');
    await page.close();
    const comments=await browser.newPage();const reads=[];
    const session={user:{id:'owner',username:'u'.repeat(32),role:'user'},csrf_token:'synthetic-token'};
    const videoId='00000000-0000-0000-0000-000000000001';
    const comment=(id,body,time)=>({id,body,created_at:`2026-11-15T00:00:0${time}Z`,author:{id:'owner',username:'tester'}});
    const newer=comment('new','Новый комментарий',2);
    await comments.route('**/api/auth/session',route=>route.fulfill(json(200,session)));
    await comments.route(`**/api/videos/${videoId}`,route=>route.fulfill(json(200,{id:videoId,title:'Test',description:'',author:'tester',likes_count:0,liked_by_me:false})));
    await comments.route(`**/api/videos/${videoId}/comments**`,route=>{
      if(route.request().method()==='POST')return route.fulfill(json(201,newer));
      if(new URL(route.request().url()).searchParams.get('cursor'))return route.fulfill(json(200,{items:[comment('mid','Средний комментарий',1),newer],next_cursor:null}));
      reads.push(route);
    });
    await comments.route(`**/api/videos/${videoId}/like`,route=>route.fulfill(json(403,{message:'Недопустимый CSRF-токен'})));
    await comments.goto(base+'/watch/'+videoId);
    await comments.getByRole('button',{name:'Аккаунт',exact:true}).waitFor();
    for(const width of [390,1440]){
      await comments.setViewportSize({width,height:1000});
      assert(await comments.evaluate(()=>{
        const account=document.querySelector('.account').getBoundingClientRect();
        const header=document.querySelector('.topbar').getBoundingClientRect();
        const search=document.querySelector('.search-bar').getBoundingClientRect();
        return document.documentElement.scrollWidth<=innerWidth&&account.top>=header.top&&account.bottom<=header.bottom&&(innerWidth>600||search.width===0);
      }),`Account with maximum username length overflows or overlaps search at ${width}px`);
    }
    console.log('OK maximum-length username fits header at 390/1440 without overlapping search');
    await comments.getByLabel('Ваш комментарий').fill('Новый комментарий');
    await comments.getByRole('button',{name:'Отправить комментарий',exact:true}).click();await comments.getByText('Комментарий добавлен',{exact:true}).waitFor();
    for(const route of reads)await route.fulfill(json(200,{items:[comment('old','Старый комментарий',0)],next_cursor:'second'}));
    await comments.getByRole('button',{name:'Ещё комментарии'}).waitFor();
    assert.deepEqual(await comments.locator('.comment-body').allTextContents(),['Старый комментарий','Новый комментарий']);
    await comments.getByRole('button',{name:'Ещё комментарии'}).click();
    await comments.getByText('Средний комментарий',{exact:true}).waitFor();
    assert.deepEqual(await comments.locator('.comment-body').allTextContents(),['Старый комментарий','Средний комментарий','Новый комментарий']);
    await comments.getByRole('button',{name:'Нравится',exact:true}).click();await comments.getByRole('alert').filter({hasText:'CSRF'}).waitFor();
    assert.equal(await comments.getByRole('button',{name:'Нравится',exact:true}).getAttribute('aria-pressed'),'false');
    assert.equal(await comments.getByRole('button',{name:'Аккаунт',exact:true}).count(),1);
    console.log('OK late comment list preserves new post, chronological merging, pagination and 403 state');
  }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exit(1)});
