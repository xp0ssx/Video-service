// Uses only synthetic drafts owned by the test accounts, including cleanup.
const {chromium} = require('playwright-core');
const {randomUUID} = require('node:crypto');
const assert = require('node:assert/strict');
const base = process.env.BASE_URL || 'http://localhost:8080';
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 const created=[];
 try{
  async function account(){
   const ctx=await browser.newContext(), username='cursor_'+randomUUID().replaceAll('-','').slice(0,12);
   const response=await ctx.request.post(base+'/api/auth/register',{headers:{Origin:base},data:{username,email:username+'@example.com',password:randomUUID()}});
   assert.equal(response.status(),201);
   return {ctx,headers:{Origin:base,'X-CSRF-Token':(await response.json()).csrf_token}};
  }
  async function draft(actor){
   const response=await actor.ctx.request.post(base+'/api/videos',{headers:actor.headers,data:{title:'Synthetic Studio cursor'}});
   assert.equal(response.status(),201);
   const id=(await response.json()).id;created.push({id,actor,deleted:false});return id;
  }
  async function remove(id){
   const record=created.find(row=>row.id===id);
   assert(record);
   assert.equal((await record.actor.ctx.request.delete(`${base}/api/videos/${id}`,{headers:record.actor.headers})).status(),204);
   record.deleted=true;
  }
  const owner=await account(), own=[];
  for(let i=0;i<3;i++)own.push(await draft(owner));
  const p=await owner.ctx.newPage();
  await p.goto(base+'/studio');
  await p.getByRole('heading',{name:'Studio',exact:true}).waitFor();
  // Requests execute in the actual browser with its authenticated cookie.
  const get=path=>p.evaluate(async path=>{const response=await fetch(path);return {status:response.status,body:await response.json()}},path);
  const first=await get('/api/me/videos?limit=1');assert.equal(first.status,200);assert.equal(first.body.items.length,1);
  const anchor=first.body.next_cursor;assert(own.includes(anchor));await remove(anchor);
  const next=await get('/api/me/videos?limit=1&cursor='+anchor);
  if(process.env.EXPECT_OLD_STUDIO==='1'){
   assert.equal(next.status,404);console.log(JSON.stringify({defect:'AUD-03',cursor_after_owner_delete:404}));return;
  }
  assert.equal(next.status,200);assert.equal(next.body.items.length,1);assert.notEqual(next.body.items[0].id,anchor);
  const third=await get('/api/me/videos?limit=1&cursor='+next.body.next_cursor);
  assert.equal(third.status,200);assert.equal(third.body.items.length,1);
  assert.equal(new Set([anchor,next.body.items[0].id,third.body.items[0].id]).size,3);
  const foreign=await account(), tombstone=await draft(foreign);await remove(tombstone);
  assert.equal((await get('/api/me/videos?limit=1&cursor='+tombstone)).status,404);
  assert.equal((await get('/api/me/videos/'+tombstone)).status,404);
  console.log(JSON.stringify({result:'PASS',own_videos:3,limit:1,deleted_owner_cursor_status:next.status,foreign_tombstone_status:404}));
 }finally{
  try{
   for(const record of created)if(!record.deleted){
    const response=await record.actor.ctx.request.delete(`${base}/api/videos/${record.id}`,{headers:record.actor.headers});
    assert.equal(response.status(),204);record.deleted=true;
   }
  }finally{await browser.close();}
 }
})().catch(e=>{console.error(e.stack);process.exitCode=1});
