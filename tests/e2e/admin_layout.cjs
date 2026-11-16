// Real API F13 regression. Uses a CLI-created test admin; credentials stay outside Git.
// Requires MODERATION_ACCOUNTS_FILE, playwright-core, Chromium and a running web.
const {chromium}=require('playwright-core');
const {readFileSync}=require('node:fs');
const {randomUUID}=require('node:crypto');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='http://localhost:8080';

(async()=>{
  const credentials=JSON.parse(readFileSync(process.env.MODERATION_ACCOUNTS_FILE,'utf8'));
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
  const context=await browser.newContext({viewport:{width:390,height:1000}});
  const errors=[],originals=[];
  let headers;
  try{
    const login=await context.request.post(base+'/api/auth/login',{headers:{Origin:base},data:{email:credentials.email,password:credentials.password}});
    assert.equal(login.status(),200,'Test admin login');
    const session=await login.json();
    assert.equal(session.user.role,'admin');
    headers={Origin:base,'X-CSRF-Token':session.csrf_token};
    const names=['w'.repeat(32),'s_'+randomUUID().replaceAll('-','').slice(0,6)];
    for(const username of names){
      const lookup=await context.request.get(base+'/api/admin/users',{params:{username}});
      assert.equal(lookup.status(),200);
      let [user]=await lookup.json();
      if(!user){
        const fresh=await browser.newContext();
        try{
          const registration=await fresh.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username,email:'layout_'+randomUUID()+'@example.com',password:randomUUID()}});
          assert.equal(registration.status(),201,'Register valid layout fixture');
          user=(await registration.json()).user;
        }finally{await fresh.close()}
      }
      assert(['user','moderator'].includes(user.role),'Fixture must not be an admin');
      originals.push(user);
      if(user.role!=='user'){
        const reset=await context.request.put(`${base}/api/admin/users/${user.id}/role`,{headers,data:{role:'user',reason:'Synthetic layout baseline'}});
        assert.equal(reset.status(),200);
      }
    }
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});
    page.on('dialog',dialog=>dialog.accept());
    await page.goto(base+'/admin');
    await page.getByRole('heading',{name:'Администрирование',exact:true}).waitFor();
    const role=page.getByTestId('user-role');
    const card=page.locator('article').filter({has:role});

    async function layout(username,width,currentRole){
      assert.equal(await role.innerText(),currentRole);
      const button=card.getByRole('button',{name:currentRole==='moderator'?'Снять модератора':'Назначить модератором',exact:true});
      assert(await button.isVisible());
      assert(await button.isEnabled());
      const measured=await card.evaluate((article,{username})=>{
        const p=article.querySelector('p'),text=p.firstChild;
        const glyphs=Array.from(username,(_,i)=>{
          const range=document.createRange();range.setStart(text,i);range.setEnd(text,i+1);
          return range.getBoundingClientRect();
        });
        const rect=node=>node.getBoundingClientRect();
        const overlaps=(a,b)=>a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;
        const contains=(a,b)=>b.left>=a.left-.5&&b.right<=a.right+.5&&b.top>=a.top-.5&&b.bottom<=a.bottom+.5;
        const main=rect(document.querySelector('main')),box=rect(article),identity=rect(p);
        const role=rect(article.querySelector('[data-testid=user-role]')),button=rect(article.querySelector('button'));
        const fields=[...document.querySelectorAll('.moderation-page input,.moderation-page textarea,.moderation-page button')].map(rect);
        return {
          width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
          complete:text.textContent===username&&glyphs.every(g=>g.width>0&&g.height>0&&contains(identity,g)&&contains(box,g)&&contains(main,g)),
          lines:new Set(glyphs.map(g=>Math.round(g.top))).size,
          contained:contains(main,box)&&contains(box,role)&&contains(box,button)&&fields.every(f=>contains(main,f)),
          noOverlaps:!overlaps(role,button)&&glyphs.every(g=>!overlaps(g,role)&&!overlaps(g,button))&&fields.every((a,i)=>fields.every((b,j)=>i===j||!overlaps(a,b)))&&fields.every(f=>glyphs.every(g=>!overlaps(f,g))&&!overlaps(f,role)),
        };
      },{username});
      assert(measured.scrollWidth<=measured.width,JSON.stringify(measured));
      assert(measured.complete&&measured.contained&&measured.noOverlaps,JSON.stringify(measured));
      if(username.length===32&&width===390)assert(measured.lines>1,JSON.stringify(measured));
      else assert.equal(measured.lines,1,JSON.stringify(measured));
      if(process.env.ARTIFACT_DIR)await page.screenshot({path:path.join(process.env.ARTIFACT_DIR,`admin-${username.length===32?'long':'short'}-${currentRole}-${width}.png`),fullPage:true});
      console.log(`OK admin ${username.length===32?'32 w':'short'} ${currentRole} ${width}px: scrollWidth=${measured.scrollWidth}, lines=${measured.lines}, complete, no overlaps`);
    }

    for(const width of [390,1440]){
      await page.setViewportSize({width,height:1000});
      for(const username of names){
        await page.getByLabel('Username пользователя').fill(username);
        const found=page.waitForResponse(response=>response.url().includes('/api/admin/users?')&&response.request().method()==='GET');
        await page.getByRole('button',{name:'Найти пользователя',exact:true}).click();
        assert.equal((await found).status(),200);
        await role.waitFor();
        await page.getByLabel('Причина изменения роли').fill('Synthetic F13 layout regression');
        await layout(username,width,'user');
        for(const nextRole of ['moderator','user']){
          const changed=page.waitForResponse(response=>response.url().endsWith('/role')&&response.request().method()==='PUT');
          await card.getByRole('button',{name:nextRole==='moderator'?'Назначить модератором':'Снять модератора',exact:true}).focus();
          await page.keyboard.press('Enter');
          const response=await changed;
          assert.equal(response.status(),200);
          assert.equal((await response.json()).role,nextRole);
          await card.getByRole('button',{name:nextRole==='moderator'?'Снять модератора':'Назначить модератором',exact:true}).waitFor();
          await layout(username,width,nextRole);
        }
      }
    }
    assert.deepEqual(errors,[]);
    console.log('OK real API assignment/revocation, keyboard, no console errors or pageerror');
  }finally{
    try{
      // Preserve fixture roles even after an assertion fails. No accounts/data are deleted.
      for(const user of originals){
        const response=await context.request.put(`${base}/api/admin/users/${user.id}/role`,{headers,data:{role:user.role,reason:'Restore synthetic layout fixture role'}});
        assert.equal(response.status(),200,'Restore fixture role');
      }
    }finally{await browser.close()}
  }
})().catch(error=>{console.error(error.message);process.exitCode=1});
