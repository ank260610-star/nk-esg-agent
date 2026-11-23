import {sample, createDemoState, displayNumber, safeCsvCell} from './demo-data.js';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = createDemoState();
let localFile = null, localUrl = null, sampleAvailable = false;
let activeMetric = null, evidenceRevision = 1, runToken = 0, uploadToken = 0, toastTimer;
let activeTab = 'metrics';
const dialog = $('#evidence-dialog');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('aria-hidden','true');
  const use = document.createElementNS('http://www.w3.org/2000/svg','use');
  use.setAttribute('href',`#i-${name}`); svg.append(use); return svg;
}
function toast(message) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false;
  toastTimer = setTimeout(()=>{$('#toast').hidden=true;},4500);
}
function addMessage(text, role='assistant', evidenceId=null) {
  const message = el('article',`chat-message ${role}`);
  if (role==='assistant') {const avatar=el('span','assistant-avatar');avatar.append(icon('leaf'));message.append(avatar);}
  const bubble=el('div','bubble');
  if (role==='assistant') bubble.append(el('small','', '澄境 · 预设演示回答'));
  bubble.append(el('div','',text));
  if (evidenceId) {const button=el('button','text-button','查看参考证据');button.append(icon('book'));button.addEventListener('click',()=>openEvidence(evidenceId));bubble.append(button);}
  message.append(bubble);$('#messages').append(message);
  $('#chat-scroll').scrollTop=$('#chat-scroll').scrollHeight;
}
function metricButton(metric, year) {
  const button=el('button','metric-card');button.type='button';
  button.setAttribute('aria-label',`${metric.id} ${metric.name}，查看证据与复核`);
  const header=el('div','metric-header');header.append(el('span','metric-id',metric.id),el('span','metric-name',metric.name));
  const values=el('div','metric-value-row');values.append(el('strong','metric-value',displayNumber(metric.values[year])),el('span','metric-unit',metric.unit));
  const bottom=el('div','metric-bottom');
  const older=state.years.length>1?state.years[0]:null;
  bottom.append(el('span',metric.values[older]==='/'?'metric-note':'',older?`${older}年 ${displayNumber(metric.values[older])}`:'单年度展示 · 未计算同比'));
  const evidence=el('span','metric-evidence',metric.confirmed[year]?'已复核':'证据');evidence.append(icon('book'));bottom.append(evidence);
  button.append(header,values,bottom);button.addEventListener('click',()=>openEvidence(metric.id)); return button;
}
function render() {
  const year=state.years.at(-1);
  $('#metric-list').replaceChildren(...(state.local?[]:state.metrics.map(m=>metricButton(m,year))));
  $('#metric-year-label').textContent=`${year}年`;
  $('#result-source').textContent=state.local?'本地PDF · 未产生分析结果':`比亚迪 · ${state.years.join('—')}`;
  $('#revision-label').textContent=state.local?'尚未解析':`示例 v${state.revision}`;
  $('#stale-notice').hidden=!state.stale||state.local;
  $('#local-notice').hidden=!state.local;
  $('#refresh-snapshot').hidden=!state.stale||state.local;
  $('#refresh-snapshot').disabled=state.run==='running';
  $('#sample-report').classList.toggle('selected',!state.local);
  $('#local-report').classList.toggle('selected',state.local);
  $('#company').value=state.local?'待后端登记':sample.company;
  $('#report-year').value=state.local?'待用户确认':'2024';
  $('#start-analysis').disabled=state.run==='running';
  $('#start-analysis').textContent=state.local?'自动分析尚未接入':state.run==='running'?'演示分析中…':'运行演示分析';
  $('#start-analysis').disabled=state.local||state.run==='running';
  $('#cancel-analysis').hidden=state.run!=='running';
  $$('#sample-report, #local-report, #upload-button').forEach(n=>{n.disabled=state.run==='running';});
  $$('input[name=years]').forEach(n=>{n.disabled=state.local||state.run==='running';});
  $('.sample-summary').hidden=state.local;
  $$('[role=tab]').forEach(n=>{n.disabled=state.local;});
  $$('[role=tabpanel]').forEach(n=>{n.hidden=state.local||n.id!==`${activeTab}-panel`;});
  const exportsDisabled=state.stale||state.local||state.run==='running';
  $$('[data-export]').forEach(b=>{b.disabled=exportsDisabled;});
  $('#export-shortcut').disabled=exportsDisabled;
  $('#score-value').textContent=state.score.reference_score??'—';
  $('#coverage-value').textContent=`${state.score.coverage}%`;
  $('#coverage-bar').value=Number(state.score.coverage);
  $('#score-range').textContent=`${Number(state.score.lower_bound).toFixed(2)}—${Number(state.score.upper_bound).toFixed(2)}`;
  const known=state.checks.filter(c=>c.status!=='unknown').length;
  $('#checks-summary').textContent=`${known} / 20 已判定（演示）`;
  $('#check-list').replaceChildren(...state.checks.map(c=>{
    const row=el('div','check-row');row.append(el('span','check-id',c.id),el('span','',c.title));
    const labels={met:['green','满足'],partial:['amber','部分满足'],unknown:['neutral','未知']};
    row.append(el('span',`badge ${labels[c.status][0]}`,labels[c.status][1]));return row;
  }));
  const confirmed=state.metrics.reduce((n,m)=>n+state.years.filter(y=>m.confirmed[y]).length,0);
  $('#review-count').textContent=state.local?'等待真实后端解析':`${state.years.length*6}条年度记录 · ${confirmed?`${confirmed}条已复核`:'待人工复核'}`;
  const history=$('#review-history');history.replaceChildren();
  if(state.history.length){history.append(el('h4','','演示复核记录'));state.history.forEach(h=>history.append(el('p','',`v${h.revision} · ${h.metric_id} / ${h.year}：${h.old_value} → ${h.new_value}。理由：${h.reason}`)));}
}
function setTab(name, focus=false) {
  if(state.local)return;
  activeTab=name;
  $$('[role=tab]').forEach(b=>{const selected=b.id===`tab-${name}`;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;if(selected&&focus)b.focus();});
  render();
}
$$('[role=tab]').forEach((b,i,buttons)=>{
  b.addEventListener('click',()=>setTab(b.id.slice(4)));
  b.addEventListener('keydown',e=>{
    let next;
    if(e.key==='ArrowRight')next=(i+1)%buttons.length;
    if(e.key==='ArrowLeft')next=(i+buttons.length-1)%buttons.length;
    if(e.key==='Home')next=0;if(e.key==='End')next=buttons.length-1;
    if(next!==undefined){e.preventDefault();setTab(buttons[next].id.slice(4),true);}
  });
});
$('#expand-results').addEventListener('click',()=>{const expanded=$('.workspace').classList.toggle('results-expanded');$('#expand-results').setAttribute('aria-pressed',String(expanded));$('#expand-results').setAttribute('aria-label',expanded?'收起结果区':'展开结果区');});

