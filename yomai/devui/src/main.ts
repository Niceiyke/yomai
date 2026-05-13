import "./styles.css";
import { GraphStore } from "./store";
import { SSEParser } from "./sse";
import { layoutGraph } from "./layout";
import { renderGraph } from "./renderer";
import { renderRequestLog } from "./panels/request-log";
import { renderInspector } from "./panels/inspector";
import { renderChat, type ChatAPI } from "./panels/chat";
import { renderRoutePanel } from "./panels/routes";
import type { GraphAction, RequestLogEntry, RouteMeta } from "./types";

declare global {
  interface Window {
    __ROUTES__?: RouteMeta[];
  }
}

// --- State ---
const store = new GraphStore();
const parser = new SSEParser();
let routes: RouteMeta[] = [];
let sessionId = crypto.randomUUID();
let selectedNodeId: string | null = null;
let requestLog: RequestLogEntry[] = [];
let currentRequestId: string | null = null;
let abortController: AbortController | null = null;
let selectedRoute: RouteMeta | null = null;
let chatAPI: ChatAPI | null = null;

// --- Build Layout ---
const app = document.getElementById("app")!;
app.innerHTML = `
  <div class="flex h-12 items-center px-4 bg-gray-900 border-b border-gray-800 shrink-0">
    <span class="font-bold text-lg text-blue-400 mr-4">Yomai</span>
    <select id="route-select" class="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 mr-3"></select>
    <span id="session-display" class="text-xs text-gray-500 font-mono mr-3"></span>
    <button id="new-session-btn" class="text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded px-3 py-1.5">New Session</button>
    <span class="flex-1"></span>
    <button id="toggle-chat-btn" class="text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded px-3 py-1.5">Chat</button>
  </div>
  <div class="flex flex-1 min-h-0">
    <div id="left-panel" class="w-64 bg-gray-900 border-r border-gray-800 flex flex-col shrink-0">
      <div id="routes-panel" class="border-b border-gray-800"></div>
      <div class="text-xs font-semibold text-gray-400 px-3 pt-3 pb-1">Requests</div>
      <div id="request-log" class="flex-1 overflow-auto"></div>
    </div>
    <div id="graph-panel" class="flex-1 flex flex-col min-w-0">
      <div id="graph-status" class="text-xs text-gray-500 px-4 py-1 border-b border-gray-800 shrink-0">
        Loading...
      </div>
      <div id="graph-container" class="flex-1 overflow-auto p-4">
        <svg id="graph-svg" class="w-full h-full" viewBox="0 0 400 300"></svg>
      </div>
    </div>
    <div id="right-panel" class="w-64 bg-gray-900 border-l border-gray-800 shrink-0">
      <div id="inspector-panel" class="overflow-auto h-full"></div>
    </div>
  </div>
  <div id="chat-panel" class="hidden border-t border-gray-800 h-64 shrink-0"></div>
`;

const $ = (id: string) => document.getElementById(id)!;
const routeSelect = $("route-select") as HTMLSelectElement;
const sessionDisplay = $("session-display");
const graphStatus = $("graph-status");
const graphSvg = $("graph-svg") as unknown as SVGSVGElement;
const requestLogEl = $("request-log");
const inspectorEl = $("inspector-panel");
const chatPanel = $("chat-panel");
const routesPanel = $("routes-panel");

// --- Init routes ---
function initRoutes(): void {
  // Primary: injected by Python as window.__ROUTES__
  if (window.__ROUTES__ && window.__ROUTES__.length > 0) {
    routes = window.__ROUTES__;
  }

  routeSelect.innerHTML = "";
  for (const r of routes) {
    const opt = document.createElement("option");
    opt.value = r.path;
    opt.textContent = r.type + ": " + r.path;
    routeSelect.appendChild(opt);
  }

  if (routes.length > 0) {
    selectedRoute = routes[0];
    routeSelect.value = selectedRoute.path;
    renderRoutePanel(routesPanel, routes);
    graphStatus.textContent = "Ready: " + selectedRoute.path;
  } else {
    // Fallback: fetch from API
    fetch("/__yomai__/routes")
      .then((res) => res.json())
      .then((data: RouteMeta[]) => {
        routes = data;
        routeSelect.innerHTML = "";
        for (const r of routes) {
          const opt = document.createElement("option");
          opt.value = r.path;
          opt.textContent = r.type + ": " + r.path;
          routeSelect.appendChild(opt);
        }
        if (routes.length > 0) {
          selectedRoute = routes[0];
          routeSelect.value = selectedRoute.path;
          renderRoutePanel(routesPanel, routes);
          graphStatus.textContent = "Ready: " + selectedRoute.path;
        }
      })
      .catch(() => {
        graphStatus.textContent = "No routes found";
      });
  }
}

routeSelect.addEventListener("change", () => {
  selectedRoute = routes.find((r) => r.path === routeSelect.value) ?? null;
  resetGraph();
  if (selectedRoute) {
    graphStatus.textContent = "Ready: " + selectedRoute.path;
  }
  if (!chatPanel.classList.contains("hidden")) {
    renderChatPanel();
  }
});

