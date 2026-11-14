// Requires an isolated server, a source with owner descriptions and matching cached dialogue.
// Never clicks Confirm, Reject or Save: actual-media owner labels remain unchanged.
const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPPER_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const source=process.env.CLIPPER_REVIEW_SOURCE;if(!source)throw Error('Set CLIPPER_REVIEW_SOURCE');
 const browser=await chromium.launch({executablePath:process.env.CLIPPER_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
  const page=await browser.newPage(),errors=[];let annotations=0,processing=0;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()!=='POST')return;if(/\/api\/(start|manual|rerender)$/.test(r.url()))processing++;
   if(r.url().endsWith('/api/known-moments')&&['confirm','reject','edit'].includes(r.postDataJSON()?.action))annotations++});
  await page.goto(process.env.CLIPPER_UI_URL||'http://127.0.0.1:8775');
  await page.getByRole('button',{name:'New episode',exact:true}).click();
  await page.getByRole('textbox',{name:'Video file',exact:true}).fill(source);
  await page.getByRole('button',{name:'Inspect tracks',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#start').disabled&&document.querySelector('#audio').value!=='');
  await page.locator('#references summary').click();
  const loaded=page.waitForResponse(r=>r.url().endsWith('/api/known-moments')&&r.request().postDataJSON()?.action==='load');
  await page.getByRole('button',{name:'Find proposed intervals',exact:true}).click();
  const existing=(await(await loaded).json()).confirmed_count;
  await page.locator('[data-reference]').first().waitFor();
  const cards=await page.locator('[data-reference]').count();assert.equal(cards,6);
  assert((await page.locator('#reference-status').innerText()).includes(`${existing} owner-confirmed`));
  assert.equal(await page.locator('.reference-video').first().evaluate(v=>getComputedStyle(v).display),'none');
  assert.equal(await page.getByRole('button',{name:'Confirm reference',exact:true}).count(),cards);
  await page.locator('.reference-video').first().evaluate(v=>v.muted=true);
  await page.getByRole('button',{name:'Play interval proxy',exact:true}).first().click();
  await page.waitForFunction(()=>document.querySelector('.reference-video').readyState>=2,{},{timeout:60000});
  const video=page.locator('.reference-video').first();
  const metadata=await video.evaluate(async v=>{v.muted=true;await v.play();await new Promise(r=>setTimeout(r,300));v.pause();return {duration:v.duration,currentTime:v.currentTime,src:v.src}});
  assert(metadata.duration>0&&metadata.currentTime>0);
  await video.evaluate(v=>{v.currentTime=5});
  await page.waitForFunction(()=>Math.abs(document.querySelector('.reference-video').currentTime-5)<.2);
  assert.equal(annotations,0);assert.equal(processing,0);assert.deepEqual(errors,[]);
  fs.mkdirSync('output/playwright',{recursive:true});
  await page.locator('#references').scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/known-moments.png'});
  fs.writeFileSync('output/playwright/known-moments-result.json',JSON.stringify({checks:8,cards,existing,annotations,processing,errors,metadata},null,2));
  console.log('PASS: 8 known-moment browser checks; no human labels or production processing sent.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
