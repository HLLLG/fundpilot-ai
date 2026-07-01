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

  it("hydrates an empty holdings payload instead of leaving stale rows mounted", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const hydratePortfolio = source.slice(
      source.indexOf("const hydratePortfolio"),
      source.indexOf("useLayoutEffect(() => {", source.indexOf("const hydratePortfolio")),
    );

    const setHoldingsIndex = hydratePortfolio.indexOf("setHoldings(payload.holdings);");
    const nonEmptyGuardIndex = hydratePortfolio.indexOf("if (payload.holdings.length > 0)");

    expect(setHoldingsIndex).toBeGreaterThan(-1);
    expect(nonEmptyGuardIndex).toBeGreaterThan(-1);
    expect(setHoldingsIndex).toBeLessThan(nonEmptyGuardIndex);
  });

  it("ignores stale holdings responses after a local holdings mutation", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const hydratePortfolio = source.slice(
      source.indexOf("const hydratePortfolio"),
      source.indexOf("useLayoutEffect(() => {", source.indexOf("const hydratePortfolio")),
    );
    const deleteHandler = source.slice(
      source.indexOf("const handleDeleteHolding"),
      source.indexOf("const mergeTransactions", source.indexOf("const handleDeleteHolding")),
    );
    const settleHandler = source.slice(
      source.indexOf("const settleOfficialNavInBackground"),
      source.indexOf("const hydratePortfolio"),
    );

    expect(source).toContain("holdingsMutationVersionRef");
    expect(hydratePortfolio).toContain("const requestVersion = holdingsMutationVersionRef.current;");
    expect(hydratePortfolio).toContain("if (requestVersion !== holdingsMutationVersionRef.current)");
    expect(deleteHandler).toContain("holdingsMutationVersionRef.current += 1;");
    expect(settleHandler).toContain("const requestVersion = holdingsMutationVersionRef.current;");
    expect(settleHandler).toContain("if (requestVersion !== holdingsMutationVersionRef.current)");
  });

  it("ignores stale OCR apply responses after a newer OCR confirmation", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const ocrHandler = source.slice(
      source.indexOf("const handleConfirmOcrHoldings"),
      source.indexOf("const handleDeleteHolding"),
    );

    expect(ocrHandler).toContain("holdingsMutationVersionRef.current += 1;");
    expect(ocrHandler).toContain("const mutationVersion = holdingsMutationVersionRef.current;");
    expect(ocrHandler).toContain("if (mutationVersion !== holdingsMutationVersionRef.current)");
    expect(ocrHandler.indexOf("if (mutationVersion !== holdingsMutationVersionRef.current)")).toBeLessThan(
      ocrHandler.indexOf("setHoldings(appliedHoldings);"),
    );
  });

  it("versions all portfolio persistence entrypoints that can race", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const manualAddHandler = source.slice(
      source.indexOf("const handleManualAddHoldings"),
      source.indexOf("const handleConfirmOcrHoldings"),
    );
    const transactionApplyHandler = source.slice(
      source.indexOf("const handleApplyTransactions"),
      source.indexOf("return (", source.indexOf("const handleApplyTransactions")),
    );
    const fundCodeUpdateHandler = source.slice(
      source.indexOf("onFundCodeUpdated={async"),
      source.indexOf("onHoldingResolved=", source.indexOf("onFundCodeUpdated={async")),
    );
    const portfolioUpdatedHandler = source.slice(
      source.indexOf("onPortfolioUpdated={async"),
      source.indexOf("</YangjibaoFundDetail>", source.indexOf("onPortfolioUpdated={async")),
    );

    for (const handler of [
      manualAddHandler,
      transactionApplyHandler,
      fundCodeUpdateHandler,
      portfolioUpdatedHandler,
    ]) {
      expect(handler).toContain("holdingsMutationVersionRef.current += 1;");
      expect(handler).toContain("const mutationVersion = holdingsMutationVersionRef.current;");
      expect(handler).toContain("if (mutationVersion !== holdingsMutationVersionRef.current)");
    }
  });

  it("does not persist the initial empty holdings before cache is ready", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const cacheEffect = source.slice(
      source.indexOf("useEffect(() => {\n    if (!portfolioCacheWriteReadyRef.current)"),
      source.indexOf("useEffect(() => {\n    if (!sectorRefresh.lastFetchedAt)", source.indexOf("if (!portfolioCacheWriteReadyRef.current")),
    );

    expect(source).toContain("portfolioCacheWriteReadyRef");
    expect(cacheEffect).toContain("if (!portfolioCacheWriteReadyRef.current)");
    expect(cacheEffect.indexOf("if (!portfolioCacheWriteReadyRef.current)")).toBeLessThan(
      cacheEffect.indexOf("saveCachedPortfolioHoldings(user?.id"),
    );
  });

  it("serializes holdings persistence calls before they reach the server", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );

    expect(source).toContain("portfolioMutationQueueRef");
    expect(source).toContain("const enqueuePortfolioMutation");
    expect(source).toContain("enqueuePortfolioMutation(() => applyPortfolioHoldings(");
    expect(source).not.toContain("await applyPortfolioHoldings(");
  });
});