function updateSnapshot(announce=true) {
  // This is a conservative demo-state transition, not the business score engine.
  // A review invalidates every sample check rather than reusing the hand-calculated score.
  if(state.hasReview||state.years.length!==2){
    state.checks.forEach(c=>{c.status='unknown';});
    state.score={reference_score:null,coverage:'0.00',lower_bound:'0',upper_bound:'100',final_score:null};
  }
  state.snapshotRevision=state.revision;state.stale=false;render();
  if(announce)toast('演示快照已更新；待核查的检查项保留为未知。');
}
$('#refresh-snapshot').addEventListener('click',()=>updateSnapshot());
$$('input[name=years]').forEach(box=>box.addEventListener('change',()=>{
  const years=$$('input[name=years]:checked').map(n=>n.value);
  if(!years.length){box.checked=true;toast('请至少保留一个分析年份。');return;}
  state.years=years;state.revision++;state.stale=true;render();
}));

async function runDemo() {
  if(state.local||state.run==='running')return;
  const token=++runToken;state.run='running';render();
  $('#run-badge').textContent='演示运行中';
  $('#progress-caption').textContent='模拟过程 · 无模型调用';
  const titles=['演示：定位报告与证据页','演示：载入六指标参考值','演示：展示披露检查状态','演示：整理结果快照'];
  for(let i=0;i<4;i++){
    if(token!==runToken)return;
    $('#progress-title').textContent=titles[i];
    $$('#progress-steps li').forEach((li,index)=>{li.classList.toggle('done',index<i);li.classList.toggle('active',index===i);});
    await new Promise(resolve=>setTimeout(resolve,750));
  }
  if(token!==runToken)return;
  state.run='completed';updateSnapshot(false);
  $$('#progress-steps li').forEach(li=>{li.classList.remove('active');li.classList.add('done');});
  $('#progress-title').textContent='演示流程完成，结果可查看';$('#progress-caption').textContent='未执行真实解析或模型分析';$('#run-badge').textContent='演示完成';
  addMessage(`演示流程已完成，已展示${state.years.join('、')}年的六项人工参考指标。\n\n${state.hasReview?'人工修改已保留；原评分示例已失效，检查项暂为未知，等待后端重新核查。':'右侧评分采用交接文档的手算示例，不是比亚迪的真实评分。'}\n\n可点击指标查看原始PDF与参考页码，也可以体验复核和导出。`);
}
$('#start-analysis').addEventListener('click',runDemo);
$('#cancel-analysis').addEventListener('click',()=>{
  runToken++;state.run='cancelled';$('#run-badge').textContent='演示已取消';$('#progress-title').textContent='演示已取消，保留已有结果';$('#progress-caption').textContent='可重新运行';
  $$('#progress-steps li').forEach(li=>li.classList.remove('active'));render();toast('已取消，当前记录保持原状。');
});

