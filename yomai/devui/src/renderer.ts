import type { GraphEdge } from "./types";
import type { LayoutNode } from "./layout";

const STATUS_COLORS: Record<string, { fill: string; stroke: string; text: string }> = {
  running: { fill: "#1d4ed8", stroke: "#3b82f6", text: "#bfdbfe" },
  done: { fill: "#166534", stroke: "#22c55e", text: "#bbf7d0" },
  error: { fill: "#991b1b", stroke: "#ef4444", text: "#fecaca" },
  pending: { fill: "#1f2937", stroke: "#4b5563", text: "#9ca3af" },
};

const KIND_ICONS: Record<string, string> = {
  user_msg: "\u{1F4AC}",
  llm: "\u{1F916}",
  tool: "\u{1F527}",
  step: "\u{25B6}",
  parallel: "\u{2225}",
  response: "\u{2705}",
  checkpoint: "\u{1F4BE}",
  error: "\u{274C}",
};

export function renderGraph(
  container: SVGSVGElement,
  nodes: LayoutNode[],
  edges: GraphEdge[],
  onNodeClick: (id: string) => void,
): void {
  const padding = 40;
  let maxX = 0;
  let maxY = 0;

  for (const n of nodes) {
    maxX = Math.max(maxX, n.x + n.width);
    maxY = Math.max(maxY, n.y + n.height);
  }

  const w = maxX + padding * 2;
  const h = maxY + padding * 2;
  container.setAttribute("viewBox", `0 0 ${Math.max(w, 400)} ${Math.max(h, 300)}`);

  let svg = "";

  // Edges
  for (const edge of edges) {
    const from = nodes.find((n) => n.id === edge.from);
    const to = nodes.find((n) => n.id === edge.to);
    if (!from || !to) continue;

    const x1 = from.x + from.width / 2;
    const y1 = from.y + from.height;
    const x2 = to.x + to.width / 2;
    const y2 = to.y;
    const cy = (y1 + y2) / 2;

    svg += `<path d="M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}" 
      fill="none" stroke="#374151" stroke-width="2" marker-end="url(#arrow)"/>`;

    if (edge.label) {
      svg += `<text x="${(x1 + x2) / 2}" y="${cy - 4}" text-anchor="middle" 
        fill="#6b7280" font-size="10">${edge.label}</text>`;
    }
  }

  // Arrow marker
  svg += `<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" 
    markerWidth="6" markerHeight="6" orient="auto">
    <path d="M0,0 L10,5 L0,10 Z" fill="#374151"/></marker></defs>`;

  // Nodes
  for (const node of nodes) {
    const colors = STATUS_COLORS[node.status] ?? STATUS_COLORS.pending;
    const icon = KIND_ICONS[node.kind] ?? "";
    const rx = 8;

    // Node background
    svg += `<rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" 
      rx="${rx}" fill="${colors.fill}" stroke="${colors.stroke}" stroke-width="2"
      class="graph-node ${node.status === 'running' ? 'graph-node-running' : ''}"
      data-node-id="${node.id}" style="cursor:pointer"/>`;

    // Icon + label
    const label = node.label.length > 28 ? node.label.slice(0, 27) + "\u2026" : node.label;
    svg += `<text x="${node.x + 10}" y="${node.y + 30}" fill="${colors.text}" font-size="13" 
      font-family="system-ui,sans-serif">${icon} ${label}</text>`;

    // Meta text (duration or token count)
    if (node.status === "done" && node.meta) {
      const metaText = node.meta.duration_ms
        ? `${node.meta.duration_ms}ms`
        : node.meta.tokens_in != null
          ? `${node.meta.tokens_in}\u2192${node.meta.tokens_out}`
          : "";
      if (metaText) {
        svg += `<text x="${node.x + node.width / 2}" y="${node.y + node.height - 8}" 
          text-anchor="middle" fill="${colors.text}" fill-opacity="0.6" font-size="10">${metaText}</text>`;
      }
    }
  }

  container.innerHTML = svg;

  // Click handlers (must be attached after innerHTML)
  for (const node of nodes) {
    const el = container.querySelector(`[data-node-id="${node.id}"]`);
    if (el) {
      el.addEventListener("click", () => onNodeClick(node.id));
    }
  }
}
