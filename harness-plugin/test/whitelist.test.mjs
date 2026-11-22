import test from 'node:test'
import assert from 'node:assert/strict'
import { isWhitelisted, rejectCommandFields } from '../dist/tools.js'

test('only get_task_results is whitelisted', () => {
  assert.equal(isWhitelisted('get_task_results'), true)
  assert.equal(isWhitelisted('save_metric_candidates'), false)
  assert.equal(isWhitelisted('bash'), false)
})

test('command and path override fields are rejected', () => {
  assert.throws(() => rejectCommandFields({ task_id: 'task_B' }), /FORBIDDEN_FIELD/)
  assert.throws(() => rejectCommandFields({ command: '/permission' }), /FORBIDDEN_FIELD/)
  assert.doesNotThrow(() => rejectCommandFields({}))
})
