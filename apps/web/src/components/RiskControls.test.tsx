// @vitest-environment jsdom
import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { RiskControls } from "@/components/RiskControls";

vi.mock("@/components/RolePromptEditor", () => ({
  RolePromptEditor: () => <div data-testid="role-prompt-editor" />,
}));

afterEach(() => cleanup());

function props(): ComponentProps<typeof RiskControls> {
  return {
    profile: {
      max_drawdown_percent: 8,
      concentration_limit_percent: 35,
      expected_investment_amount: 30_000,
      prefer_dca: true,
      avoid_chasing: true,
      hold_days_target: 7,
    },
    rolePrompt: "默认角色",
    isRolePromptCustom: false,
    onChange: vi.fn(),
    onRolePromptChange: vi.fn(),
    onRolePromptReset: vi.fn(),
    onAnalyze: vi.fn(),
    isBusy: false,
    hasBlockingErrors: false,
    blockingMessage: null,
  };
}

it("shows full generation controls when there is no completed report", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  const rolePromptTrigger = screen.getByRole("button", { name: /AI 分析偏好附录（高级）/ });
  expect(rolePromptTrigger).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByTestId("role-prompt-editor")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "快速 · Flash" })).not.toBeInTheDocument();
  fireEvent.click(rolePromptTrigger);
  expect(screen.getByTestId("role-prompt-editor")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成今日操作建议" })).toBeInTheDocument();
});

it("collapses to a reading summary when a report exists", () => {
  render(<RiskControls {...props()} readingModeKey="report-1" />);
  expect(screen.getByText("本次生成设置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /AI 分析偏好附录（高级）/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新生成" })).toBeInTheDocument();
});

it("opens settings and collapses again for a new report id", () => {
  const view = render(<RiskControls {...props()} readingModeKey="report-1" />);
  fireEvent.click(screen.getByRole("button", { name: "调整设置" }));
  expect(screen.getByRole("button", { name: /AI 分析偏好附录（高级）/ })).toBeInTheDocument();
  view.rerender(<RiskControls {...props()} readingModeKey="report-2" />);
  expect(screen.queryByRole("button", { name: /AI 分析偏好附录（高级）/ })).not.toBeInTheDocument();
});

it("lets readers collapse settings without regenerating the report", () => {
  render(<RiskControls {...props()} readingModeKey="report-1" />);
  fireEvent.click(screen.getByRole("button", { name: "调整设置" }));
  expect(screen.getByRole("button", { name: /AI 分析偏好附录（高级）/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "收起设置" }));
  expect(screen.getByText("本次生成设置")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /AI 分析偏好附录（高级）/ })).not.toBeInTheDocument();
});

it("shows a clickable label for the DCA preference", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
  expect(screen.getByRole("checkbox", { name: "偏好定投" })).toBeInTheDocument();
});

it("shows the current stream stage while generating a report", () => {
  render(
    <RiskControls
      {...props()}
      readingModeKey="report-1"
      isBusy
      busyLabel="正在审校报告…"
    />,
  );
  expect(screen.getByTestId("report-generate-stage")).toHaveTextContent(
    "正在审校报告…",
  );
  expect(screen.getByRole("button", { name: "正在审校报告…" })).toBeDisabled();
});

it("shows the daily scan track while a job is running", () => {
  render(
    <RiskControls
      {...props()}
      readingModeKey="report-1"
      isBusy
      scanProgress={{
        stage: "generating",
        stageLabel: "正在生成 AI 日报…",
        status: "running",
      }}
    />,
  );
  expect(screen.getByTestId("analysis-scan-progress")).toBeInTheDocument();
  expect(screen.getByLabelText(/日报航线/)).toBeInTheDocument();
  expect(screen.getByText("Daily Chart · 日报航线")).toBeInTheDocument();
  expect(screen.getByTestId("analysis-scan-step-generating")).toHaveAttribute(
    "data-state",
    "current",
  );
  expect(screen.getByRole("button", { name: "停止生成" })).toBeInTheDocument();
});

it("disables generate while discovery is running", () => {
  const onAnalyze = vi.fn();
  render(
    <RiskControls
      {...props()}
      onAnalyze={onAnalyze}
      peerBusyMessage="发现基金正在扫描，完成后即可生成日报。"
    />,
  );

  expect(screen.getByRole("status")).toHaveTextContent(
    "发现基金正在扫描，完成后即可生成日报。",
  );
  expect(screen.getByTestId("analyze")).toBeDisabled();
  fireEvent.click(screen.getByTestId("analyze"));
  expect(onAnalyze).not.toHaveBeenCalled();
});

it("shows only actionable blocking details instead of a generic review count", () => {
  render(
    <RiskControls
      {...props()}
      hasBlockingErrors
      blockingMessage="持仓数据异常：当日收益额与当日收益率符号不一致。"
      readingModeKey={null}
    />,
  );

  expect(
    screen.getByRole("alert"),
  ).toHaveTextContent("持仓数据异常：当日收益额与当日收益率符号不一致。");
  expect(screen.queryByText(/识别待核对/)).not.toBeInTheDocument();
  expect(screen.getByTestId("analyze")).toBeDisabled();
});
