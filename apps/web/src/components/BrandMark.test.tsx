// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";

import { BrandMark } from "@/components/BrandMark";


afterEach(cleanup);


describe("BrandMark", () => {
  it("renders the Lingxi Chinese and English names", () => {
    render(<BrandMark showEnglish />);

    expect(screen.getByText("灵析")).toBeInTheDocument();
    expect(screen.getByText("LINGXI")).toBeInTheDocument();
    expect(screen.queryByText("好基灵")).not.toBeInTheDocument();
    expect(screen.queryByText("FundPilot")).not.toBeInTheDocument();
  });
});
