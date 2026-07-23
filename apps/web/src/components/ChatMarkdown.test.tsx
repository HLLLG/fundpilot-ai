// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import "@testing-library/jest-dom/vitest";

import { ChatMarkdown } from "@/components/ChatMarkdown";

afterEach(cleanup);

it("lazily renders generated tables as keyboard-scrollable regions", async () => {
  render(<ChatMarkdown content={"| 基金 | 收益 |\n| --- | --- |\n| 示例基金 | +1.2% |"} />);

  const region = await screen.findByRole("region", { name: "对话数据表格，可左右滚动查看" });
  expect(region).toHaveAttribute("tabindex", "0");
  expect(region).toHaveClass("overflow-x-auto");
  expect(region.querySelector("table")).toHaveClass("min-w-max");
});