async function loadLocalFile(file) {
  if(state.run==='running'){toast('请先取消演示，再选择文件。');return;}
  if(!file)return;
  const selection=++uploadToken;
  if(!file.name.toLowerCase().endsWith('.pdf')||file.size>50*1024*1024){toast('请选择不超过50 MiB的PDF文件。');$('#file-input').value='';return;}
  const signature=await file.slice(0,5).text();
  if(selection!==uploadToken)return;
  if(signature!=='%PDF-'){toast('文件没有有效的PDF文件头，请重新选择。');$('#file-input').value='';return;}
  if(localUrl)URL.revokeObjectURL(localUrl);
  localFile=file;localUrl=URL.createObjectURL(file);
  $('#local-name').textContent=file.name;$('#local-report').hidden=false;$('#report-count').textContent='02';
  selectDocument(true);toast('已打开本地预览，文件未上传，也未生成分析结果。');openEvidence();
}
function selectDocument(local) {
  state.local=local;$('#run-badge').textContent=local?'仅本地预览':state.run==='completed'?'演示完成':'示例已就绪';
  $('#progress-title').textContent=local?'本地PDF尚未解析':'从报告，到可追溯的洞察';
  $('#progress-caption').textContent=local?'返回公开样例可体验分析流程':'模拟流程 · 未调用模型';
  $('#intro-evidence').disabled=local;
  $$('[data-question]').forEach(b=>{b.disabled=local;});render();
}
$('#upload-button').addEventListener('click',()=>$('#file-input').click());
$('#file-input').addEventListener('change',e=>loadLocalFile(e.target.files[0]));
$('#sample-report').addEventListener('click',()=>selectDocument(false));
$('#local-report').addEventListener('click',()=>{if(localFile)selectDocument(true);});
const dropZone=$('#upload-button');
for(const event of ['dragenter','dragover'])dropZone.addEventListener(event,e=>{e.preventDefault();dropZone.classList.add('dragging');});
for(const event of ['dragleave','drop'])dropZone.addEventListener(event,e=>{e.preventDefault();dropZone.classList.remove('dragging');});
dropZone.addEventListener('drop',e=>{if(e.dataTransfer.files.length!==1){toast('请每次选择一份PDF。');return;}loadLocalFile(e.dataTransfer.files[0]);});

