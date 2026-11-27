import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=await readFile(new URL('../backend/src/esg_app/static/js/business-client.js',import.meta.url),'utf8');
const {decodeResponse,revisionFrom,runStatus,buildMetricReview}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('metric correction preserves precision, zero, null and evidence selection',()=>{
  const fields={action:'correct',record_id:'r1',expected_revision:'0',reason:'原文依据',evidence_ids:'ev1 ev2',c_value:'0.1300',c_raw_value:'0',c_canonical_unit:'',null_canonical_unit:'null',c_denominator:'',null_denominator:'null',c_status:'extracted'};
  const body=buildMetricReview(fields);
  assert.equal(body.expected_revision,0);assert.equal(body.correction.value,'0.1300');assert.equal(body.correction.raw_value,'0');
  assert.equal(body.correction.canonical_unit,null);assert.deepEqual(body.evidence_ids,['ev1','ev2']);
  const confirmed=buildMetricReview({...fields,action:'confirm'});assert.equal(confirmed.correction,undefined);
  assert.throws(()=>buildMetricReview({...fields,evidence_ids:''}));
  assert.throws(()=>buildMetricReview({...fields,c_denominator:'bad json',null_denominator:'text'}));
  const denominator=buildMetricReview({...fields,null_denominator:'text',d_kind:'revenue',d_currency:'CNY',d_scale:'1',d_unit:'万元营收'});
  assert.deepEqual(denominator.correction.denominator,{kind:'revenue',currency:'CNY',scale:'1',unit:'万元营收'});
  const missing=buildMetricReview({...fields,c_value:'',null_value:'null',c_raw_value:'/',c_status:'missing_marker'});
  assert.equal(missing.correction.value,null);assert.equal(missing.correction.raw_value,'/');
});
test('HTTP and business errors are independent',()=>{
  assert.throws(()=>decodeResponse(200,{ok:false,error:{code:'REVISION_CONFLICT'}}),e=>e.code==='REVISION_CONFLICT');
  assert.throws(()=>decodeResponse(500,{ok:false,error:{code:'INTERNAL'}}),e=>e.uncertain);
  assert.throws(()=>decodeResponse(403,{}),e=>e.code==='FORBIDDEN');
  assert.throws(()=>decodeResponse(200,{ok:true,data:null}),e=>e.uncertain);
});
test('revision works at envelope/data level and zero is valid',()=>{
  assert.equal(revisionFrom({revision:0}),0);
  assert.equal(revisionFrom({}, {revision:9}),9);
  assert.throws(()=>revisionFrom({revision:'9'}));
});
test('only queued/running continue polling; paused is not completed',()=>{
  assert.equal(runStatus('queued').active,true);
  assert.equal(runStatus('running').active,true);
  assert.equal(runStatus('waiting_review').active,false);
  assert.equal(runStatus('waiting_review').label,'待人工复核');
  assert.equal(runStatus('failed').active,false);
  assert.throws(()=>runStatus('invented'));
});

test('limited completion stops polling without claiming facts confirmed',()=>{assert.equal(runStatus('completed_with_limitations').active,false);assert.match(runStatus('completed_with_limitations').label,/使用限制/);});
