// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";

import type {
  AnalysisPromptConfig,
  DiscoveryPromptConfig,
  InvestorProfile,
} from "@/lib/api";
import {
  loadAnalysisPrompt,
  loadDiscoveryPrompt,
  loadInvestorProfile,
  saveAnalysisPrompt,
  saveDiscoveryPrompt,
  saveInvestorProfile,
} from "@/lib/storage";

const fallbackProfile: InvestorProfile = {
  style: "fallback",
  horizon: "medium",
  max_drawdown_percent: 8,
  concentration_limit_percent: 35,
  expected_investment_amount: 30_000,
  prefer_dca: true,
  avoid_chasing: true,
  decision_style: "conservative",
  investment_preset: "conservative_hold",
  round_trip_fee_percent: 1.5,
  min_net_profit_percent: 1,
  hold_days_target: 7,
  swing_alerts_enabled: false,
  swing_monitor_scope: "both",
};

function profile(style: string, amount: number): InvestorProfile {
  return { ...fallbackProfile, style, expected_investment_amount: amount };
}

const fallbackPrompt = { role_prompt: "fallback", default_role_prompt: "default" };

beforeEach(() => {
  window.localStorage.clear();
});

describe("account-scoped local preferences", () => {
  it("keeps investor profiles for different users in the original storage key", () => {
    saveInvestorProfile(101, profile("account-a", 10_000));
    saveInvestorProfile(202, profile("account-b", 20_000));

    expect(loadInvestorProfile(101, fallbackProfile)).toMatchObject({
      style: "account-a",
      expected_investment_amount: 10_000,
    });
    expect(loadInvestorProfile(202, fallbackProfile)).toMatchObject({
      style: "account-b",
      expected_investment_amount: 20_000,
    });

    const raw = JSON.parse(
      window.localStorage.getItem("fundpilot-investor-profile") ?? "null",
    ) as { version: number; byUserId: Record<string, InvestorProfile> };
    expect(raw.version).toBe(1);
    expect(Object.keys(raw.byUserId)).toEqual(["101", "202"]);
  });

  it("isolates analysis and discovery prompts by user", () => {
    const analysisA: AnalysisPromptConfig = {
      role_prompt: "analysis-a",
      default_role_prompt: "analysis-default",
      is_custom: true,
    };
    const analysisB: AnalysisPromptConfig = {
      role_prompt: "analysis-b",
      default_role_prompt: "analysis-default",
      is_custom: true,
    };
    const discoveryA: DiscoveryPromptConfig = {
      role_prompt: "discovery-a",
      default_role_prompt: "discovery-default",
      is_custom: true,
    };
    const discoveryB: DiscoveryPromptConfig = {
      role_prompt: "discovery-b",
      default_role_prompt: "discovery-default",
      is_custom: true,
    };

    saveAnalysisPrompt(101, analysisA);
    saveAnalysisPrompt(202, analysisB);
    saveDiscoveryPrompt(101, discoveryA);
    saveDiscoveryPrompt(202, discoveryB);

    expect(loadAnalysisPrompt(101, fallbackPrompt).role_prompt).toBe("analysis-a");
    expect(loadAnalysisPrompt(202, fallbackPrompt).role_prompt).toBe("analysis-b");
    expect(loadDiscoveryPrompt(101, fallbackPrompt).role_prompt).toBe("discovery-a");
    expect(loadDiscoveryPrompt(202, fallbackPrompt).role_prompt).toBe("discovery-b");
  });

  it("never attributes a legacy ownerless value to the next signed-in user", () => {
    window.localStorage.setItem(
      "fundpilot-investor-profile",
      JSON.stringify(profile("legacy-account", 99_000)),
    );
    window.localStorage.setItem(
      "fundpilot-analysis-prompt",
      JSON.stringify({ role_prompt: "legacy-analysis", is_custom: true }),
    );
    window.localStorage.setItem(
      "fundpilot-discovery-prompt",
      JSON.stringify({ role_prompt: "legacy-discovery", is_custom: true }),
    );

    expect(loadInvestorProfile(202, fallbackProfile).style).toBe("fallback");
    expect(loadAnalysisPrompt(202, fallbackPrompt)).toMatchObject({
      role_prompt: "fallback",
      is_custom: false,
    });
    expect(loadDiscoveryPrompt(202, fallbackPrompt)).toMatchObject({
      role_prompt: "fallback",
      is_custom: false,
    });
  });

  it("does not read or write account data before a user id is known", () => {
    saveInvestorProfile(null, profile("unknown", 1));
    saveAnalysisPrompt(undefined, {
      role_prompt: "unknown",
      default_role_prompt: "default",
      is_custom: true,
    });

    expect(window.localStorage.getItem("fundpilot-investor-profile")).toBeNull();
    expect(window.localStorage.getItem("fundpilot-analysis-prompt")).toBeNull();
    expect(loadInvestorProfile(null, fallbackProfile).style).toBe("fallback");
  });
});