function showPdf(url, page) {
  $('#pdf-frame').hidden=!url;$('#pdf-placeholder').hidden=Boolean(url);
  $('#pdf-new-tab').hidden=!url;
  if(url){const src=`${url}#page=${page}`;$('#pdf-frame').src=src;$('#pdf-new-tab').href=src;}
  else{$('#pdf-frame').removeAttribute('src');$('#pdf-new-tab').removeAttribute('href');$('#pdf-placeholder').textContent='本机未安装公开样例PDF。可查看上方人工参考摘要；按README准备PDF后刷新页面即可查看原件。';}
}
function openEvidence(id=null) {
  evidenceRevision=state.revision;activeMetric=id?state.metrics.find(m=>m.id===id):null;
  const isLocal=state.local;
  $('#review-form').hidden=!activeMetric||isLocal;
  $('.evidence-year').hidden=!activeMetric||isLocal;
  $('#evidence-kind').textContent=isLocal?'本地文件 · 未解析':'公开样例 · 人工参考';
  $('#evidence-title').textContent=activeMetric?`${activeMetric.id} · 证据与人工复核`:isLocal?'本地报告预览':'公开报告原件';
  $('#review-error').textContent='';$('#review-reason').value='';
  if(activeMetric&&!isLocal){
    $('#review-year').replaceChildren(...state.years.map(y=>{const option=el('option','',`${y}年`);option.value=y;return option;}));
    $('#review-year').value=state.years.at(-1);renderEvidence();
  }else{
    $('#evidence-pages').textContent=isLocal?'PDF页数尚未校验':'PDF物理页1';
    $('#evidence-heading').textContent=isLocal?localFile.name:sample.title;
    $('#evidence-summary').textContent=isLocal?'此文件只在浏览器预览，不会上传至服务器。尚未接入自动解析、指标提取或模型分析。':'公开样例用于核对原文，页面指标来自交接文档中的人工参考记录。';
    $('#evidence-summary-label').textContent=isLocal?'不能以公开样例的指标代表此文件。':'原始报告与演示数据来源分开标注。';
    $('#pdf-location').textContent=isLocal?'本地PDF预览':'PDF原件 · 第1页';showPdf(isLocal?localUrl:sampleAvailable?'/demo/report':null,1);
  }
  if(!dialog.open)dialog.showModal();
}
function renderEvidence() {
  if(!activeMetric)return;
  const m=activeMetric,year=$('#review-year').value;
  $('#evidence-pages').textContent=`PDF物理页 ${m.page} · 印刷页 ${m.printed}`;
  $('#evidence-heading').textContent=`${m.name} · ${year}年`;
  const original=sample.metrics.find(item=>item.id===m.id).values[year];
  $('#evidence-summary').textContent=`原始人工参考值：${displayNumber(original)} ${m.unit}${m.confirmed[year]?`\n当前演示复核值：${displayNumber(m.values[year])} ${m.unit}`:''}\n${m.note}\n组织范围参考：排除海外在建基地和销售门店；详见PDF物理页3。`;
  $('#evidence-summary-label').textContent='人工参考摘要，非PDF逐字引用。请打开原文核对。';
  $('#record-review-state').textContent=m.confirmed[year]?'已演示复核':'待人工复核';
  $('#review-value').value=m.values[year];$('#review-unit').textContent=`单位：${m.unit}。可填数值，或保留“/”“—”“未披露”。`;
  $('#pdf-location').textContent=`PDF物理页 ${m.page} · 印刷页 ${m.printed}`;
  showPdf(sampleAvailable?'/demo/report':null,m.page);
}
$('#review-year').addEventListener('change',()=>{renderEvidence();$('#review-reason').value='';$('#review-error').textContent='';});
$('#view-report').addEventListener('click',()=>openEvidence());
$('#intro-evidence').addEventListener('click',()=>openEvidence('E01'));
$('#close-evidence').addEventListener('click',()=>dialog.close());
dialog.addEventListener('close',()=>{$('#pdf-frame').removeAttribute('src');});
$('#review-form').addEventListener('submit',e=>{
  e.preventDefault();if(!activeMetric||state.local)return;
  if(state.run==='running'){$('#review-error').textContent='请等待演示完成或取消后，再提交复核。';return;}
  if(evidenceRevision!==state.revision){$('#review-error').textContent='记录版本已变化，请关闭并重新打开证据面板。';return;}
  const value=$('#review-value').value.trim().replaceAll(',',''),reason=$('#review-reason').value.trim();
  if(!reason){$('#review-error').textContent='请填写复核理由。';return;}
  if(!/^(?:\d+(?:\.\d+)?|\/|—|未披露)$/.test(value)){$('#review-error').textContent='请输入非负数值，或使用“/”“—”“未披露”保留缺失含义。';return;}
  const year=$('#review-year').value,old=activeMetric.values[year];
  activeMetric.values[year]=value;activeMetric.confirmed[year]=true;activeMetric.reasons[year]=reason;
  state.revision++;state.stale=true;state.hasReview=true;
  state.history.push({revision:state.revision,metric_id:activeMetric.id,year,old_value:old,new_value:value,reason,at:new Date().toISOString()});
  render();dialog.close();toast(`已保存演示复核 v${state.revision}；旧评分和导出已过期。`);
});

