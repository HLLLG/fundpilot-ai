export type AgentJobKind = "analysis" | "discovery";

export type AgentJobStarted = {
  jobKind: AgentJobKind;
  jobId: string;
};

type Listener = (event: AgentJobStarted) => void;

const listeners = new Set<Listener>();

export function emitAgentJobStarted(event: AgentJobStarted): void {
  const jobId = event.jobId.trim();
  if (!jobId) {
    return;
  }
  if (event.jobKind !== "analysis" && event.jobKind !== "discovery") {
    return;
  }
  for (const listener of listeners) {
    listener({ jobKind: event.jobKind, jobId });
  }
}

export function subscribeAgentJobStarted(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
