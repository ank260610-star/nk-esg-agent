/** 本地业务 API 客户端：只访问 127.0.0.1，不接收任意 URL/路径。 */
export class BusinessClient {
  constructor(
    private readonly baseUrl: string = 'http://127.0.0.1:8000',
    private readonly token: string = ''
  ) {}

  /**
   * 统一走 /agent/tool 入口：task_id 由服务端按 agent 令牌解析，
   * 模型不可自行填写（TOOL_SPEC §get_task_results / handoff §3.4）。
   */
  async getTaskResults(): Promise<unknown> {
    const response = await fetch(`${this.baseUrl}/agent/tool`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Agent-Token': this.token
      },
      body: JSON.stringify({ tool: 'get_task_results', arguments: {} }),
      signal: AbortSignal.timeout(10_000)
    })
    if (!response.ok) {
      throw new Error(`business_api_${response.status}`)
    }
    return await response.json()
  }
}
