type VisibilityTarget = Pick<
  Document,
  "visibilityState" | "addEventListener" | "removeEventListener"
>;

type IntervalTarget = Pick<Window, "setInterval" | "clearInterval">;

export type VisibilityPollTickReason = "interval" | "visible";

type VisibilityPollingOptions = {
  documentTarget?: VisibilityTarget;
  intervalMs: number;
  onTick: (reason: VisibilityPollTickReason) => void;
  windowTarget?: IntervalTarget;
};

/**
 * Runs polling only while the page is visible. The first visible mount keeps the
 * normal interval cadence; returning from the background triggers one immediate
 * catch-up tick before restarting that cadence.
 */
export function startVisibilityAwarePolling({
  documentTarget = document,
  intervalMs,
  onTick,
  windowTarget = window,
}: VisibilityPollingOptions): () => void {
  let disposed = false;
  let timer: number | null = null;

  const stopTimer = () => {
    if (timer === null) {
      return;
    }
    windowTarget.clearInterval(timer);
    timer = null;
  };

  const runTick = (reason: VisibilityPollTickReason) => {
    if (disposed || documentTarget.visibilityState !== "visible") {
      return;
    }
    onTick(reason);
  };

  const startTimer = () => {
    if (disposed || timer !== null || documentTarget.visibilityState !== "visible") {
      return;
    }
    timer = windowTarget.setInterval(() => runTick("interval"), intervalMs);
  };

  const handleVisibilityChange = () => {
    if (documentTarget.visibilityState !== "visible") {
      stopTimer();
      return;
    }
    runTick("visible");
    startTimer();
  };

  documentTarget.addEventListener("visibilitychange", handleVisibilityChange);
  startTimer();

  return () => {
    disposed = true;
    stopTimer();
    documentTarget.removeEventListener("visibilitychange", handleVisibilityChange);
  };
}
