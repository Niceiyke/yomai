import type { GraphAction, GraphEdge, GraphNode } from "./types";

export class GraphStore {
  private nodes = new Map<string, GraphNode>();
  private edges: GraphEdge[] = [];
  private listeners: Array<() => void> = [];

  apply(action: GraphAction): void {
    switch (action.action) {
      case "upsert": {
        if (!action.id || !action.kind) return;
        const existing = this.nodes.get(action.id);
        const node: GraphNode = {
          id: action.id,
          label: action.label ?? existing?.label ?? action.id,
          kind: action.kind as GraphNode["kind"],
          status: (action.status as GraphNode["status"]) ?? existing?.status ?? "running",
          parent: action.parent ?? existing?.parent,
          meta: { ...existing?.meta, ...action.meta },
        };
        this.nodes.set(action.id, node);
        break;
      }
      case "edge": {
        if (!action.from || !action.to) return;
        const exists = this.edges.some(
          (e) => e.from === action.from && e.to === action.to
        );
        if (!exists) {
          this.edges.push({ from: action.from, to: action.to, label: action.label ?? "" });
        }
        break;
      }
      case "update": {
        if (!action.id) return;
        const node = this.nodes.get(action.id);
        if (node) {
          if (action.status) node.status = action.status as GraphNode["status"];
          if (action.meta) node.meta = { ...node.meta, ...action.meta };
        }
        break;
      }
      case "clear": {
        this.nodes.clear();
        this.edges = [];
        break;
      }
    }
    this.notify();
  }

  getNodes(): GraphNode[] {
    return Array.from(this.nodes.values());
  }

  getEdges(): GraphEdge[] {
    return this.edges;
  }

  getNode(id: string): GraphNode | undefined {
    return this.nodes.get(id);
  }

  isEmpty(): boolean {
    return this.nodes.size === 0;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  private notify(): void {
    for (const l of this.listeners) l();
  }
}
