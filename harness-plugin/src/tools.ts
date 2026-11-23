/** Task 1 领域工具：只注册 get_task_results，默认拒绝其他工具。 */
export const AGENT_TOOL_WHITELIST = ['get_task_results'] as const

export type WhitelistedTool = (typeof AGENT_TOOL_WHITELIST)[number]

export function isWhitelisted(name: string): name is WhitelistedTool {
  return (AGENT_TOOL_WHITELIST as readonly string[]).includes(name)
}

/** 拒绝模型夹带的原生命令字段（P01 越权测试用例）。 */
export function rejectCommandFields(args: Record<string, unknown>): void {
  for (const key of ['command', 'task_id', 'path', 'url', 'weight']) {
    if (key in args) {
      throw new Error(`FORBIDDEN_FIELD:${key}`)
    }
  }
}
