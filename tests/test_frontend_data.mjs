// Pure data/export boundaries; browser workflow is verified separately.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const source = await readFile(new URL('../backend/src/esg_app/static/js/demo-data.js',import.meta.url),'utf8');
const {createDemoState,displayNumber,safeCsvCell}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const first=createDemoState(),second=createDemoState();
first.metrics[0].values['2024']='1';
assert.equal(second.metrics[0].values['2024'],'10101826.20');
assert.equal(second.metrics.find(m=>m.id==='E03').values['2023'],'/');
assert.equal(displayNumber('0.1300'),'0.1300');
assert.equal(displayNumber('12341455.00'),'12,341,455.00');
assert.equal(displayNumber('/'),'/');
assert.equal(safeCsvCell(null),'""');
assert.equal(safeCsvCell('=SUM(A1:A2)'),`"'=SUM(A1:A2)"`);
assert.equal(safeCsvCell(' \t@value'),`"' \t@value"`);
assert.equal(safeCsvCell('a"b'),'"a""b"');
assert.equal(second.score.final_score,null);
assert.equal(second.checks.length,20);
console.log('Frontend data checks passed: isolated state, precision, missing values, CSV formula escaping.');
