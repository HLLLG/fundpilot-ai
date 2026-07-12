// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import "@testing-library/jest-dom/vitest";

import { WheelDatePicker } from "@/components/WheelDatePicker";

function Harness() {
  const [value, setValue] = useState("2025-06-15");
  return (
    <>
      <output data-testid="date-value">{value}</output>
      <WheelDatePicker value={value} max="2026-07-11" minYear={2024} onChange={setValue} />
    </>
  );
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WheelDatePicker keyboard support", () => {
  it("exposes named spinbuttons and changes the date with standard keys", () => {
    render(<Harness />);

    const year = screen.getByRole("spinbutton", { name: "年份" });
    const month = screen.getByRole("spinbutton", { name: "月份" });
    const day = screen.getByRole("spinbutton", { name: "日期" });
    expect(year).toHaveAttribute("aria-valuetext", "2025年");
    expect(month).toHaveAttribute("aria-valuetext", "6月");
    expect(day).toHaveAttribute("aria-valuetext", "15日");

    fireEvent.keyDown(year, { key: "ArrowDown" });
    expect(screen.getByTestId("date-value")).toHaveTextContent("2026-06-15");

    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "月份" }), {
      key: "ArrowDown",
    });
    expect(screen.getByTestId("date-value")).toHaveTextContent("2026-07-11");

    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "年份" }), { key: "Home" });
    expect(screen.getByTestId("date-value")).toHaveTextContent("2024-07-11");
  });
});
