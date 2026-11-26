import {RequestError, decodeResponse, runStatus, revisionFrom} from './business-client.js';

const root=()=>document.getElementById('business-root');
const feedback=document.getElementById('feedback');
const drafts=new Map(), keys=new Map(), controllers=new Set();
let pending=false, stale=false, generation=0, timer=null, pollDelay=2000, pollingRun=null;
let lastFocus=null, pdfController=null;
function notify(message,error=false) {feedback.textContent=message;feedback.classList.toggle('error',error);feedback.hidden=false;}
function saveDrafts() {
  root().querySelectorAll('form[data-draft]').forEach(form=>{
    const fields={};form.querySelectorAll('textarea,select').forEach(n=>{fields[n.name]=n.value;});
    drafts.set(form.dataset.draft,fields);
  });
}
function restoreDrafts() {
  root().querySelectorAll('form[data-draft]').forEach(form=>{
    const fields=drafts.get(form.dataset.draft);if(!fields)return;
    form.querySelectorAll('textarea,select').forEach(n=>{if(Object.hasOwn(fields,n.name))n.value=fields[n.name];});
  });
}
function disableWrites() {root().querySelectorAll('form[data-operation] button[type=submit]').forEach(n=>{n.disabled=true;});}
function initTabs(selected='metrics') {
  const tabs=[...root().querySelectorAll('[role=tab]')];
  if(!tabs.length)return;
  tabs.forEach(t=>{const active=t.dataset.panel===selected;t.setAttribute('aria-selected',String(active));t.tabIndex=active?0:-1;});
  root().querySelectorAll('[role=tabpanel]').forEach(p=>{p.hidden=p.id!==selected;});
}
function sameTaskURL(task) {const url=new URL('/',location.origin);url.searchParams.set('task_id',task);return url;}
async function request(url,options={}) {
  const controller=new AbortController();controllers.add(controller);
  const timeout=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch(url,{...options,credentials:'same-origin',signal:controller.signal,redirect:'error',headers:{Accept:'application/json',...options.headers}});
    if(!response.headers.get('content-type')?.includes('application/json'))throw new RequestError('服务器未返回预期数据；请刷新核对实际提交结果。','INVALID_RESPONSE',true);
    const payload=await response.json();return {data:decodeResponse(response.status,payload),payload};
  }catch(error){
    if(error instanceof RequestError)throw error;
    throw new RequestError('连接中断或超时，提交是否生效尚未确定。请先刷新核对，不要重复操作。','NETWORK_ERROR',true);
  }finally{clearTimeout(timeout);controllers.delete(controller);}
}
async function refresh({preserve=true, expected=null}={}) {
  if(!root().dataset.taskId)return;
  const task=root().dataset.taskId,version=Number(root().dataset.revision),epoch=++generation;
  const selected=root().querySelector('[role=tab][aria-selected=true]')?.dataset.panel;
  if(preserve)saveDrafts();
  const controller=new AbortController();controllers.add(controller);const timeout=setTimeout(()=>controller.abort(),20000);
  try{
    const response=await fetch(sameTaskURL(task),{credentials:'same-origin',cache:'no-store',signal:controller.signal,redirect:'error'});
    if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))throw new Error('读取失败');
    const parsed=new DOMParser().parseFromString(await response.text(),'text/html');
    const next=parsed.getElementById('business-root'),revision=Number(next?.dataset.revision);
    if(!next||next.dataset.taskId!==task||!Number.isSafeInteger(revision)||revision<version||(expected!==null&&revision<expected))throw new Error('快照不一致');
    if(epoch!==generation||root().dataset.taskId!==task)return false;
    root().replaceWith(document.importNode(next,true));initTabs(selected);if(preserve)restoreDrafts();stale=false;
    return true;
  }catch(error){stale=true;disableWrites();throw new RequestError('无法取得当前任务快照。草稿已保留，请点击“刷新任务”重试；不回退到演示数据。','SNAPSHOT_UNAVAILABLE');}
  finally{clearTimeout(timeout);controllers.delete(controller);}
}
function clearPoll(){clearTimeout(timer);timer=null;}
async function poll(runId, task, epoch) {
  clearPoll();if(epoch!==generation||root().dataset.taskId!==task)return;
  try{
    const {data}=await request(`/tasks/${encodeURIComponent(task)}/agent-runs/${encodeURIComponent(runId)}`);
    if(epoch!==generation||root().dataset.taskId!==task)return;
    const status=runStatus(data.status);
    const target=root().querySelector('.run-card .status');if(target)target.textContent=status.label;
    if(status.active){pollDelay=Math.min(pollDelay*1.3,8000);timer=setTimeout(()=>poll(runId,task,epoch),pollDelay);return;}
    pollingRun=null;const ok=await refresh();if(ok)notify(data.status==='waiting_review'?'运行暂停，待人工复核；请检查未知指标与检查建议。':data.status==='failed'?`运行失败：${data.error || '未提供原因'}`:`${status.label}，已刷新当前任务结果。`,data.status==='failed');
  }catch(error){if(root().dataset.taskId===task&&(epoch===generation||error.code==='SNAPSHOT_UNAVAILABLE')){pollingRun=null;notify(`${error.message} 已停止轮询，请刷新任务后再查询。`,true);}}
}
function startPolling() {
  clearPoll();const workspace=root().querySelector('.workspace');
  if(!workspace||!['queued','running'].includes(workspace.dataset.runStatus))return;
  pollingRun=workspace.dataset.runId;pollDelay=2000;
  const task=root().dataset.taskId,epoch=generation;
  timer=setTimeout(()=>poll(pollingRun,task,epoch),pollDelay);
}
// Events are delegated because SSR replaces the root.
document.addEventListener('input',event=>{if(event.target.closest('form[data-draft]'))saveDrafts();});
document.addEventListener('change',event=>{if(event.target.closest('form[data-draft]'))saveDrafts();});
document.addEventListener('submit',async event=>{
  const form=event.target.closest('form[data-operation]');if(!form)return;event.preventDefault();
  if(pending||stale){notify('请先刷新当前任务状态，再核对并提交。',true);return;}
  const body=Object.fromEntries(new FormData(form));body.expected_revision=Number(body.expected_revision);
  if(body.reason!==undefined&&!body.reason.trim()){notify('请填写复核理由。',true);return;}
  if(body.question!==undefined&&!body.question.trim()){notify('请输入问题。',true);return;}
  saveDrafts();pending=true;clearPoll();disableWrites();
  const operation=form.dataset.operation,draft=form.dataset.draft,task=root().dataset.taskId;
  let confirmed=false;
  try{
    const headers={'Content-Type':'application/json'};
    if(operation==='export'){const fingerprint=JSON.stringify(body);if(keys.get(draft)?.body!==fingerprint)keys.set(draft,{id:crypto.randomUUID(),body:fingerprint});headers['Idempotency-Key']=keys.get(draft).id;}
    const {data,payload}=await request(form.getAttribute('action'),{method:'POST',headers,body:JSON.stringify(body)});
    if(operation==='run'){
      if(typeof data.run_id!=='string')throw new RequestError('未取得运行ID，请刷新核对。','INVALID_RESPONSE',true);
      confirmed=true;drafts.delete(draft);
      const loaded=await refresh({preserve:false});restoreDrafts();
      if(loaded){notify('请求已受理，正在查询运行状态；受理不等于完成。');pollDelay=2000;pollingRun=data.run_id;timer=setTimeout(()=>poll(data.run_id,task,generation),pollDelay);}
    }else{
      const revision=revisionFrom(data,payload);confirmed=true;keys.delete(draft);drafts.delete(draft);
      const loaded=await refresh({preserve:false,expected:revision});restoreDrafts();
      if(loaded)notify(['review','disposition'].includes(operation)?`人工核查已保存，当前revision ${root().dataset.revision}，已刷新评分和导出状态。`:'服务端导出已生成，请在右侧下载。');
    }
  }catch(error){
    if(!error.uncertain)keys.delete(draft);
    if(error.code==='REVISION_CONFLICT'||error.code==='RUN_BUSY'||error.uncertain){stale=true;}
    notify(confirmed?`服务端已受理，但页面刷新未完成。请刷新核对，勿重复提交。${error.message}`:error.message,true);
  }finally{
    pending=false;
    if(stale||confirmed===false){disableWrites();notify(`${feedback.textContent} 点击“刷新任务”核对后再操作。`,true);}
    // Do not resubmit a write or restart a run after ambiguous transport failure.
  }
});
document.addEventListener('click',async event=>{
  const refreshButton=event.target.closest('[data-refresh]');
  if(refreshButton){
    if(pending)return;refreshButton.disabled=true;clearPoll();
    try{if(await refresh()){notify('已取得当前版本；未提交草稿已保留，请核对后决定是否提交。');startPolling();}}catch(error){notify(error.message,true);}finally{document.querySelectorAll('[data-refresh]').forEach(b=>{b.disabled=false;});}return;
  }
  const artifact=event.target.closest('[data-artifact]');if(artifact){event.preventDefault();await downloadArtifact(artifact);return;}
  const tab=event.target.closest('[role=tab][data-panel]');if(tab){initTabs(tab.dataset.panel);return;}
  const evidence=event.target.closest('a[data-evidence]');if(evidence){event.preventDefault();await openEvidence(evidence);}
});
document.addEventListener('keydown',event=>{
  const current=event.target.closest('[role=tab][data-panel]');if(!current)return;
  const tabs=[...root().querySelectorAll('[role=tab]')],index=tabs.indexOf(current);
  const next=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:null;
  if(next!==null){event.preventDefault();initTabs(tabs[next].dataset.panel);tabs[next].focus();}
});
async function openEvidence(link) {
  const source=document.getElementById(`ev-${link.dataset.evidence}`);if(!source){notify('证据详情缺失，请刷新任务。',true);return;}
  const href=source.dataset.viewer;
  if(!/^\/api\/reports\/[A-Za-z0-9_.:%-]+\/pdf#page=[1-9]\d*$/.test(href)){notify('证据查看链接无效。',true);return;}
  lastFocus=link;document.getElementById('evidence-heading').textContent=source.querySelector('h4').textContent;
  document.getElementById('evidence-meta').textContent=source.querySelector('.source-meta').textContent;
  document.getElementById('evidence-text').textContent=source.querySelector('pre').textContent;
  const dialog=document.getElementById('evidence-dialog'),frame=document.getElementById('pdf-frame'),status=document.getElementById('pdf-status'),external=document.getElementById('pdf-link');
  frame.hidden=true;frame.removeAttribute('src');external.href=href;external.hidden=true;status.textContent='正在检查登记PDF…';dialog.showModal();
  pdfController?.abort();const controller=new AbortController();pdfController=controller;
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(href,{credentials:'same-origin',headers:{Range:'bytes=0-4'},signal:controller.signal});
    if(!response.ok||!response.headers.get('content-type')?.includes('application/pdf'))throw new Error(response.status===403||response.status===401?'无权访问登记PDF。':'登记PDF不存在或当前不可用。');
    if(pdfController!==controller||!dialog.open)return;
    frame.src=href;frame.hidden=false;external.hidden=false;status.textContent='请核对原文；若内嵌查看器不可用，可在新窗口打开。';
  }catch(error){if(pdfController===controller&&dialog.open)status.textContent=error.name==='AbortError'?'PDF读取超时，请关闭后重试。':error.message;}
  finally{clearTimeout(timeout);controller.abort();}
}
const dialog=document.getElementById('evidence-dialog');
document.getElementById('close-evidence').addEventListener('click',()=>dialog.close());
dialog.addEventListener('close',()=>{pdfController?.abort();pdfController=null;document.getElementById('pdf-frame').removeAttribute('src');lastFocus?.focus();});
window.addEventListener('pagehide',()=>{generation++;clearPoll();controllers.forEach(c=>c.abort());pdfController?.abort();});
initTabs();startPolling();

async function downloadArtifact(link) {
  if(link.disabled)return;link.disabled=true;
  const controller=new AbortController();controllers.add(controller);const timeout=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch(link.dataset.url,{credentials:'same-origin',signal:controller.signal,redirect:'error'});
    if(!response.ok){
      const message={401:'用户会话已失效，请重新打开任务。',403:'无权下载此产物。',404:'产物不存在或不属于当前用户。'}[response.status] || '服务端下载失败。';
      throw new Error(message);
    }
    const revision=response.headers.get('X-Artifact-Revision'),current=response.headers.get('X-Artifact-Current');
    if(revision===null||!['true','false'].includes(current))throw new Error('下载响应缺少版本信息，请刷新后重试。');
    const anchor=document.createElement('a');
    anchor.href=link.dataset.url;anchor.download=link.dataset.filename||'analysis-result';document.body.append(anchor);anchor.click();anchor.remove();
    notify(`已发起服务端文件下载 · revision ${revision}${current==='false'?' · 已过期，仅供历史核对':''}`);
  }catch(error){notify(error.name==='AbortError'?'下载超时，请重试。':error.message,true);}
  finally{clearTimeout(timeout);controllers.delete(controller);link.disabled=false;}
}
