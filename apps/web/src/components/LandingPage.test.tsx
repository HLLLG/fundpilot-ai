// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { LandingPage } from "@/components/LandingPage";
import { SITE_REGISTRATION } from "@/lib/brand";
import { OCR_PRIVACY_COPY } from "@/lib/ocrPrivacy";

afterEach(cleanup);

it("states the real configurable OCR privacy boundary", () => {
  render(<LandingPage />);
  expect(screen.getByText(OCR_PRIVACY_COPY.trustTitle)).toBeInTheDocument();
  expect(screen.getByText(OCR_PRIVACY_COPY.trustDescription)).toBeInTheDocument();
  expect(screen.getByText("服务端持仓按账号隔离")).toBeInTheDocument();
  expect(screen.getByText(/保存到服务端的持仓按登录账号隔离/)).toBeInTheDocument();
  expect(screen.queryByText("不上传原始截图")).not.toBeInTheDocument();
  expect(screen.queryByText("截图仅在本地识别，发往 AI 的只有结构化的持仓与行情摘要。")).not.toBeInTheDocument();
  expect(screen.queryByText(/本地模式不会上传截图/)).not.toBeInTheDocument();
  expect(screen.queryByText(/仅在本机处理/)).not.toBeInTheDocument();
  expect(screen.getByText(/截图会发送到本应用服务端/)).toBeInTheDocument();
});

it("labels illustrative market data and avoids invented testimonials", () => {
  render(<LandingPage />);
  expect(screen.getByText("界面示意 · 非实时数据")).toBeInTheDocument();
  expect(screen.getByText("典型使用方式")).toBeInTheDocument();
  expect(screen.queryByText("真实场景")).not.toBeInTheDocument();
  expect(screen.queryByText("易方达蓝筹精选")).not.toBeInTheDocument();
});

it("keeps one primary action in the hero and exposes login as an account utility", () => {
  render(<LandingPage />);

  const hero = screen.getByTestId("landing-hero");
  const primaryAction = within(hero).getByRole("link", { name: "免费开始使用" });

  expect(within(hero).getAllByRole("link")).toHaveLength(1);
  expect(primaryAction).toHaveAttribute("href", "/register");
  expect(primaryAction).toHaveClass("min-h-11");
  expect(within(screen.getByRole("navigation", { name: "账号入口" })).getByRole("link", { name: "登录" }))
    .toHaveAttribute("href", "/login");
});

it("uses truthful proof points and editorial section separators", () => {
  render(<LandingPage />);

  expect(within(screen.getByTestId("landing-proof-strip")).getByText("写入前校对")).toBeInTheDocument();
  expect(screen.queryByText("0 手动录入")).not.toBeInTheDocument();
  expect(screen.getByText(/基础能力当前可免费使用/)).toBeInTheDocument();
  expect(screen.queryByText(/永久免费/)).not.toBeInTheDocument();

  for (const testId of ["landing-steps", "landing-features", "landing-trust"]) {
    const section = screen.getByTestId(testId);
    expect(section).toHaveAttribute("data-layout", "editorial");
    expect(section).not.toHaveClass("section-card");
  }
});

it("shows the registered website name and linked ICP record in the public footer", () => {
  render(<LandingPage />);

  const registration = screen.getByLabelText("网站备案信息");
  expect(within(registration).getByText(SITE_REGISTRATION.registeredSiteName)).toBeInTheDocument();
  expect(within(registration).getByRole("link", { name: SITE_REGISTRATION.icpRecordNumber }))
    .toHaveAttribute("href", SITE_REGISTRATION.icpQueryUrl);
});
