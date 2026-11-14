// Requires a running local server with a saved captioned job. Uses a disposable browser context.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPPER_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 fs.mkdirSync('.runtime/ui-review',{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CLIPPER_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[];let processingRequests=0;
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|manual|rerender)$/.test(r.url()))processingRequests++});
 const base=process.env.CLIPPER_UI_URL||'http://127.0.0.1:8771';
 await page.goto(base);await page.locator('.caption-row textarea').first().waitFor();
 await page.waitForFunction(()=>document.querySelector('#jobs option')?.value&&document.querySelector('#jobs option').textContent!=='Loading saved jobs…');
 assert.equal(await page.locator('#episode-panel').getAttribute('open'),null);
 const draftTop=await page.locator('.draft').first().evaluate(e=>e.getBoundingClientRect().top);
 assert(draftTop<600,`Draft below fold: ${draftTop}`);
 const filename=await page.locator('.draft-heading').first().innerText();assert(filename.includes('Draft 1'));
 await page.locator('video').first().evaluate(async v=>{v.muted=true;await v.play();await new Promise(r=>setTimeout(r,250));v.pause()});
 await page.screenshot({path:'.runtime/ui-review/after-desktop.png',fullPage:true});
 await page.locator('.draft').first().scrollIntoViewIfNeeded();await page.screenshot({path:'.runtime/ui-review/editor-desktop.png'});
 const caption=page.getByRole('textbox',{name:'Caption text',exact:true}).first();
 await caption.fill('A local edit\nwith a second line.');await page.waitForTimeout(2100);
 assert.equal(await caption.inputValue(),'A local edit\nwith a second line.');
 await page.reload();await page.getByRole('textbox',{name:'Caption text',exact:true}).first().waitFor();
 assert.equal(await page.getByRole('textbox',{name:'Caption text',exact:true}).first().inputValue(),'A local edit with a second line.');
 await page.getByRole('button',{name:'New episode',exact:true}).click();assert(await page.locator('#source').evaluate(e=>document.activeElement===e));
 const source=(await(await context.request.get(base+'/api/status')).json()).report.source_path;
 await page.locator('#source').fill(source);await page.getByRole('button',{name:'Inspect tracks',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#audio').value==='2');
 assert.equal(processingRequests,0,'Page load, editing and file inspection must not process media');
 await context.clearCookies();await page.evaluate(()=>localStorage.clear());await page.reload();await page.locator('.caption-row textarea').first().waitFor();
 await page.waitForFunction(()=>document.querySelector('#jobs option')?.value&&document.querySelector('#jobs option').textContent!=='Loading saved jobs…');
 await page.locator('video').first().evaluate(async v=>{v.muted=true;await v.play();await new Promise(r=>setTimeout(r,250));v.pause()});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile horizontal overflow');
 await page.screenshot({path:'.runtime/ui-review/after-mobile.png',fullPage:true});
 assert.equal(await page.locator('.preview-panel').first().evaluate(e=>getComputedStyle(e).position),'static');
 await page.setViewportSize({width:1440,height:1000});assert.equal(await page.locator('.preview-panel').first().evaluate(e=>getComputedStyle(e).position),'sticky');
 assert.deepEqual(errors,[]);
 fs.writeFileSync('.runtime/ui-review/result.json',JSON.stringify({playwright:require(path.join(process.env.CLIPPER_PLAYWRIGHT_MODULE||'playwright','package.json')).version,draftTop,processingRequests,pageErrors:errors,checks:['saved-job layout','desktop screenshot','caption multiline edit','poll preservation','reload recovery','new episode focus','track inspection','idle startup','mobile overflow','responsive preview']},null,2));
 console.log('PASS: 10 Playwright UI checks; no page errors or processing requests.');await context.close();
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
