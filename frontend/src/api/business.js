import {request} from './client.js';
export const loadAll=async()=>Object.fromEntries(await Promise.all(['sources','periods','policies','runs','schedules','deliveries','audits','worktime','worktime/entries'].map(async k=>[k,await request('/'+k)])));
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
export const saveWorkEntry=(data,entryId)=>entryId
  ?request('/worktime/entries/'+encodeURIComponent(entryId),'PATCH',data)
  :request('/worktime/entries','POST',data);
export const withdrawWorkEntry=(entryId,base_rev)=>request('/worktime/entries/'+encodeURIComponent(entryId)+'/withdraw','POST',{base_rev});
export const recomputeWork=()=>request('/worktime/recompute','POST',{});
export const rebuildWork=()=>request('/worktime/rebuild','POST',{});
export const loadRecomputes=()=>request('/worktime/recomputes');
