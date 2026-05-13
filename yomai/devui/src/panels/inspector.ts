import type { GraphNode } from "../types";
import type { GraphStore } from "../store";

export function renderInspector(
  container: HTMLElement,
  store: GraphStore,
  selectedNodeId: string | null,
): void {
  if (!selectedNodeId) {
    container.innerHTML =
      '<div class="text-gray-500 text-xs p-4">Click a node to inspect</div>';
    return;
  }

  const node = store.getNode(selectedNodeId);
  if (!node) {
    container.innerHTML = '<div class="text-gray-500 text-xs p-4">Node not found</div>';
    return;
  }

  const colors: Record<string, string> = {
    running: "text-blue-400",
    done: "text-green-400",
    error: "text-red-400",
    pending: "text-gray-400",
  };

  const statusColor = colors[node.status] ?? "text-gray-400";
  const meta = node.meta ?? {};

  container.innerHTML = `
    <div class="p-3">
      <div class="text-xs font-semibold text-gray-400 mb-2">Node Inspector</div>
      <div class="space-y-2 text-xs">
        <div><span class="text-gray-500">id:</span> <span class="text-gray-300">${node.id}</span></div>
        <div><span class="text-gray-500">kind:</span> <span class="text-blue-300">${node.kind}</span></div>
        <div><span class="text-gray-500">label:</span> <span class="text-gray-300">${node.label}</span></div>
        <div><span class="text-gray-500">status:</span> <span class="${statusColor}">${node.status}</span></div>
        ${renderMeta(meta)}
      </div>
    </div>`;
}

function renderMeta(meta: Record<string, unknown>): string {
  const rows: string[] = [];
  for (const [key, value] of Object.entries(meta)) {
    const display = typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
    rows.push(
      `<div><span class="text-gray-500">${key}:</span> <span class="text-gray-300">${display}</span></div>`,
    );
  }
  return rows.join("");
}
