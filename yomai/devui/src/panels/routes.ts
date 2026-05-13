import type { RouteMeta } from "../types";

export function renderRoutePanel(container: HTMLElement, routes: RouteMeta[]): void {
  let html = '<div class="p-3"><div class="text-xs font-semibold text-gray-400 mb-2">Routes</div>';

  for (const route of routes) {
    const typeBadge =
      route.type === "agent"
        ? "bg-blue-900 text-blue-300"
        : route.type === "workflow"
          ? "bg-purple-900 text-purple-300"
          : "bg-gray-800 text-gray-400";

    html += `<div class="mb-3 p-2 bg-gray-900 rounded-lg border border-gray-800">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-xs px-1.5 py-0.5 rounded ${typeBadge}">${route.type}</span>
        <span class="text-xs text-gray-300 font-mono">POST ${route.path}</span>
      </div>`;

    if (route.tools?.length) {
      html += `<div class="text-xs text-gray-500">tools: ${route.tools.join(", ")}</div>`;
    }

    if (route.body_params?.length) {
      html += `<div class="text-xs text-gray-500">body: ${route.body_params.join(", ")}</div>`;
    }

    html += "</div>";
  }

  html += "</div>";
  container.innerHTML = html;
}
