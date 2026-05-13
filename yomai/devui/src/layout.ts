import dagre from "dagre";
import type { GraphEdge, GraphNode } from "./types";

const NODE_WIDTH = 180;
const NODE_HEIGHT = 48;
const HORIZONTAL_SPACING = 60;
const VERTICAL_SPACING = 80;

export interface LayoutNode extends GraphNode {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): LayoutNode[] {
  if (nodes.length === 0) return [];

  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: HORIZONTAL_SPACING,
    ranksep: VERTICAL_SPACING,
    marginx: 30,
    marginy: 30,
  });
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of nodes) {
    const w = node.kind === "parallel" ? NODE_WIDTH * 1.5 : NODE_WIDTH;
    g.setNode(node.id, { width: w, height: NODE_HEIGHT });
  }

  for (const edge of edges) {
    g.setEdge(edge.from, edge.to);
  }

  dagre.layout(g);

  return nodes.map((node) => {
    const pos = g.node(node.id);
    const w = node.kind === "parallel" ? NODE_WIDTH * 1.5 : NODE_WIDTH;
    if (!pos) {
      return { ...node, x: 0, y: 0, width: w, height: NODE_HEIGHT };
    }
    return {
      ...node,
      x: pos.x - w / 2,
      y: pos.y - NODE_HEIGHT / 2,
      width: w,
      height: NODE_HEIGHT,
    };
  });
}