// --- Session ---
function refreshSessionDisplay(): void {
  sessionDisplay.textContent = sessionId.slice(0, 8) + "...";
}
refreshSessionDisplay();

$("new-session-btn").addEventListener("click", () => {
  sessionId = crypto.randomUUID();
  refreshSessionDisplay();
  resetGraph();
  if (!chatPanel.classList.contains("hidden")) {
    renderChatPanel();
  }
});

// --- Chat toggle ---
$("toggle-chat-btn").addEventListener("click", () => {
  chatPanel.classList.toggle("hidden");
  if (!chatPanel.classList.contains("hidden")) {
    renderChatPanel();
  }
});

function renderChatPanel(): void {
  if (!selectedRoute) return;
  chatAPI = renderChat(chatPanel, selectedRoute, (message: string) => {
    runRequest(message);
  });
}

// --- Run request ---
async function runRequest(message: string): Promise<void> {
  if (!selectedRoute) return;

  resetGraph();
  currentRequestId = crypto.randomUUID();
  const entry: RequestLogEntry = {
    id: currentRequestId,
    path: selectedRoute.path,
    timestamp: Date.now(),
    status: "running",
    sessionId,
    events: [],
  };
  requestLog.unshift(entry);
  refreshRequestLog();

  graphStatus.textContent = "Running " + selectedRoute.path + "...";

  abortController?.abort();
  abortController = new AbortController();
  const signal = abortController.signal;

  try {
    const isWorkflow = selectedRoute.type === "workflow";
    const body: Record<string, unknown> = {};
    if (isWorkflow) {
      for (const param of selectedRoute.params ?? []) {
        const el = document.getElementById("field-" + param.name) as HTMLInputElement | null;
        const val = el?.value.trim();
        if (val) body[param.name] = val;
        else if (param.default !== undefined) body[param.name] = param.default;
      }
      if (!body[selectedRoute.params?.[0]?.name ?? ""]) {
        body[selectedRoute.params?.[0]?.name ?? "input"] = message;
      }
    }

    const res = await fetch(selectedRoute.path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Session-Id": sessionId,
      },
      body: JSON.stringify(isWorkflow ? body : { message }),
      signal,
    });

    if (!res.ok) {
      entry.status = "error";
      refreshRequestLog();
      graphStatus.textContent = "Error: HTTP " + res.status;
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) return;

    const dec = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      const events = parser.feed(buf);
      buf = (parser as any).buffer as string;

      for (const event of events) {
        entry.events.push(event);

        if (event.event === "graph") {
          store.apply(event.data as unknown as GraphAction);
          refreshGraph();
        }

        if (event.event === "chunk" && event.data.content) {
          chatAPI?.addAssistantChunk(String(event.data.content));
        }

        if (event.event === "result" && event.data.content) {
          chatAPI?.addAssistantResult(String(event.data.content));
        }

        if (event.event === "done") {
          entry.status = "done";
          graphStatus.textContent = "Completed " + selectedRoute.path;
          refreshRequestLog();
        }

        if (event.event === "error") {
          entry.status = "error";
          const msg = String((event.data as Record<string, unknown>).message ?? "Error");
          chatAPI?.addError(msg);
          graphStatus.textContent = "Error: " + msg;
          refreshRequestLog();
        }
      }
    }
  } catch (e: unknown) {
    if ((e as Error).name !== "AbortError") {
      entry.status = "error";
      graphStatus.textContent = "Error: " + e;
      refreshRequestLog();
    }
  }
}

// --- Graph ---
function refreshGraph(): void {
  const nodes = store.getNodes();
  const edges = store.getEdges();
  if (nodes.length === 0) return;

  const layout = layoutGraph(nodes, edges);
  renderGraph(graphSvg, layout, edges, (id) => {
    selectedNodeId = id;
    renderInspector(inspectorEl, store, selectedNodeId);
  });
}

function resetGraph(): void {
  store.apply({ action: "clear" });
  selectedNodeId = null;
  chatAPI?.reset();
  graphSvg.innerHTML = "";
  renderInspector(inspectorEl, store, null);
}

// --- Request log (click = replay) ---
function refreshRequestLog(): void {
  renderRequestLog(requestLogEl, requestLog, (entry) => {
    store.apply({ action: "clear" });
    for (const event of entry.events) {
      if (event.event === "graph") {
        store.apply(event.data as unknown as GraphAction);
      }
    }
    refreshGraph();
    currentRequestId = entry.id;
    graphStatus.textContent =
      entry.status === "done"
        ? "Completed " + entry.path
        : entry.status === "error"
          ? "Error: " + entry.path
          : "Running " + entry.path + "...";
    refreshRequestLog();
  }, currentRequestId);
}

// --- Init ---
initRoutes();
refreshRequestLog();
renderInspector(inspectorEl, store, null);
