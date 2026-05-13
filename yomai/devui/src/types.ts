export interface GraphNode {
  id: string;
  label: string;
  kind: "user_msg" | "llm" | "tool" | "step" | "parallel" | "response" | "checkpoint" | "error";
  status: "pending" | "running" | "done" | "error";
  parent?: string;
  meta?: Record<string, unknown>;
}

export interface GraphEdge {
  from: string;
  to: string;
  label: string;
}

export interface GraphAction {
  action: "upsert" | "edge" | "update" | "clear";
  id?: string;
  label?: string;
  kind?: string;
  status?: string;
  parent?: string;
  meta?: Record<string, unknown>;
  from?: string;
  to?: string;
}

export interface RouteMeta {
  path: string;
  type: string;
  tools: string[];
  body_params: string[];
  params: { name: string; type: string; required: boolean; default?: unknown }[];
  path_params: string[];
  injected_params: string[];
  system?: string;
  tags: string[];
  summary?: string;
  description?: string;
  deprecated?: boolean;
  cors?: Record<string, unknown>;
}

export interface RequestLogEntry {
  id: string;
  path: string;
  timestamp: number;
  status: "running" | "done" | "error";
  sessionId: string;
  events: Array<{ event: string; data: Record<string, unknown> }>;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}
