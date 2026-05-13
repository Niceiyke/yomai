import type { RequestLogEntry } from "../types";

export function renderRequestLog(
  container: HTMLElement,
  entries: RequestLogEntry[],
  onSelect: (entry: RequestLogEntry) => void,
  selectedId: string | null,
): void {
  if (entries.length === 0) {
    container.innerHTML = '<div class="text-gray-500 text-xs p-2">No requests yet</div>';
    return;
  }

  let html = "";
  for (const entry of entries) {
    const isSelected = entry.id === selectedId;
    const bg = isSelected ? "bg-blue-900/40" : "hover:bg-gray-800";
    const statusIcon =
      entry.status === "running" ? "\u23F3" : entry.status === "error" ? "\u274C" : "\u2705";
    const time = new Date(entry.timestamp).toLocaleTimeString();

    html += `<div class="px-3 py-2 text-xs ${bg} cursor-pointer border-b border-gray-800 flex items-center gap-2" 
      data-req-id="${entry.id}">
      <span>${statusIcon}</span>
      <span class="text-gray-400">${time}</span>
      <span class="text-blue-400">${entry.path}</span>
    </div>`;
  }

  container.innerHTML = html;

  for (const entry of entries) {
    const el = container.querySelector(`[data-req-id="${entry.id}"]`);
    if (el) {
      el.addEventListener("click", () => onSelect(entry));
    }
  }
}
