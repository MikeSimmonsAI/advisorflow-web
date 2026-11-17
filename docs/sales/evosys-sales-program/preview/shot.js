const {chromium}=require('playwright'); const http=require('http'),fs=require('fs'),path=require('path')
const dist='/home/claude/wt-api/frontend/dist'
const T=JSON.parse(fs.readFileSync('/home/claude/qa/evs_preview/tokens.json'))
const srv=http.createServer((q,r)=>{let f=path.join(dist,q.url.split('?')[0]);if(!fs.existsSync(f)||fs.statSync(f).isDirectory())f=path.join(dist,'index.html');
 const ext=path.extname(f);r.setHeader('content-type',{'.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.webmanifest':'application/json'}[ext]||'text/html');r.end(fs.readFileSync(f))}).listen(4174)
;(async()=>{const b=await chromium.launch();const pages=JSON.parse(process.env.PAGES);const who=process.env.WHO||'mike'
 const widths=JSON.parse(process.env.W||'[[1440,900],[390,844]]'); const errs=[]; const api=[]
 for(const [w,h] of widths){const c=await b.newContext({viewport:{width:w,height:h},deviceScaleFactor:1})
  await c.addInitScript(({tok,u})=>{localStorage.setItem('af_token',tok);localStorage.setItem('af_user',JSON.stringify(u))},{tok:T[who],u:T.mike_user})
  await c.route(/onrender\.com/,async rt=>{const u=new URL(rt.request().url());const req=rt.request()
   try{const resp=await rt.fetch({url:'http://localhost:8765'+u.pathname+u.search});api.push(resp.status()+' '+req.method()+' '+u.pathname);await rt.fulfill({response:resp,headers:{...resp.headers(),'access-control-allow-origin':'*'}})}catch(e){errs.push('route '+u.pathname+' '+e.message);await rt.abort()}})
  const p=await c.newPage(); p.on('pageerror',e=>errs.push(w+' '+e.message))
  for(const [name,pg,action] of pages){await p.goto('http://localhost:4174'+pg);await p.waitForTimeout(2200)
   if(action){try{await p.click(action,{timeout:3000});await p.waitForTimeout(1500)}catch(e){errs.push('click '+action+' '+e.message.slice(0,80))}}
   const ov=await p.evaluate(()=>document.documentElement.scrollWidth-window.innerWidth); if(ov>1) errs.push('OVERFLOW '+w+' '+name+' '+ov+'px')
   await p.screenshot({path:`/home/claude/qa/evs_preview/out/${name}_${w}.png`,fullPage:w!==390})}
  await c.close()}
 fs.writeFileSync('/home/claude/qa/evs_preview/api.log',api.join('\n'))
 console.log('errors',JSON.stringify(errs.slice(0,15)));await b.close();srv.close()})()
