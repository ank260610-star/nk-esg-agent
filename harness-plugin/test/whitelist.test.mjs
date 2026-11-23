import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { isWhitelisted, rejectCommandFields } from '../dist/tools.js'
import { BusinessClient } from '../dist/client.js'

test('domain tools are whitelisted and native tools are denied', () => {
  assert.equal(isWhitelisted('get_task_results'), true)
  assert.equal(isWhitelisted('save_metric_candidates'), false)
  assert.equal(isWhitelisted('bash'), false)
})

test('command and path override fields are rejected', () => {
  assert.throws(() => rejectCommandFields({ task_id: 'task_B' }), /FORBIDDEN_FIELD/)
  assert.throws(() => rejectCommandFields({ command: '/permission' }), /FORBIDDEN_FIELD/)
  assert.doesNotThrow(() => rejectCommandFields({}))
})

test('client posts to /agent/tool with no task_id in body', async () => {
  let seen = null
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c) => { body += c })
    req.on('end', () => {
      seen = { url: req.url, method: req.method, token: req.headers['x-agent-token'], body }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: true, data: {}, error: null, task_id: 'task_A', revision: 7 }))
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  try {
    const client = new BusinessClient(`http://127.0.0.1:${port}`, 'test-token')
    const result = await client.getTaskResults()
    assert.equal(seen.url, '/agent/tool')
    assert.equal(seen.method, 'POST')
    assert.equal(seen.token, 'test-token')
    const payload = JSON.parse(seen.body)
    assert.equal(payload.tool, 'get_task_results')
    assert.equal(Object.hasOwn(payload.arguments, 'task_id'), false)
    assert.equal(result.ok, true)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})
