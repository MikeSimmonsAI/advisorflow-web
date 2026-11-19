const {chromium}=require('playwright'); const http=require('http'),fs=require('fs'),path=require('path')
const dist='/home/claude/wt-api/frontend/dist', OUT='/home/claude/qa/acq_preview/out'
const T=JSON.parse(fs.readFileSync('/home/claude/qa/acq_preview/tokens.json'))
const srv=http.createServer((q,r)=>{let f=path.join(dist,q.url.split('?')[0]);if(!fs.existsSync(f)||fs.statSync(f).isDirectory())f=path.join(dist,'index.html');
 const ext=path.extname(f);r.setHeader('content-type',{'.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.webmanifest':'application/json'}[ext]||'text/html');r.end(fs.readFileSync(f))}).listen(4174)
;(async()=>{const b=await chromium.launch();const pages=JSON.parse(process.env.PAGES);const who=process.env.WHO||'god'
 const widths=JSON.parse(process.env.W||'[[1440,900],[390,844]]'); const errs=[]; const api=[]
 for(const [w,h] of widths){const c=await b.newContext({viewport:{width:w,height:h},deviceScaleFactor:1,locale:process.env.LOCALE||'en-US'})
  await c.addInitScript(({tok,u,ws,client})=>{localStorage.setItem('af_token',tok);localStorage.setItem('af_user',JSON.stringify(u));
    if(ws) localStorage.setItem('af_workspace_id',ws); if(client) sessionStorage.setItem('aq_client:'+u.id,client);
    if(window.__LANG) localStorage.setItem('evs_portal_lang',window.__LANG)},
   {tok:T[who],u:T[who+'_user'],ws:who==='owner'?T.mich:null,client:process.env.CLIENT?T[process.env.CLIENT]:null})
  if(process.env.LANG2) await c.addInitScript(l=>localStorage.setItem('evs_portal_lang',l),process.env.LANG2)
  await c.route(/onrender\.com/,async rt=>{const u=new URL(rt.request().url());const req=rt.request()
   try{const resp=await rt.fetch({url:'http://localhost:8765'+u.pathname+u.search});api.push(resp.status()+' '+req.method()+' '+u.pathname);await rt.fulfill({response:resp,headers:{...resp.headers(),'access-control-allow-origin':'*'}})}catch(e){errs.push('route '+u.pathname+' '+e.message);await rt.abort()}})
  const p=await c.newPage(); p.on('pageerror',e=>errs.push(w+' '+e.message))
  for(const [name,pg] of pages){await p.goto('http://localhost:4174'+pg);await p.waitForTimeout(2500)
   const ov=await p.evaluate(()=>document.documentElement.scrollWidth-window.innerWidth); if(ov>1) errs.push('OVERFLOW '+w+' '+name+' '+ov+'px')
   await p.screenshot({path:`${OUT}/${name}_${w}.png`,fullPage:true})}
  await c.close()}
 fs.writeFileSync('/home/claude/qa/acq_preview/api.log',api.join('\n'))
 console.log('errors',JSON.stringify(errs.slice(0,15)));await b.close();srv.close()})()
