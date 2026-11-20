import {heading} from '../components/common/header.js';import {table} from '../components/common/table.js';import {esc} from '../utils/format.js';
const entryTable=(entries)=>entries.length?'<div class="table-wrap"><table><thead><tr><th>记录编号</th><th>人员</th><th>部门</th><th>开始</th><th>结束</th><th>版本</th><th>状态</th><th>操作</th></tr></thead><tbody>'+entries.map(e=>`<tr><td>${esc(e.记录编号)}</td><td>${esc(e.人员)}</td><td>${esc(e.部门)}</td><td>${esc(e.开始)}</td><td>${esc(e.结束)}</td><td>${esc(e.版本)}</td><td>${esc(e.状态)}</td><td><button type="button" class="toggle worktime-select" data-id="${esc(e.记录编号)}">编辑</button>${e.状态==='有效'?`<button type="button" class="toggle worktime-withdraw" data-id="${esc(e.记录编号)}" data-rev="${esc(e.版本)}">撤回</button>`:''}</td></tr>`).join('')+'</tbody></table></div>':'<div class="empty">尚无工时条目。</div>';
export function worktime(c,d,selected,period='全部'){
  const w=d.worktime||{rows:[],totals:[],gen:0,pending_ops:0},entries=(d['worktime/entries']||{entries:[],conflicts:[]});
  const periods=['全部',...d.periods.map(p=>p.name)];
  return heading('WORKTIME LEDGER / 03','工时台账与分摊依据','同一人同日的重叠工时只计一次，跨日按自然日拆分；同时报给多个部门时该瞬时均分。补录、更正、撤回只重算受影响的人日，整批校验通过后才切换生效版本。')
  +`<div class="metrics"><div><small>生效台账版本</small><strong>#${w.gen||0}</strong></div><div><small>人日单元</small><strong>${(w.rows||[]).length}</strong></div><div><small>待投影操作</small><strong>${w.pending_ops||0}</strong></div><div><small>合计工时</small><strong>${esc(w.hours_total||'0')} h</strong></div></div>`
  +`<details open><summary>补录或更正起止工时</summary><form id="worktime-form"><div class="form-grid"><label>记录编号（留空新增，点下方“编辑”自动带入）<input name="entry_id" placeholder="留空即新增"></label><label>人员<input name="person" required></label><label>部门<input name="department" required></label><label>开始（YYYY-MM-DDTHH:MM）<input name="start" placeholder="2026-11-05T23:00" required></label><label>结束<input name="end" placeholder="2026-11-06T02:30" required></label><label>基版本 base_rev<input name="base_rev" type="number" min="0" value="0"></label></div><button>记录工时</button></form><p>并发更正按字段合并，互不覆盖；同一字段并发时以服务端顺序确定性取胜并在报告中提示。</p></details>`
  +`<div class="inline-form"><label>统计期间<select id="worktime-period">${periods.map(p=>`<option ${p===period?'selected':''}>${esc(p)}</option>`).join('')}</select></label><button id="worktime-recompute">重算积压操作</button><button id="worktime-rebuild">全量重算并核对</button><p id="worktime-report"></p></div>`
  +`<h2>部门权重（由台账人日秒数推导 · ${esc(period)}）</h2>${table(w.totals||[])}`
  +`<h2>人日台账 · 版本 #${w.gen||0}</h2>${table((w.rows||[]).map(r=>({人员:r.人员,日期:r.日期,部门:r.部门,小时:r.小时,秒数分数:r.秒数分数})))}`
  +`<h2>起止工时条目</h2>${entryTable(entries.entries||[])}`
  +(entries.conflicts?.length?`<h3>并发更正提示</h3>${table(entries.conflicts)}`:'');
}
