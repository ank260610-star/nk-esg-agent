// User-route helpers only. No Agent token, model key or business arithmetic.
export class RequestError extends Error {
  constructor(message, code='SERVICE_ERROR', uncertain=false) {super(message);this.code=code;this.uncertain=uncertain;}
}
export function decodeResponse(status, payload) {
  if (status < 200 || status >= 300 || payload?.ok !== true) {
    const code=payload?.error?.code || ({401:'UNAUTHORIZED',403:'FORBIDDEN',404:'NOT_FOUND',409:'REVISION_CONFLICT'}[status]) || 'SERVICE_ERROR';
    const messages={UNAUTHORIZED:'用户会话失效，请重新打开任务页面。',FORBIDDEN:'当前用户无权执行此操作。',NOT_FOUND:'任务或资源不存在。',REVISION_CONFLICT:'数据版本已变化，草稿已保留。请刷新任务后核对，再决定是否提交。',RUN_BUSY:'任务正在运行，请勿重复启动。'};
    throw new RequestError(messages[code] || payload?.error?.message || '请求未成功，请核对任务状态。', code, status>=500);
  }
  if (!payload.data || typeof payload.data !== 'object') throw new RequestError('服务器响应不完整，请刷新核对实际结果。','INVALID_RESPONSE',true);
  return payload.data;
}
export function runStatus(value) {
  const labels={queued:'排队中',running:'运行中',waiting_review:'待人工复核',waiting_budget:'费用检查点暂停，待核对授权',completed:'本轮运行结束',completed_with_limitations:'核查流程已完成，存在使用限制',failed:'运行失败',cancelled:'已取消'};
  if (!Object.hasOwn(labels,value)) throw new RequestError('服务端运行状态无法识别。','INVALID_RESPONSE');
  return {label:labels[value],active:['queued','running'].includes(value)};
}
export function revisionFrom(data, envelope) {
  const revision=envelope?.revision ?? data?.revision;
  if (!Number.isSafeInteger(revision) || revision<0) throw new RequestError('未取得有效服务端版本，请刷新核对。','INVALID_RESPONSE',true);
  return revision;
}

export function buildMetricReview(fields) {
  const body={action:fields.action,record_id:fields.record_id,expected_revision:Number(fields.expected_revision),reason:fields.reason,
    evidence_ids:(fields.evidence_ids||'').split(/\s+/).filter(Boolean)};
  if(!body.reason?.trim()||!body.evidence_ids.length)throw new RequestError('请填写理由并选择原文证据。','INVALID_INPUT');
  if(body.action==='correct'){
    body.correction={};
    for(const [name,value] of Object.entries(fields)){
      if(!name.startsWith('c_'))continue;
      const field=name.slice(2);
      if(fields[`null_${field}`]==='null'){body.correction[field]=null;continue;}
      if(field==='denominator'){
        if(!fields.d_kind?.trim()||!fields.d_scale?.trim()||!fields.d_unit?.trim())throw new RequestError('请补齐分母类型、倍率和单位，或选择空值。','INVALID_INPUT');
        body.correction[field]={kind:fields.d_kind,currency:fields.d_currency||null,scale:fields.d_scale,unit:fields.d_unit};
      }else body.correction[field]=value;
    }
  }
  return body;
}
