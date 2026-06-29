import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

describe("Dashboard apply refresh flow", () => {
  it("does not hydrate holdings from server cache immediately after apply", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const marker = 'if (refreshAfterApplyRef.current !== "sector"';
    const start = source.indexOf(marker);
    const end = source.indexOf("}, [holdings]);", start);
    const applyRefreshEffect = start === -1 || end === -1 ? undefined : source.slice(start, end);

    expect(applyRefreshEffect).toBeDefined();
    expect(applyRefreshEffect).not.toContain("hydratePortfolio(");
    expect(applyRefreshEffect).toContain('sectorRefresh.refresh(false, "fast")');
  });

  it("does not trigger sector refresh immediately after OCR confirm", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const ocrHandler = source.slice(
      source.indexOf("const handleConfirmOcrHoldings"),
      source.indexOf("const handleDeleteHolding"),
    );
    expect(ocrHandler).not.toContain('refreshAfterApplyRef.current = "sector"');
  });
});
