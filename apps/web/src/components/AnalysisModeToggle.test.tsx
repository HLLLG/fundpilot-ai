// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { AnalysisModeToggle } from "@/components/AnalysisModeToggle";

afterEach(() => cleanup());

it("describes compact fast mode as Flash with fewer prefetched topics", () => {
  const onChange = vi.fn();
  render(<AnalysisModeToggle mode="fast" onChange={onChange} compact />);

  expect(screen.getByText("Flash 模型 · 较少主题预取")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "快速 · Flash" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  fireEvent.click(screen.getByRole("button", { name: "深度 · Pro" }));
  expect(onChange).toHaveBeenCalledWith("deep");
});

it("describes compact deep mode as bounded Pro analysis with an optional risk review", () => {
  render(<AnalysisModeToggle mode="deep" onChange={vi.fn()} compact />);

  expect(
    screen.getByText("Pro 模型 · 有界扩展证据 · 可选风控审校"),
  ).toBeInTheDocument();
  expect(screen.queryByText(/自主|联网|工具检索/)).not.toBeInTheDocument();
});

it("shows truthful model and evidence descriptions in the full-size control", () => {
  render(<AnalysisModeToggle mode="deep" onChange={vi.fn()} />);

  expect(
    screen.getByRole("button", { name: "快速 · Flash：较少主题预取" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "深度 · Pro：有界扩展证据 · 可选风控审校" }),
  ).toHaveAttribute("aria-pressed", "true");
});
