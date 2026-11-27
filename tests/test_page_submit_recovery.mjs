import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as client from '../backend/src/esg_app/static/js/business-client.js';

for(const [status,code,blocked] of [[422,'INVALID_INPUT',false],[409,'REVISION_CONFLICT',true],[503,'INTERNAL_ERROR',true]])test(`submission ${status} restores only safe controls and preserves drafts`,async()=>{
  const handlers={};
  const buttons=[{disabled:false},{disabled:true}];
  const correction={hidden:true};
  const fields=[{name:'reason',value:'保留的理由'},{name:'action',value:'confirm'}];
  const form={dataset:{operation:'metric',draft:'metric-record'},elements:{action:{value:'confirm'}},
    querySelector:()=>correction,querySelectorAll:()=>fields,getAttribute:()=>'/tasks/t/metric-reviews'};
  const root={dataset:{taskId:'t',revision:'1'},querySelector:()=>null,querySelectorAll:s=>
    s==='form[data-operation] button[type=submit]'?buttons:s==='form[data-operation="metric"]'||s==='form[data-draft]'?[form]:[]};
  const node=()=>({hidden:false,textContent:'',after(){},addEventListener(){},classList:{toggle(){}}});
  const feedback=node(),dialog=node();
  const document={getElementById:id=>id==='business-root'?root:id==='feedback'?feedback:dialog,
    createElement:node,addEventListener:(type,fn)=>{handlers[type]=fn;}};
  class FormData{
    *[Symbol.iterator](){yield*Object.entries({action:'confirm',record_id:'record',expected_revision:'1',reason:'保留的理由'});}
    getAll(){return ['evidence'];}
  }
  let calls=0;
  const context={...client,document,FormData,AbortController,crypto:{randomUUID:()=> 'test-key'},
    window:{addEventListener(){}},setTimeout,clearTimeout,
    fetch:async()=>{calls++;return {status,headers:{get:()=> 'application/json'},json:async()=>({ok:false,error:{code,message:'证据不支持当前数值'}})};}};
  const source=fs.readFileSync('backend/src/esg_app/static/js/business.js','utf8').replace(/^import .*?;\n/,'');
  vm.runInNewContext(source,context);
  await handlers.submit({target:{closest:()=>form},preventDefault(){}});
  assert.equal(calls,1);
  assert.equal(buttons[0].disabled,blocked);
  assert.equal(buttons[1].disabled,true);
  assert.equal(fields[0].value,'保留的理由');
  assert.ok(feedback.textContent);
});