function answerQuestion(question) {
  if(state.local)return ['当前本地PDF尚未解析，不能回答其内容。可通过“查看报告原件”手动浏览，或返回比亚迪公开样例体验预设问答。'];
  const scope3=state.metrics.find(m=>m.id==='E03');
  if(/E03|Scope\s*3|范围三/i.test(question)) {
    if(state.hasReview)return ['记录已经过演示复核，当前没有可核查的新比较结果。请回到原文核对两年的数值、覆盖类别与边界；本原型不据此生成同比。','E03'];
    return [`原始样例中2023年Scope 3为“/”，属于缺失标记，不能当作0。因此无法计算普通同比。\n\n2024年参考值为${displayNumber(scope3.values['2024'])} tCO₂e，脚注只涵盖差旅、通勤、燃料能源相关活动，不能视为完整价值链排放。请在PDF物理页127核对。`,'E03'];
  }
  if(/评分|分数|参考分|覆盖率/.test(question))return [state.stale?'记录已修改，显示的旧评分已过期。请先更新演示快照；本页面没有执行真实评分。':state.hasReview||state.years.length!==2?'目前检查项均为未知，覆盖率为0%，没有可展示的参考分。正式评分需要后端重新核查证据。':'右侧75.00分是交接文档中的手算示例：仅C01满足、C02部分满足，其余未知。覆盖率只有12.50%，因此仅能显示参考分与9.38—96.88的区间，最终分数为空。这不是比亚迪报告的实际评分。'];
  if(/页|碳排放|证据/.test(question))return ['碳排放总量、强度与Scope 3的参考位置是PDF物理页127，对应印刷页126；能源在PDF物理页125，水资源在126。点击“查看参考证据”可打开原件。页码来自人工参考，尚未由本应用解析验证。','E01'];
  return ['当前是预设问答演示，尚未连接模型，无法对这个问题给出有依据的分析。你可以体验“E03为何不能算同比”“分数为何是参考分”“碳排放数据在第几页”这三个问题。'];
}
function ask(question) {
  const trimmed=question.trim();if(!trimmed)return;
  addMessage(trimmed,'user');const [answer,id]=answerQuestion(trimmed);addMessage(answer,'assistant',id);
}
$('#chat-form').addEventListener('submit',e=>{e.preventDefault();ask($('#question').value);$('#question').value='';});
$('#question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chat-form').requestSubmit();}});
$$('[data-question]').forEach(b=>b.addEventListener('click',()=>ask(b.dataset.question)));

