// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ReportNavigator } from "@/components/ReportNavigator";

afterEach(cleanup);

it("exposes previous, next, today and history as direct report-workflow actions", () => {
  const onPrevious = vi.fn();
  const onNext = vi.fn();
  const onToday = vi.fn();
  const onOpenHistory = vi.fn();
  render(
    <ReportNavigator
      currentReport={null}
      reportCount={42}
      currentLabel="历史日报 · 2026-07-10"
      currentStatus="风险等级 medium"
      hasPrevious
      hasNext
      canReturnToday
      onPrevious={onPrevious}
      onNext={onNext}
      onToday={onToday}
      onOpenHistory={onOpenHistory}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "上一份日报" }));
  fireEvent.click(screen.getByRole("button", { name: "下一份日报" }));
  fireEvent.click(screen.getByRole("button", { name: "回到今日" }));
  fireEvent.click(screen.getByRole("button", { name: /全部历史/ }));

  expect(onPrevious).toHaveBeenCalledTimes(1);
  expect(onNext).toHaveBeenCalledTimes(1);
  expect(onToday).toHaveBeenCalledTimes(1);
  expect(onOpenHistory).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("日报导航器")).toHaveTextContent("42");
});
