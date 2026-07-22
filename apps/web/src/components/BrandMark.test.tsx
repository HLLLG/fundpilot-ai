// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";

import { BrandMark } from "@/components/BrandMark";


afterEach(cleanup);


describe("BrandMark", () => {
  it("renders the registered Chinese and English names", () => {
    render(<BrandMark showEnglish />);

    expect(screen.getByText("数据分析学习笔记")).toBeInTheDocument();
    expect(screen.getByText("DATA ANALYSIS NOTES")).toBeInTheDocument();
    expect(screen.queryByText("好基灵")).not.toBeInTheDocument();
    expect(screen.queryByText("FundPilot")).not.toBeInTheDocument();
  });
});
