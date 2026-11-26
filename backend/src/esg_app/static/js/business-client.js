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
  const labels={queued:'排队中',running:'运行中',waiting_review:'待人工复核',completed:'本轮运行结束',completed_with_limitations:'核查流程已完成，存在使用限制',failed:'运行失败',cancelled:'已取消'};
  if (!Object.hasOwn(labels,value)) throw new RequestError('服务端运行状态无法识别。','INVALID_RESPONSE');
  return {label:labels[value],active:['queued','running'].includes(value)};
}
export function revisionFrom(data, envelope) {
  const revision=envelope?.revision ?? data?.revision;
  if (!Number.isSafeInteger(revision) || revision<0) throw new RequestError('未取得有效服务端版本，请刷新核对。','INVALID_RESPONSE',true);
  return revision;
}
