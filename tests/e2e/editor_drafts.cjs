// Real API fixtures; controlled HTTP gates reproduce pending writes and stale reads.
const {chromium}=require('playwright-core');
const {randomUUID}=require('node:crypto');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='http://localhost:8080';
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
async function waitGate(gate,label){
 let timer;
 try{return await Promise.race([gate.promise,new Promise((_,reject)=>timer=setTimeout(()=>reject(new Error('Timed out: '+label)),30000))])}
 finally{clearTimeout(timer)}
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  async function register(){
   const context=await browser.newContext(),name='draft_'+randomUUID().replaceAll('-','').slice(0,12);
   let response;
   // Respect production registration rate limiting; this wait is not a race-test delay.
   for(let attempt=0;attempt<5;attempt++){
    response=await context.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username:name,email:name+'@example.com',password:randomUUID()}});
    if(response.status()!==429)break;
    await new Promise(resolve=>setTimeout(resolve,21000));
   }
   assert.equal(response.status(),201);
   const session=await response.json();
   return {context,name,headers:{Origin:base,'X-CSRF-Token':session.csrf_token}};
  }
  const {context,name,headers}=await register(),otherUser=await register();
  async function playlist(title){
   const response=await context.request.post(base+'/api/playlists',{headers,data:{title,description:'Saved description'}});
   assert.equal(response.status(),201);return (await response.json()).id;
  }
  const id=await playlist('Saved playlist'),otherId=await playlist('Other playlist');
  const page=await context.newPage(),other=await context.newPage(),errors=[],expected503=new Set();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error'&&!(expected503.has(m.location().url)&&/503/.test(m.text())))errors.push(m.text())});
  await other.goto('about:blank');
  for(const width of [390,1440]){
   await page.setViewportSize({width,height:1000});
   for(const kind of ['channel','playlist']){
    const patch=kind==='channel'?'/api/me/channel':`/api/playlists/${id}`;
    const get=kind==='channel'?'/api/channels/'+name:`/api/me/playlists/${id}`;
    const editor=kind==='channel'?'/channels/'+name:`/studio/playlists/${id}`;
    const title=page.getByLabel('Название',{exact:true}),description=page.getByLabel('Описание');
    const save=page.getByRole('button',{name:kind==='channel'?'Сохранить канал':'Сохранить плейлист',exact:true});
    async function open(){
     if(kind==='channel'&&!await title.isVisible())await page.getByText('Редактировать канал',{exact:true}).click();
     await title.waitFor();
    }
    async function fields(value,body){
     await page.waitForFunction(({value,body})=>document.querySelector('input[name=title]')?.value===value&&document.querySelector('textarea[name=description]')?.value===body,{value,body});
    }
    async function idle(){await page.waitForFunction(()=>!document.querySelector('.metadata-form button').disabled)}
    async function fill(value,body){await title.fill(value);await description.fill(body)}
    async function persisted(value,body){
     const response=await context.request.get(base+get);assert.equal(response.status(),200);
     const data=await response.json();assert.equal(data.title,value);assert.equal(data.description,body);
    }
    async function saveAndRefetch(){
     const refreshed=page.waitForResponse(r=>r.url()===base+get&&r.request().method()==='GET');
     await save.click();assert.equal((await refreshed).status(),200);await idle();
    }
    async function spa(url){await page.evaluate(url=>{history.pushState(null,'',url);window.dispatchEvent(new PopStateEvent('popstate'))},url)}
    await page.goto(base+editor);await open();

    // Existing behavior: a real focus GET changes saved metadata without replacing the draft/DOM.
    await fill('Unsaved title '+width,'Unsaved description '+width);
    const original=await title.elementHandle(),external='External saved '+kind+' '+width;
    assert.equal((await context.request.patch(base+patch,{headers,data:{title:external,description:'External description'}})).status(),kind==='channel'?200:204);
    const refetched=page.waitForResponse(r=>r.url()===base+get&&r.request().method()==='GET');
    await other.bringToFront();await page.bringToFront();
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await (await refetched).finished();
    assert(await original.evaluate(node=>node.isConnected),'Refetch remounted form');
    await fields('Unsaved title '+width,'Unsaved description '+width);
    await page.getByRole('button',{name:'Отменить изменения',exact:true}).click();
    await fields(external,'External description');

    // Failed PATCH must keep both the draft and the previously confirmed resource.
    await fill('Preserve after failure','Preserve description');expected503.add(base+patch);
    await page.route(base+patch,route=>route.request().method()==='PATCH'?route.fulfill({status:503,json:{message:'Synthetic save failure'}}):route.continue());
    await save.click();await page.getByRole('alert').filter({hasText:'Synthetic save failure'}).waitFor();await idle();
    await fields('Preserve after failure','Preserve description');await persisted(external,'External description');
    if(kind==='channel')assert.equal(await page.locator('h1').innerText(),external);
    await page.unroute(base+patch);

    // A: hold the actual PATCH until B has been typed; a second submit must be ignored.
    const a=`Snapshot A ${kind} ${width}`,b=`New draft B ${kind} ${width}`;
    const arrived=deferred(),release=deferred(),bodies=[];
    await page.route(base+patch,async route=>{
     if(route.request().method()!=='PATCH')return route.continue();
     bodies.push(route.request().postDataJSON());arrived.resolve();await release.promise;
     await route.continue();
    });
    await fill(a,'Description A');await save.click();await waitGate(arrived,'PATCH intercepted');
    assert(await save.isDisabled());await fill(b,'Description B');
    await page.locator('.metadata-form').evaluate(form=>{form.requestSubmit();form.requestSubmit()});
    const savedA=page.waitForResponse(r=>r.url()===base+get&&r.request().method()==='GET');
    release.resolve();assert.equal((await savedA).status(),200);await idle();
    assert.deepEqual(bodies,[{title:a,description:'Description A'}],'Duplicate PATCH or wrong snapshot');
    await fields(b,'Description B');await persisted(a,'Description A');
    if(kind==='channel')assert.equal(await page.locator('h1').innerText(),a);
    const savedB=page.waitForRequest(r=>r.url()===base+patch&&r.method()==='PATCH');
    await saveAndRefetch();assert.deepEqual((await savedB).postDataJSON(),{title:b,description:'Description B'});
    await page.unroute(base+patch);await persisted(b,'Description B');
    await page.reload();await open();await fields(b,'Description B');
    console.log(`OK A ${kind} ${width}: pending PATCH preserves B, duplicate submit blocked, repeat saves B, real reload`);

    // B: capture an old GET before PATCH, fail exactly the following resource refetch.
    // The stale response is released only after the confirmed PATCH and the failed new GET.
    const captured=deferred(),releaseOld=deferred(),oldFinished=deferred();let gets=0;
    expected503.add(base+get);
    await page.route(base+get,async route=>{
     if(route.request().method()!=='GET')return route.continue();
     gets++;
     if(gets===1){
      const response=await route.fetch();assert.equal(response.status(),200);captured.resolve();
      await releaseOld.promise;await route.fulfill({response});oldFinished.resolve();return;
     }
     if(gets===2)return route.fulfill({status:503,json:{message:'Synthetic refetch failure'}});
     return route.continue();
    });
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await waitGate(captured,'old GET captured');
    const c=`Confirmed C ${kind} ${width}`;
    await fill('  '+c+'  ','  Confirmed description C  ');
    const failedGet=page.waitForResponse(r=>r.url()===base+get&&r.status()===503);
    const successfulPatch=page.waitForResponse(r=>r.url()===base+patch&&r.request().method()==='PATCH');
    await save.click();const patchResponse=await successfulPatch;assert.equal(patchResponse.status(),kind==='channel'?200:204);
    assert.deepEqual(patchResponse.request().postDataJSON(),{title:c,description:'Confirmed description C'});
    await failedGet;await page.getByRole('alert').filter({hasText:'Synthetic refetch failure'}).waitFor();await idle();
    await fields(c,'Confirmed description C');await persisted(c,'Confirmed description C');
    if(kind==='channel')assert.equal(await page.locator('h1').innerText(),c);
    releaseOld.resolve();await waitGate(oldFinished,'old GET released');
    // Drain browser rendering after the released old response; no fixed sleep.
    await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
    await fields(c,'Confirmed description C');
    if(kind==='channel')assert.equal(await page.locator('h1').innerText(),c);
    await page.unroute(base+get);
    const recovered=page.waitForResponse(r=>r.url()===base+get&&r.status()===200);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await recovered;
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('[role=alert]')).some(node=>node.textContent.includes('Synthetic refetch failure')));
    await fields(c,'Confirmed description C');
    await fill('Cancel this change','Cancel description');await page.getByRole('button',{name:'Отменить изменения',exact:true}).click();await fields(c,'Confirmed description C');
    console.log(`OK B ${kind} ${width}: PATCH confirmed locally despite next GET 503, stale GET ignored, API recovery and cancel`);

    // Reopen, public presentation and SPA object switching must agree with confirmed metadata.
    if(kind==='channel'){
     await page.getByText('Редактировать канал',{exact:true}).click();await open();await fields(c,'Confirmed description C');
    }else{
     await page.getByRole('link',{name:'Открыть плейлист',exact:true}).click();
     await page.getByRole('heading',{name:c,exact:true}).waitFor();
     assert.equal(await page.locator('.video-description').innerText(),'Confirmed description C');
     await page.getByRole('link',{name:'Редактировать плейлист',exact:true}).click();await open();await fields(c,'Confirmed description C');
    }
    await fill('Must not follow object switch','Must not follow description');
    await spa(kind==='channel'?'/channels/'+otherUser.name:`/studio/playlists/${otherId}`);
    if(kind==='channel'){
     await page.getByRole('heading',{name:otherUser.name,exact:true}).waitFor();assert.equal(await title.count(),0);
    }else{await fields('Other playlist','Saved description')}
    await spa(editor);await open();await fields(c,'Confirmed description C');
    await page.reload();await open();await fields(c,'Confirmed description C');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(process.env.ARTIFACT_DIR)await page.screenshot({path:path.join(process.env.ARTIFACT_DIR,`draft-${kind}-${width}.png`),fullPage:true});
    console.log(`OK C ${kind} ${width}: focus/refetch, failed PATCH, cancel, reopen, SPA object switch, public metadata, layout`);
   }
  }
  assert.deepEqual(errors,[]);console.log('OK no unexpected console errors/pageerror');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
