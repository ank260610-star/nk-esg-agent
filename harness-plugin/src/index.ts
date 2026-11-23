import type { Context } from "@deepseek-ai/cordis";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { BusinessClient } from "./client.js";
import {
  AGENT_TOOL_WHITELIST,
  PARAMETERS,
  DESCRIPTIONS,
  isWhitelisted,
} from "./tools.js";

export const name = "nk-esg-tools";
export const inject = ["tools"];
export function apply(ctx: Context) {
  const client = new BusinessClient(
    process.env.ESG_API_BASE_URL || "http://127.0.0.1:8000",
    process.env.ESG_AGENT_TOKEN || "",
    process.env.ESG_TASK_ID || null,
  );
  ctx.on("agent/created", ({ agent }) => {
    agent.ctx.tools.restrict({ allow: [...AGENT_TOOL_WHITELIST] });
  });
  // A single monotonic allowlist governs both fresh and resumed runtime sessions.
  ctx.tools.guard((exec) =>
    isWhitelisted(exec.name) ? undefined : `FORBIDDEN_TOOL:${exec.name}`,
  );
  for (const tool of AGENT_TOOL_WHITELIST) {
    ctx.tools.register(
      defineTool({
        name: tool,
        description: DESCRIPTIONS[tool],
        parameters: PARAMETERS[tool],
        output: {
          schema: {
            type: "object",
            additionalProperties: false,
            properties: {
              ok: { type: "boolean", required: true },
              data: {
                oneOf: [
                  { type: "object", additionalProperties: true },
                  { type: "null" },
                ],
                required: true,
              },
              error: {
                oneOf: [
                  { type: "object", additionalProperties: true },
                  { type: "null" },
                ],
                required: true,
              },
              task_id: {
                oneOf: [{ type: "string" }, { type: "null" }],
                required: true,
              },
              revision: {
                oneOf: [{ type: "integer" }, { type: "null" }],
                required: true,
              },
            },
          },
          render: (_args, value) => [
            { type: "text", text: JSON.stringify(value) },
          ],
        },
        async execute(args, exec) {
          const identity = JSON.stringify([
            exec.agent?.session.id || "",
            exec.callId,
          ]);
          return await client.call(tool, args as Record<string, unknown>, {
            signal: exec.signal,
            callIdentity: identity,
          });
        },
      }),
    );
  }
}
