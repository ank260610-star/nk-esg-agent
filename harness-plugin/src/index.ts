import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { BusinessClient } from './client.js'
import { rejectCommandFields } from './tools.js'

export const name = 'nk-esg-tools'
export const inject = ['tools']

export function apply(ctx: Context) {
  const baseUrl = process.env.ESG_API_BASE_URL || 'http://127.0.0.1:8000'
  const token = process.env.ESG_AGENT_TOKEN || ''

  ctx.tools.register(defineTool({
    name: 'get_task_results',
    description: 'Read the current bound ESG task results (metrics, checks, score) from the local business service. No task id or path arguments accepted.',
    parameters: {},
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        properties: {
          ok: { type: 'boolean', required: true },
          task_id: { type: 'string' },
          revision: { type: 'integer' }
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
      return (await client.getTaskResults()) as { ok: boolean; task_id?: string; revision?: number }
    }
  }))
}
