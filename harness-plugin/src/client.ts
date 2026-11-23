import type { JsonValue } from "@deepseek-ai/dsh-session";
import { createHash, randomUUID } from "node:crypto";
import {
  isWhitelisted,
  rejectExtraArgs,
  WRITE_TOOLS,
  type WhitelistedTool,
} from "./tools.js";

export interface ToolResult {
  ok: boolean;
  data: Record<string, JsonValue> | null;
  error: { code: string; message: string; retryable: boolean } | null;
  task_id: string | null;
  revision: number | null;
}
export interface CallOptions {
  signal?: AbortSignal;
  callIdentity?: string;
}
export function hostKey(
  token: string,
  task: string,
  tool: string,
  callIdentity: string,
): string {
  const b = createHash("sha256")
    .update(JSON.stringify([token, task, tool, callIdentity]))
    .digest()
    .subarray(0, 16);
  b[6] = (b[6] & 15) | 80;
  b[8] = (b[8] & 63) | 128;
  const h = b.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
function object(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}
export function isEnvelope(v: unknown): v is ToolResult {
  if (
    !object(v) ||
    Object.keys(v).sort().join(",") !== "data,error,ok,revision,task_id" ||
    typeof v.ok !== "boolean"
  )
    return false;
  if (
    !(v.task_id === null || typeof v.task_id === "string") ||
    !(
      v.revision === null ||
      (Number.isSafeInteger(v.revision) && Number(v.revision) >= 0)
    )
  )
    return false;
  if (v.ok)
    return (
      object(v.data) &&
      v.error === null &&
      typeof v.task_id === "string" &&
      v.task_id.length > 0 &&
      v.revision !== null
    );
  if (
    v.data !== null ||
    !object(v.error) ||
    Object.keys(v.error).sort().join(",") !== "code,message,retryable"
  )
    return false;
  if (
    typeof v.error.code !== "string" ||
    typeof v.error.message !== "string" ||
    typeof v.error.retryable !== "boolean"
  )
    return false;
  return (
    !["UNAUTHORIZED", "FORBIDDEN"].includes(v.error.code) ||
    (v.task_id === null && v.revision === null)
  );
}
/** Host-controlled loopback transport. No model URL, headers or retry policy. */
export class BusinessClient {
  private readonly baseUrl: string;
  constructor(
    baseUrl = "http://127.0.0.1:8000",
    private readonly token = "",
    private readonly taskId: string | null = null,
    private readonly timeoutMs?: number,
    private readonly delayMs = 250,
  ) {
    const u = new URL(baseUrl);
    if (
      u.protocol !== "http:" ||
      !["127.0.0.1", "[::1]"].includes(u.hostname) ||
      u.username ||
      u.password ||
      u.search ||
      u.hash ||
      u.pathname !== "/"
    )
      throw new Error("LOOPBACK_API_REQUIRED");
    this.baseUrl = u.origin;
  }
  private failure(
    code: string,
    message: string,
    retryable = false,
  ): ToolResult {
    return {
      ok: false,
      data: null,
      error: { code, message, retryable },
      task_id: ["UNAUTHORIZED", "FORBIDDEN"].includes(code)
        ? null
        : this.taskId,
      revision: null,
    };
  }
  async call(
    tool: WhitelistedTool,
    args: Record<string, unknown>,
    options: CallOptions = {},
  ): Promise<ToolResult> {
    if (!isWhitelisted(tool))
      return this.failure("FORBIDDEN", "Tool is not allowed");
    try {
      rejectExtraArgs(tool, args);
    } catch {
      return this.failure("INVALID_INPUT", "Undeclared or forbidden arguments");
    }
    const key = WRITE_TOOLS.has(tool)
      ? options.callIdentity
        ? hostKey(this.token, this.taskId || "", tool, options.callIdentity)
        : randomUUID()
      : null;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (options.signal?.aborted)
        return this.failure(
          "CANCELLED",
          "Cancelled; remote write outcome may be unknown",
        );
      const timeout = AbortSignal.timeout(
        this.timeoutMs ??
          (["parse_report", "extract_metrics"].includes(tool) ? 120000 : 30000),
      );
      const signal = options.signal
        ? AbortSignal.any([options.signal, timeout])
        : timeout;
      let result: ToolResult;
      try {
        const response = await fetch(`${this.baseUrl}/agent/tool`, {
          method: "POST",
          redirect: "error",
          headers: {
            "Content-Type": "application/json",
            "X-Agent-Token": this.token,
            ...(key ? { "Idempotency-Key": key } : {}),
          },
          body: JSON.stringify({ tool, arguments: args }),
          signal,
        });
        let raw: unknown;
        try {
          raw = await response.json();
        } catch {
          if (signal.aborted) throw new Error("aborted");
          return this.failure(
            "INTERNAL_ERROR",
            "Business service returned invalid JSON",
          );
        }
        if (
          !isEnvelope(raw) ||
          response.ok !== raw.ok ||
          (raw.ok && this.taskId !== null && raw.task_id !== this.taskId)
        )
          return this.failure(
            "INTERNAL_ERROR",
            "Business response violates the tool contract",
          );
        result = raw;
      } catch {
        if (options.signal?.aborted)
          return this.failure(
            "CANCELLED",
            "Cancelled; remote write outcome may be unknown",
          );
        result = this.failure(
          timeout.aborted ? "TIMEOUT" : "INTERNAL_ERROR",
          "Business transport failed; remote write outcome may be unknown",
          true,
        );
      }
      if (
        result.ok ||
        !result.error?.retryable ||
        !["TIMEOUT", "MODEL_UNAVAILABLE", "INTERNAL_ERROR"].includes(
          result.error.code,
        ) ||
        attempt === 2
      )
        return result;
      try {
        await new Promise<void>((resolve, reject) => {
          const abort = () => {
            clearTimeout(timer);
            reject(new Error("cancelled"));
          };
          const timer = setTimeout(
            () => {
              options.signal?.removeEventListener("abort", abort);
              resolve();
            },
            this.delayMs * (attempt + 1),
          );
          options.signal?.addEventListener("abort", abort, { once: true });
          if (options.signal?.aborted) abort();
        });
      } catch {
        return this.failure(
          "CANCELLED",
          "Cancelled before retry; read current task state",
        );
      }
    }
    return this.failure("INTERNAL_ERROR", "Retry limit reached");
  }
  getTaskResults(): Promise<ToolResult> {
    return this.call("get_task_results", {});
  }
}
