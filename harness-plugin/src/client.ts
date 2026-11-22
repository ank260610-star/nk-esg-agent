/** 本地业务 API 客户端：只访问 127.0.0.1，不接收任意 URL/路径。 */
export class BusinessClient {
  constructor(
    private readonly baseUrl: string = 'http://127.0.0.1:8000',
    private readonly token: string = ''
  ) {}

  async getTaskResults(): Promise<unknown> {
    // task_id 由服务端令牌上下文决定，模型不可自行填写（TOOL_SPEC §get_task_results）
    const response = await fetch(`${this.baseUrl}/api/tasks/current/results`, {
      headers: { 'X-Agent-Token': this.token },
      signal: AbortSignal.timeout(10_000)
    })
    if (!response.ok) {
      throw new Error(`business_api_${response.status}`)
    }
    return await response.json()
  }
}
