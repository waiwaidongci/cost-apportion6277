import {loadAll} from '../api/business.js';
import {request} from '../api/client.js';

export const store={session:JSON.parse(localStorage.getItem('session')||'null'),data:{sources:[],periods:[],policies:[],runs:[],schedules:[],deliveries:[],audits:[],worktime:{rows:[],totals:[]}},selected:null,workPeriod:'全部'};

export async function reload(){
  store.data=await loadAll();
  if(store.workPeriod&&store.workPeriod!=='全部'){
    store.data.worktime=await request('/worktime?period='+encodeURIComponent(store.workPeriod));
  }
}

export function setSession(value){store.session=value;if(value)localStorage.setItem('session',JSON.stringify(value));else localStorage.removeItem('session')}
