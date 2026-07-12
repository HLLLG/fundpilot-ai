// @vitest-environment jsdom
import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { RiskControls } from "@/components/RiskControls";

vi.mock("@/components/AnalysisModeToggle", () => ({
  AnalysisModeToggle: () => <div data-testid="analysis-mode-toggle" />,
}));
vi.mock("@/components/InvestmentPresetSelector", () => ({
  InvestmentPresetSelector: () => <div data-testid="investment-preset-selector" />,
}));
vi.mock("@/components/RolePromptEditor", () => ({
  RolePromptEditor: () => <div data-testid="role-prompt-editor" />,
}));

afterEach(() => cleanup());

function props(): ComponentProps<typeof RiskControls> {
  return {
    profile: {
      style: "长期持有",
      horizon: "半年至一年",
      max_drawdown_percent: 8,
      concentration_limit_percent: 35,
      expected_investment_amount: 30_000,
      prefer_dca: true,
      avoid_chasing: true,
      decision_style: "conservative",
    },
    analysisMode: "deep",
    rolePrompt: "默认角色",
    isRolePromptCustom: false,
    onAnalysisModeChange: vi.fn(),
    onChange: vi.fn(),
    onRolePromptChange: vi.fn(),
    onRolePromptReset: vi.fn(),
    onAnalyze: vi.fn(),
    isBusy: false,
    ocrWarningCount: 0,
    hasBlockingErrors: false,
  };
}

it("shows full generation controls when there is no completed report", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  const rolePromptTrigger = screen.getByRole("button", { name: /AI 角色设定（高级）/ });
  expect(rolePromptTrigger).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByTestId("role-prompt-editor")).not.toBeInTheDocument();
  fireEvent.click(rolePromptTrigger);
  expect(screen.getByTestId("role-prompt-editor")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成今日操作建议" })).toBeInTheDocument();
});

it("collapses to a reading summary when a report exists", () => {
  render(<RiskControls {...props()} readingModeKey="report-1" />);
  expect(screen.getByText("本次生成设置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /AI 角色设定（高级）/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新生成" })).toBeInTheDocument();
});

it("opens settings and collapses again for a new report id", () => {
  const view = render(<RiskControls {...props()} readingModeKey="report-1" />);
  fireEvent.click(screen.getByRole("button", { name: "调整设置" }));
  expect(screen.getByRole("button", { name: /AI 角色设定（高级）/ })).toBeInTheDocument();
  view.rerender(<RiskControls {...props()} readingModeKey="report-2" />);
  expect(screen.queryByRole("button", { name: /AI 角色设定（高级）/ })).not.toBeInTheDocument();
});

it("lets readers collapse settings without regenerating the report", () => {
  render(<RiskControls {...props()} readingModeKey="report-1" />);
  fireEvent.click(screen.getByRole("button", { name: "调整设置" }));
  expect(screen.getByRole("button", { name: /AI 角色设定（高级）/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "收起设置" }));
  expect(screen.getByText("本次生成设置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /AI 角色设定（高级）/ })).not.toBeInTheDocument();
});

it("shows a clickable label for the DCA preference", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
  expect(screen.getByRole("checkbox", { name: "偏好定投" })).toBeInTheDocument();
});
