import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

describe("Dashboard apply refresh flow", () => {
  it("keeps the report scroll target below the sticky account header", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );

    expect(source).toContain("ref={reportSectionRef}");
    expect(source).toContain('aria-label="日报阅读区"');
    expect(source).toContain('className="min-w-0 scroll-mt-24 outline-none"');
  });

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

  it("keeps the OCR draft visible until persistence succeeds", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const ocrHandler = source.slice(
      source.indexOf("const handleConfirmOcrHoldings"),
      source.indexOf("const handleDeleteHolding"),
    );
    const persistIndex = ocrHandler.indexOf("await enqueuePortfolioMutation");

    expect(persistIndex).toBeGreaterThan(-1);
    expect(ocrHandler.indexOf("setPendingOcrHoldings(null);")).toBeGreaterThan(persistIndex);
    expect(ocrHandler.indexOf("setHoldings(appliedHoldings);")).toBeGreaterThan(persistIndex);
    expect(ocrHandler).toContain("setOcrApplyError(errorMessage);");
  });

  it("swaps the transaction review for the upload dialog without stacking both", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );

    expect(source).toContain("{pendingTransactions && !showBatchModal ? (");
    expect(source).toContain("setPendingTransactions((prev) => mergeTransactions(prev ?? [], result.transactions))");
  });

  it("loads interaction-only panels, drawers, and modal components on demand", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );

    for (const componentName of [
      "YangjibaoFundDetail",
      "AddHoldingModal",
      "LedgerBaselineModal",
      "BatchTransactionModal",
      "BatchTransactionConfirmModal",
      "AlipayOcrConfirmModal",
      "ReportDiagnostics",
      "ReportHistoryDrawer",
    ]) {
      expect(source).toMatch(
        new RegExp(
          `import\\("@/components/${componentName}"\\)[\\s\\S]*?module\\.${componentName}`,
        ),
      );
    }
    expect(source).not.toContain('import { YangjibaoFundDetail');
    for (const moduleName of [
      "AddHoldingModal",
      "LedgerBaselineModal",
      "BatchTransactionModal",
      "BatchTransactionConfirmModal",
      "AlipayOcrConfirmModal",
      "ReportDiagnostics",
      "ReportHistoryDrawer",
    ]) {
      expect(source).not.toContain(`from "@/components/${moduleName}"`);
    }
    for (const moduleName of [
      "MarketBreadthGauge",
      "NewsPreviewPanel",
      "RecommendationAccuracyPanel",
      "SectorSignalBacktestPanel",
      "ShadowEscalationDigestCard",
    ]) {
      expect(source).not.toContain(`from "@/components/${moduleName}"`);
    }

    expect(source).toContain("{selectedHoldingIndex !== null && holdings[selectedHoldingIndex] ? (");
    expect(source).toContain("{showAddHoldingModal ? (");
    expect(source).toContain("{showLedgerBaselineModal ? (");
    expect(source).toContain("{showBatchModal ? (");
    expect(source).toContain("{pendingTransactions && !showBatchModal ? (");
    expect(source).toContain("{pendingOcrHoldings ? (");
    expect(source).toContain("{reportHistoryOpen ? (");
    expect(source).toContain("diagnostics={() => (");
    expect(source).toContain("<ReportDiagnostics");

    const loadingFallback = source.slice(
      source.indexOf("function DeferredInteractionLoading"),
      source.indexOf("const PortfolioDashboard"),
    );
    expect(loadingFallback).toContain('role="status"');
    expect(loadingFallback).not.toContain('role="dialog"');
    expect(loadingFallback).not.toContain("aria-modal");
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
      source.indexOf("const handleSingleFundTransaction"),
    );
    const fundCodeUpdateHandler = source.slice(
      source.indexOf("onFundCodeUpdated={async"),
      source.indexOf("onHoldingResolved=", source.indexOf("onFundCodeUpdated={async")),
    );
    const singleTransactionHandler = source.slice(
      source.indexOf("const handleSingleFundTransaction"),
      source.indexOf("const handleAdjustHolding"),
    );
    const adjustHoldingHandler = source.slice(
      source.indexOf("const handleAdjustHolding"),
      source.indexOf("return (", source.indexOf("const handleAdjustHolding")),
    );

    for (const handler of [
      manualAddHandler,
      transactionApplyHandler,
      fundCodeUpdateHandler,
      singleTransactionHandler,
      adjustHoldingHandler,
    ]) {
      expect(handler).toContain("holdingsMutationVersionRef.current += 1;");
      expect(handler).toContain("const mutationVersion = holdingsMutationVersionRef.current;");
      expect(handler).toContain("if (mutationVersion !== holdingsMutationVersionRef.current)");
    }
  });

  it("routes single-fund writes through the queue without a second whole-portfolio write", () => {
    const source = readFileSync(
      fileURLToPath(new URL("./Dashboard.tsx", import.meta.url)),
      "utf8",
    );
    const singleTransactionHandler = source.slice(
      source.indexOf("const handleSingleFundTransaction"),
      source.indexOf("const handleAdjustHolding"),
    );
    const adjustHoldingHandler = source.slice(
      source.indexOf("const handleAdjustHolding"),
      source.indexOf("return (", source.indexOf("const handleAdjustHolding")),
    );

    expect(singleTransactionHandler).toContain(
      "enqueuePortfolioMutation(() => applyTransactions([transaction]))",
    );
    expect(singleTransactionHandler).not.toContain("applyPortfolioHoldings");
    expect(adjustHoldingHandler).toContain(
      "enqueuePortfolioMutation(() => adjustHolding(fundCode, patch))",
    );
    expect(adjustHoldingHandler).not.toContain("applyPortfolioHoldings");
    expect(source).toContain("onAdjustHolding={handleAdjustHolding}");
    expect(source).toContain("onApplyTransaction={handleSingleFundTransaction}");
    expect(source).not.toContain("onPortfolioUpdated=");
  });

  it("keeps mutation APIs out of the single-fund modal components", () => {
    const transactionModalSource = readFileSync(
      fileURLToPath(new URL("./SingleFundTransactionModal.tsx", import.meta.url)),
      "utf8",
    );
    const modifyModalSource = readFileSync(
      fileURLToPath(new URL("./HoldingModifyModal.tsx", import.meta.url)),
      "utf8",
    );
    const detailSource = readFileSync(
      fileURLToPath(new URL("./YangjibaoFundDetail.tsx", import.meta.url)),
      "utf8",
    );

    expect(transactionModalSource).not.toContain("applyTransactions(");
    expect(modifyModalSource).not.toContain("adjustHolding(");
    expect(transactionModalSource).toContain("await onSubmit(tx);");
    expect(modifyModalSource).toContain("await onSubmit({");
    expect(detailSource).toContain("refreshDetailAfterPortfolioMutation");
    expect(detailSource).toContain("持仓已更新，但最新详情暂时无法刷新");
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
