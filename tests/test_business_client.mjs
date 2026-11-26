import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
const source=await readFile(new URL('../backend/src/esg_app/static/js/business-client.js',import.meta.url),'utf8');
const {decodeResponse,revisionFrom,runStatus}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
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