function snapshot() {
  return {mode:'frontend-demo',is_demo:true,model_connected:false,revision:state.revision,snapshot_revision:state.snapshotRevision,
    company:sample.company,report:sample.title,report_year:sample.reportYear,selected_years:state.years,
    source_url:sample.source,reference_source:sample.reference,rule_version:'disclosure-v0.1',
    disclaimer:'人工参考数据与手算评分演示；未执行自动提取、真实评分或模型分析。复核仅在当前页面会话有效。',
    metrics:state.metrics.flatMap(m=>state.years.map(year=>({metric_id:m.id,name:m.name,metric_year:Number(year),raw_value:m.values[year],value:/^\d+(\.\d+)?$/.test(m.values[year])?m.values[year]:null,unit:m.unit,status:/^\d+(\.\d+)?$/.test(m.values[year])?'extracted':'missing_marker',origin:m.confirmed[year]?'manual':'human_reference',review_state:m.confirmed[year]?'confirmed':'unreviewed',evidence_id:`demo_${m.id}_${year}`,pdf_page:m.page,printed_page:m.printed,extraction_note:'人工参考，非本应用自动提取',revision:state.revision}))),
    score:{...state.score,coverage:String(Number(state.score.coverage)/100),rule_version:'disclosure-v0.1',revision:state.snapshotRevision,label:'演示评分，非企业实际评价'},
    checks:state.checks,comparisons:[],comparison_status:'needs_review',review_history:state.history};
}
function download(format) {
  if(state.stale||state.local||state.run==='running'){toast('当前不能导出，请先更新演示快照。');return;}
  const data=snapshot();let content,type;
  if(format==='json'){content=JSON.stringify(data,null,2);type='application/json';}
  if(format==='csv'){
    const header=['mode','revision','metric_id','metric_year','raw_value','value','unit','status','review_state','pdf_page','source_url','note'];
    const rows=data.metrics.map(m=>['frontend-demo',state.revision,m.metric_id,m.metric_year,m.raw_value,m.value,m.unit,m.status,m.review_state,m.pdf_page,sample.source,data.disclaimer]);
    content='\uFEFF'+[header,...rows].map(row=>row.map(safeCsvCell).join(',')).join('\r\n');type='text/csv;charset=utf-8';
  }
  if(format==='md'){
    const escape=value=>String(value).replace(/[\\`*_{}\[\]()#+!<>|]/g,'\\$&').replaceAll('\n',' ');
    content=`# 环境披露分析 · 演示快照\n\n${data.disclaimer}\n\n版本：v${state.revision}；规则：disclosure-v0.1\n\n企业：${sample.company}；年份：${state.years.join('、')}\n\n来源：[原始报告](${sample.source})\n\n人工参考来源：${sample.reference}\n\n## 六指标\n\n| 指标 | 年份 | 原值 | 单位 | PDF物理页 | 复核状态 |\n| --- | --- | --- | --- | --- | --- |\n`;
    content+=data.metrics.map(m=>`| ${escape(m.metric_id+' '+m.name)} | ${m.metric_year} | ${escape(m.raw_value)} | ${escape(m.unit)} | ${m.pdf_page} | ${m.review_state} |`).join('\n');
    content+=`\n\n## 评分手算演示\n\n参考分：${state.score.reference_score??'未知'}；覆盖率：${state.score.coverage}%；区间：${state.score.lower_bound}—${state.score.upper_bound}；最终分：未知。\n\n## 检查项（演示状态）\n\n`;
    content+=data.checks.map(c=>`- ${c.id} ${c.title}：${c.status}`).join('\n');
    content+='\n\n## 复核记录\n\n'+(state.history.length?state.history.map(h=>`- v${h.revision} ${h.metric_id}/${h.year}：${escape(h.old_value)} → ${escape(h.new_value)}。理由：${escape(h.reason)}`).join('\n'):'尚无复核记录。');
    content+='\n\n## 未知与限制\n\n未核查年度可比性，不输出普通同比。当前数据不构成完整ESG评级、违规判断或投资建议。\n';type='text/markdown;charset=utf-8';
  }
  const url=URL.createObjectURL(new Blob([content],{type}));const link=el('a');link.href=url;link.download=`ESG-DEMO-v${state.revision}.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);toast(`已导出演示${format.toUpperCase()} · v${state.revision}`);
}
$$('[data-export]').forEach(b=>b.addEventListener('click',()=>download(b.dataset.export)));
$('#export-shortcut').addEventListener('click',()=>setTab('report',true));
$('#help-button').addEventListener('click',()=>$('#help-dialog').showModal());
$('#close-help').addEventListener('click',()=>$('#help-dialog').close());
window.addEventListener('pagehide',()=>{if(localUrl)URL.revokeObjectURL(localUrl);});
render();
fetch('/demo/report-status').then(r=>{if(!r.ok)throw new Error('Unavailable');return r.json();}).then(data=>{sampleAvailable=data.available;}).catch(()=>{sampleAvailable=false;});
