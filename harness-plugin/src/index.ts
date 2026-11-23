import type { Context } from '@deepseek-ai/cordis'
import type { JsonValue } from '@deepseek-ai/dsh-session'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { BusinessClient } from './client.js'
import { rejectCommandFields } from './tools.js'

export const name = 'nk-esg-tools'
export const inject = ['tools']

/** 契约白名单：模型只能调用这些工具，其余一律单调拒绝。 */
const ALLOWED_TOOLS: ReadonlySet<string> = new Set([
  'get_task_results',
  // 完整 P0 再逐个放开 contracts/tools.json 的其余 9 个名称；
  // 闸门 A 阶段只放行已实现的 get_task_results。
])

export function apply(ctx: Context) {
  const baseUrl = process.env.ESG_API_BASE_URL || 'http://127.0.0.1:8000'
  const token = process.env.ESG_AGENT_TOKEN || ''

  // ── 安全红线：单调 guard，返回 reason 即拒绝，任何 pre-execute listener 无法翻案 ──
  // 覆盖 Shell(pwsh)、文件写入(write/edit)、网络(web_fetch/web_search)、
  // 子Agent(subagent*)及一切非白名单工具。全局生效，新建/恢复会话都经过。
  ctx.tools.guard((exec) => {
    if (!ALLOWED_TOOLS.has(exec.name)) {
      return `FORBIDDEN_TOOL:${exec.name} not in ESG agent allowlist`
    }
    return undefined
  })

  ctx.tools.register(defineTool({
    name: 'get_task_results',
    description: 'Read the current bound ESG task results (metrics, checks, score) from the local business service. No task id or path arguments accepted.',
    parameters: {},
    output: {
      // 对齐 contracts v0.2.0 get_task_results_output 信封；
      // 用 dsh-tools 的 DSL：required 为 property 级、可空字段用 oneOf。
      // additionalProperties:false 防止输出被截断/夹带后误判为成功。
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          ok: { type: 'boolean', required: true },
          data: { oneOf: [{ type: 'object', additionalProperties: true }, { type: 'null' }], required: true },
          error: { oneOf: [{ type: 'object', additionalProperties: true }, { type: 'null' }], required: true },
          task_id: { oneOf: [{ type: 'string' }, { type: 'null' }], required: true },
          revision: { oneOf: [{ type: 'integer' }, { type: 'null' }], required: true }
        }
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }]
    },
    async execute(_args, exec) {
      rejectCommandFields(_args as Record<string, unknown>)
      if (exec.signal.aborted) {
        throw new Error('TOOL_ABORTED')
      }
      const client = new BusinessClient(baseUrl, token)
      const raw = (await client.getTaskResults()) as Record<string, JsonValue>
      // 契约信封：五个字段必须齐全，缺失即视为异常，绝不返回半截结果。
      const required = ['ok', 'data', 'error', 'task_id', 'revision']
      const missing = required.filter((k) => !(k in raw))
      if (missing.length > 0) {
        throw new Error(`CONTRACT_ENVELOPE_INCOMPLETE:${missing.join(',')}`)
      }
      return {
        ok: raw.ok as boolean,
        data: raw.data as Record<string, JsonValue> | null,
        error: raw.error as Record<string, JsonValue> | null,
        task_id: raw.task_id as string | null,
        revision: raw.revision as number | null
      }
    }
  }))
}
