import {readPolicy,bindPolicyRows} from './utils/policy.js';
import {request,download} from './api/client.js';
import {saveSource,savePolicy,submitRun,approveRun,saveWorkEntry,withdrawWorkEntry,recomputeWork,rebuildWork} from './api/business.js';
import {store,reload,setSession} from './stores/business.js';
import {pages,currentPage} from './router/index.js';
import {esc} from './utils/format.js';
import {action} from './hooks/action.js';
import {login} from './pages/login.js';
import {overview} from './pages/overview.js';
import {sources} from './pages/sources.js';
import {worktime} from './pages/worktime.js';
import {policies} from './pages/policies.js';
import {runs} from './pages/runs.js';
import {deliveries} from './pages/deliveries.js';
import {audit} from './pages/audit.js';

const c=await (await fetch('/project.json')).json();
const app=document.querySelector('#app');
let busy=false;
const renderers={overview,sources,worktime,policies,runs,deliveries,audit};

function render(){
 if(!store.session){
  app.innerHTML=login(c)+'<div id="notice" role="status"></div>';
  document.querySelector('#login').onsubmit=e=>{e.preventDefault();action(async()=>{
   const b=Object.fromEntries(new FormData(e.target));
   setSession(await request('/auth/login','POST',b));await reload();render()})};
  return;
 }
 const page=currentPage();
 app.innerHTML=`<div class="shell"><aside class="sidebar"><a href="#overview" class="brand">${esc(c.title.slice(0,2))}<small>业务研究底稿<br>${esc(c.slug)}</small></a><nav>${pages.map(([id,title],i)=>`<a class="${page===id?'active':''}" href="#${id}"><span>0${i+1}</span>${title}</a>`).join('')}</nav><footer>${esc(store.session.email)}<br><b>${esc(store.session.role)}</b><br><button id="logout">退出</button></footer></aside><main class="workspace"><div class="topline"><span>${esc(c.title)}</span><button id="refresh">刷新数据</button></div><div id="notice" role="status"></div>${(renderers[page]||overview)(c,store.data,store.selected,store.workPeriod||'全部')}</main></div>`;
 bind();
}

function bind(){
 bindPolicyRows();
 document.querySelector('#logout').onclick=()=>{setSession(null);render()};
 document.querySelector('#refresh').onclick=()=>action(async()=>{await reload();render()});

 const sf=document.querySelector('#source-form');
 if(sf)sf.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(sf)),id=b.id;delete b.id;
  for(const k of Object.keys(b)){if(typeof c.example[k]==='number')b[k]=Number(b[k]);else if(typeof c.example[k]==='object')b[k]=JSON.parse(b[k])}
  await saveSource(b,id);await reload();render()})};

 const pf=document.querySelector('#policy-form');
 if(pf)pf.onsubmit=e=>{e.preventDefault();action(async()=>{await savePolicy(readPolicy(pf,c));await reload();render()})};

 bindWorktime();

 const rf=document.querySelector('#run-form');
 if(rf)rf.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(rf));b.policy_id=Number(b.policy_id);
  store.selected=(await submitRun(b)).id;await reload();render()})};

 document.querySelectorAll('.select-run').forEach(b=>b.onclick=()=>{store.selected=Number(b.dataset.id);render()});

 const ex=document.querySelector('#export');
 if(ex)ex.onclick=()=>action(()=>download('/runs/'+ex.dataset.id+'/csv'));

 const af=document.querySelector('#approve');
 if(af)af.onsubmit=e=>{e.preventDefault();action(async()=>{await approveRun(af.dataset.id,new FormData(af).get('note'));await reload();render()})};

 const df=document.querySelector('#schedule-form');
 if(df)df.onsubmit=e=>{e.preventDefault();action(async()=>{
  const b=Object.fromEntries(new FormData(df));b.run_id=Number(b.run_id);b.interval_seconds=Number(b.interval_seconds);
  await request('/schedules','POST',b);await reload();render()})};

 document.querySelectorAll('.toggle').forEach(b=>b.onclick=()=>action(async()=>{await request('/schedules/'+b.dataset.id+'/toggle','POST',{});await reload();render()}));
 document.querySelectorAll('.delivery-export').forEach(b=>b.onclick=()=>action(()=>download('/deliveries/'+b.dataset.id+'/csv')));
}

function notice(text){
 const box=document.querySelector('#notice');
 if(box)box.textContent=text;
}

function bindWorktime(){
 const form=document.querySelector('#worktime-form');
 if(form){
  const entries=(store.data['worktime/entries']||{}).entries||[];
  form.onsubmit=e=>{e.preventDefault();action(async()=>{
   const b=Object.fromEntries(new FormData(form));
   const entryId=(b.entry_id||'').trim();
   const base_rev=Number(b.base_rev||0);
   if(!entryId){
    b.op_id='ui-'+crypto.randomUUID();
    await saveWorkEntry({person:b.person,department:b.department,start:b.start,end:b.end,op_id:b.op_id});
   }else{
    await saveWorkEntry({person:b.person,department:b.department,start:b.start,end:b.end,base_rev,op_id:'ui-'+crypto.randomUUID()},entryId);
   }
   await reload();render();notice('工时已记录；重算后生效')})};
  document.querySelectorAll('.worktime-select').forEach(b=>b.onclick=()=>{
   const entry=entries.find(x=>x.记录编号===b.dataset.id);if(!entry)return;
   form.querySelector('[name=entry_id]').value=entry.记录编号;
   form.querySelector('[name=person]').value=entry.人员;
   form.querySelector('[name=department]').value=entry.部门;
   form.querySelector('[name=start]').value=entry.开始;
   form.querySelector('[name=end]').value=entry.结束;
   form.querySelector('[name=base_rev]').value=entry.版本;
  });
 }
 const wp=document.querySelector('#worktime-period');
 if(wp)wp.onchange=()=>action(async()=>{
  store.workPeriod=wp.value;
  store.data.worktime=await request('/worktime?period='+encodeURIComponent(wp.value));
  render()});
 const rc=document.querySelector('#worktime-recompute');
 if(rc)rc.onclick=()=>action(async()=>{
  const r=await recomputeWork();
  await reload();render();
  notice(r.result==='noop'?'没有待投影操作':`已切换台账版本 #${r.gen}，重算 ${r.affected.length} 个人日${r.conflicts?.length?'，并发冲突 '+r.conflicts.length+' 条':''}`)});
 const rb=document.querySelector('#worktime-rebuild');
 if(rb)rb.onclick=()=>action(async()=>{
  const r=await rebuildWork();
  await reload();render();
  notice(`全量重算版本 #${r.gen}，与上一版 #${r.previous_gen} 一致：${r.consistent?'是':'否'}`)});
 document.querySelectorAll('.worktime-withdraw').forEach(b=>b.onclick=()=>action(async()=>{
  await withdrawWorkEntry(b.dataset.id,Number(b.dataset.rev));
  await reload();render();
  notice('已撤回；重算后生效')}));
}

window.addEventListener('hashchange',render);
try{if(store.session)await reload()}catch(e){setSession(null)}
render();
setInterval(async()=>{
 if(store.session&&!busy&&store.data.runs.some(r=>['queued','running'].includes(r.status))){
  busy=true;try{await reload();render()}finally{busy=false}
 }
},2000);
