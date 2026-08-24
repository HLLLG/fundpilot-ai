/** Shared timing / buffer helpers. Main report generation is async Job, not SSE. */

export function streamTimestamp(): number {
  return Date.now();
}

export const STREAM_TOKEN_BUFFER_MAX = 2048;

export function appendStreamTokenBuffer(prev: string, chunk: string): string {
  const next = prev + chunk;
  if (next.length <= STREAM_TOKEN_BUFFER_MAX) {
    return next;
  }
  return next.slice(-STREAM_TOKEN_BUFFER_MAX);
}
