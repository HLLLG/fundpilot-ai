// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

import { MarketFetchNotice } from "@/components/MarketTab";

afterEach(cleanup);

describe("MarketFetchNotice", () => {
  it("distinguishes a first-load failure from stale data and offers retry", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <MarketFetchNotice error="行情服务超时" hasData={false} onRetry={onRetry} />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("市场数据加载失败：行情服务超时");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);

    rerender(<MarketFetchNotice error="行情服务超时" hasData onRetry={onRetry} />);
    expect(screen.getByRole("status")).toHaveTextContent("继续显示上次数据");
  });

  it("renders nothing when there is no failure", () => {
    const { container } = render(
      <MarketFetchNotice error={null} hasData={false} onRetry={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
