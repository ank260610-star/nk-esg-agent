import type {
  ParameterSchemaSpec,
  ValueSchemaSpec,
} from "@deepseek-ai/dsh-tools";

export const AGENT_TOOL_WHITELIST = [
  "parse_report",
  "search_evidence",
  "get_rulebook",
  "extract_metrics",
  "validate_metrics",
  "compare_years",
  "save_check_proposals",
  "calculate_score",
  "get_task_results",
  "export_analysis",
] as const;
export type WhitelistedTool = (typeof AGENT_TOOL_WHITELIST)[number];
export const WRITE_TOOLS = new Set<WhitelistedTool>([
  "parse_report",
  "extract_metrics",
  "compare_years",
  "save_check_proposals",
  "export_analysis",
]);
export function isWhitelisted(name: string): name is WhitelistedTool {
  return (AGENT_TOOL_WHITELIST as readonly string[]).includes(name);
}
const rev = {
  type: "integer",
  required: true,
  description:
    "Latest task revision read from the server. On conflict read results again; do not blindly retry.",
} as const;
const metric = {
  type: "string",
  enum: ["E01", "E02", "E03", "E04", "E05", "E06"],
} as const;
const nullableText = { oneOf: [{ type: "string" }, { type: "null" }] } as const;
const check: ValueSchemaSpec = {
  type: "object",
  additionalProperties: false,
  properties: {
    check_id: {
      type: "string",
      enum: [
        "C01",
        "C02",
        "C03",
        "C04",
        "Q01",
        "Q02",
        "Q03",
        "Q04",
        "B01",
        "B02",
        "B03",
        "B04",
        "Y01",
        "Y02",
        "Y03",
        "Y04",
        "T01",
        "T02",
        "T03",
        "T04",
      ],
      required: true,
    },
    rule_version: { type: "string", const: "disclosure-v0.1", required: true },
    status: {
      type: "string",
      enum: ["met", "partial", "not_met", "unknown"],
      required: true,
    },
    evidence_ids: { type: "array", items: { type: "string" }, required: true },
    reason: { type: "string", required: true },
    missing_information: {
      type: "array",
      items: { type: "string" },
      required: true,
    },
    suggestion: { type: "string", required: true },
    target_key: { ...nullableText, required: true },
  },
};
export const PARAMETERS: Record<WhitelistedTool, ParameterSchemaSpec> = {
  parse_report: { expected_revision: rev },
  search_evidence: {
    query: { type: "string", required: true },
    metric_id: metric,
    limit: { type: "integer" },
  },
  get_rulebook: {},
  extract_metrics: {
    expected_revision: rev,
    metric_ids: { type: "array", items: metric, required: true },
    years: { type: "array", items: { type: "integer" }, required: true },
  },
  validate_metrics: {
    record_ids: { type: "array", items: { type: "string" }, required: true },
  },
  compare_years: {
    expected_revision: rev,
    older_record_id: { type: "string", required: true },
    newer_record_id: { type: "string", required: true },
  },
  save_check_proposals: {
    expected_revision: rev,
    checks: { type: "array", items: check, required: true },
  },
  calculate_score: {},
  get_task_results: {},
  export_analysis: { expected_revision: rev },
};
export const DESCRIPTIONS: Record<WhitelistedTool, string> = {
  parse_report:
    "Parse only the bound task PDFs. Partial parsing is not evidence of non-disclosure.",
  search_evidence:
    "Search bound report evidence and linked notes. Query max 2000 chars; limit 1–20. Empty search never implies zero or not_met.",
  get_rulebook:
    "Read the current experimental disclosure rulebook and anchors. No external standards or weights may be supplied.",
  extract_metrics:
    "Backend extracts 1–6 unique E01–E06 metrics for 1–10 selected years. Do not supply values, evidence, configuration, or paths.",
  validate_metrics:
    "Read-only diagnosis of 1–100 unique stored record IDs. Does not mean human confirmation.",
  compare_years:
    "Compare two current stored records. Only a comparable result permits ordinary year-on-year conclusions.",
  save_check_proposals:
    "Save 1–20 unique proposals under current rules. Evidence is required except unknown; no automatic N/A or overwriting human decisions.",
  calculate_score:
    "Compute disclosure quality deterministically from current checks. Unknown is not zero and an incomplete score is not final.",
  get_task_results:
    "Read current and stale results, revision, issues and extraction runs. Review is only through the user interface.",
  export_analysis:
    "Export Markdown, CSV and JSON from the specified revision. No destination URL, path or arbitrary content is accepted.",
};
export function rejectCommandFields(args: Record<string, unknown>): void {
  for (const key of [
    "command",
    "task_id",
    "path",
    "url",
    "weight",
    "model",
    "model_id",
    "prompt",
    "idempotency_key",
  ]) {
    if (Object.hasOwn(args, key)) throw new Error(`FORBIDDEN_FIELD:${key}`);
  }
}
export function rejectExtraArgs(
  tool: WhitelistedTool,
  args: Record<string, unknown>,
): void {
  rejectCommandFields(args);
  if (Object.keys(args).some((k) => !Object.hasOwn(PARAMETERS[tool], k)))
    throw new Error("UNDECLARED_ARGUMENT");
}
