// Requires a running local server with a saved captioned job. Uses a disposable browser context.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.CLIPPER_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 fs.mkdirSync('output/playwright',{recursive:true});
 const browser=await chromium.launch({executablePath:process.env.CLIPPER_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const page=await context.newPage();const errors=[],consoleErrors=[];let processingRequests=0,newSourceImported=false;
 page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(start|manual|rerender)$/.test(r.url()))processingRequests++});
 const base=process.env.CLIPPER_UI_URL||'http://127.0.0.1:8771';
 await page.goto(base);await page.locator('.caption-row textarea').first().waitFor();
 await page.waitForFunction(()=>document.querySelector('#jobs option')?.value&&document.querySelector('#jobs option').textContent!=='Loading saved jobs…');
 assert((await page.locator('#gpu').innerText()).includes('System GPU'));
 await page.waitForFunction(()=>!document.querySelector('#episode-panel').open);
 assert.equal(await page.locator('#episode-panel').getAttribute('open'),null);
 const draftTop=await page.locator('.draft').first().evaluate(e=>e.getBoundingClientRect().top);
 assert(draftTop<600,`Draft below fold: ${draftTop}`);
 const filename=await page.locator('.draft-heading').first().innerText();assert(filename.includes('Draft 1'));
 await page.locator('video').first().evaluate(async v=>{v.muted=true;await v.play();await new Promise(r=>setTimeout(r,250));v.pause()});
 await page.screenshot({path:'output/playwright/after-desktop.png',fullPage:true});
 await page.locator('.draft').first().scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/editor-desktop.png'});
 const caption=page.getByRole('textbox',{name:'Caption text',exact:true}).first();
 await caption.fill('A local edit\nwith a second line.');await page.waitForTimeout(2100);
 assert.equal(await caption.inputValue(),'A local edit\nwith a second line.');
 await page.reload();await page.getByRole('textbox',{name:'Caption text',exact:true}).first().waitFor();
 assert.equal(await page.getByRole('textbox',{name:'Caption text',exact:true}).first().inputValue(),'A local edit with a second line.');
 await page.getByRole('button',{name:'New recording',exact:true}).click();assert(await page.locator('#source').evaluate(e=>document.activeElement===e));
 assert.equal(await page.locator('#source-profile').inputValue(),'general');
 assert.equal(await page.locator('#source-profile option').count(),5);
 await page.locator('#source-profile').selectOption('broadcast');
 await page.waitForTimeout(1800);
 assert.equal(await page.locator('#source-profile').inputValue(),'broadcast','Polling must preserve profile choice');
 assert((await page.locator('#profile-help').innerText()).includes('preserve the full picture'));
 const source=(await(await context.request.get(base+'/api/status')).json()).report.source_path;
 const fileChooserPromise=page.waitForEvent('filechooser');
 await page.getByRole('button',{name:'Choose file…',exact:true}).click();
 const fileChooser=await fileChooserPromise;await fileChooser.setFiles(source);
 await page.waitForFunction(source=>document.querySelector('#source').value===source&&!document.querySelector('#start').disabled,source,{timeout:60000});
 const metadata=await(await context.request.get(base+'/api/probe?source='+encodeURIComponent(source))).json();
 const english=metadata.audio.filter(t=>(t.language||'').toLowerCase().startsWith('en'));
 if(english.length===1)assert.equal(await page.locator('#audio').inputValue(),String(english[0].index));
 else if(metadata.audio.length>1)assert.equal(await page.locator('#audio').inputValue(),'');
 const prior=await page.locator('#source').inputValue();
 const cancelPromise=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Choose file…',exact:true}).click();await(await cancelPromise).setFiles([]);
 assert.equal(await page.locator('#source').inputValue(),prior,'Cancelled chooser changed the selected source');
 const invalid=path.resolve('output/playwright/not-a-video.txt');fs.writeFileSync(invalid,'not video');
 const invalidPromise=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Choose file…',exact:true}).click();await(await invalidPromise).setFiles(invalid);
 await page.waitForFunction(()=>document.querySelector('#meta').textContent.includes('Choose a non-empty supported video'));
 assert(await page.locator('#start').isDisabled());
 if(process.env.CLIPPER_NEW_SOURCE){
  const newPromise=page.waitForEvent('filechooser');await page.getByRole('button',{name:'Choose file…',exact:true}).click();await(await newPromise).setFiles(process.env.CLIPPER_NEW_SOURCE);
  await page.waitForFunction(()=>document.querySelector('#source').value.includes('Imports')&&!document.querySelector('#start').disabled,{},{timeout:30000});newSourceImported=true;
 }

 assert.equal(processingRequests,0,'Page load, editing and file inspection must not process media');
 await context.clearCookies();await page.evaluate(()=>localStorage.clear());await page.reload();await page.locator('.caption-row textarea').first().waitFor();
 await page.waitForFunction(()=>document.querySelector('#jobs option')?.value&&document.querySelector('#jobs option').textContent!=='Loading saved jobs…');
 await page.locator('video').first().evaluate(async v=>{v.muted=true;await v.play();await new Promise(r=>setTimeout(r,250));v.pause()});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile horizontal overflow');
 await page.screenshot({path:'output/playwright/after-mobile.png',fullPage:true});
 assert.equal(await page.locator('.preview-panel').first().evaluate(e=>getComputedStyle(e).position),'static');
 await page.setViewportSize({width:1440,height:1000});assert.equal(await page.locator('.preview-panel').first().evaluate(e=>getComputedStyle(e).position),'sticky');
 assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
 const checks=['source profile choices and idle polling','saved-job layout','desktop screenshot','caption multiline edit','poll preservation','reload recovery','new episode focus','browser file chooser','cancelled chooser','unsupported file rejection','track inspection','idle startup','mobile overflow','responsive preview','system GPU telemetry'];if(newSourceImported)checks.push('new local import');
 fs.writeFileSync('output/playwright/result.json',JSON.stringify({playwright:require(path.join(process.env.CLIPPER_PLAYWRIGHT_MODULE||'playwright','package.json')).version,draftTop,processingRequests,pageErrors:errors,consoleErrors,checks},null,2));
 console.log(`PASS: ${checks.length} Playwright UI checks; no page/console errors or processing requests.`);await context.close();
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
