import type { AnalysisPromptConfig } from "@/lib/api";

/** Only user-enabled custom prompts may enter provider requests. */
export function activeAnalysisRolePrompt(
  config: AnalysisPromptConfig,
): string | undefined {
  if (!config.is_custom || !config.role_prompt.trim()) return undefined;
  return config.role_prompt;
}
