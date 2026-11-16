// Deterministic F13 regression against the production SPA; no working data is changed.
const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='http://localhost:8080';
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
  try{
    const page=await browser.newPage();const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});
    await page.route('**/api/auth/session',route=>route.fulfill({json:{user:{id:'synthetic',username:'u'.repeat(32),role:'user'},csrf_token:'synthetic-token'}}));
    await page.route('**/layout-thumbnail',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#eee"/></svg>'}));
    await page.route('**/api/videos?*',route=>{
      const q=new URL(route.request().url()).searchParams.get('q');
      return route.fulfill({json:{items:[1,2,3].map(id=>({id:String(id),title:q==='short'?'Короткий результат '+id:'Видео '+id,channel:{id:'channel',title:'Канал',username:'author'},duration_ms:2000,published_at:'2026-11-15T00:00:00Z',thumbnail_url:'/layout-thumbnail',watch_url:'/watch/'+id})),next_cursor:null}});
    });
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:1000});
      await page.goto(base+'/results?q='+'w'.repeat(200));
      await page.getByRole('button',{name:'Выйти',exact:true}).waitFor();
      await page.locator('.video-card').nth(2).waitFor();
      for(const query of ['w'.repeat(200),'short']){
        if(query==='short'){
          await page.getByRole('searchbox',{name:'Поиск видео'}).fill(query);
          await page.getByRole('button',{name:'Найти видео'}).click();
          await page.getByRole('heading',{name:'Короткий результат 1',exact:true}).waitFor();
        }
        assert.equal(await page.locator('.page-heading h1').innerText(),'Результаты: '+query);
        const measured=await page.evaluate(()=>{
          const rect=selector=>document.querySelector(selector).getBoundingClientRect();
          const overlaps=(a,b)=>a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;
          const heading=rect('.page-heading h1'),label=rect('.category-label'),main=rect('main'),sidebar=rect('.sidebar'),grid=rect('.video-grid'),account=rect('.account'),search=rect('.search-bar'),brand=rect('.brand'),header=rect('.topbar');
          const cards=[...document.querySelectorAll('.video-card')].map(node=>node.getBoundingClientRect());
          return {scrollWidth:document.documentElement.scrollWidth,width:innerWidth,contained:heading.left>=main.left&&heading.right<=main.right&&account.top>=header.top&&account.bottom<=header.bottom,noOverlaps:!overlaps(main,sidebar)&&!overlaps(brand,account)&&!overlaps(search,account)&&!(label.width&&overlaps(heading,label))&&heading.bottom<=grid.top&&cards.every((a,i)=>cards.every((b,j)=>i===j||!overlaps(a,b)))};
        });
        assert(measured.scrollWidth<=measured.width,JSON.stringify(measured));
        assert(measured.contained&&measured.noOverlaps,JSON.stringify(measured));
        if(process.env.ARTIFACT_DIR)await page.screenshot({path:path.join(process.env.ARTIFACT_DIR,`search-${query==='short'?'short':'long'}-${width}.png`),fullPage:true});
        console.log(`OK ${query==='short'?'short query':'200 w query'}, username 32, ${width}px: scrollWidth=${measured.scrollWidth}, no overlaps`);
      }
    }
    assert.deepEqual(errors,[]);console.log('OK no console errors or uncaught exceptions');
  }finally{await browser.close()}
})().catch(error=>{console.error(error.message);process.exitCode=1});
