import type { RouteMeta } from "../types";

export interface ChatAPI {
  addUserBubble(text: string): void;
  addAssistantChunk(text: string): void;
  addAssistantResult(text: string): void;
  addError(text: string): void;
  reset(): void;
}

export function renderChat(
  container: HTMLElement,
  route: RouteMeta,
  onSend: (message: string) => void,
): ChatAPI {
  container.innerHTML = `
    <div class="flex flex-col h-full">
      <div id="chat-messages" class="flex-1 overflow-auto p-3 space-y-2"></div>
      <div class="p-3 border-t border-gray-800 flex gap-2">
        <textarea id="chat-input" rows="1" placeholder="Send a message..." 
          class="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 
          resize-none focus:outline-none focus:border-blue-500"></textarea>
        <button id="chat-send" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium">
          Send
        </button>
      </div>
    </div>`;

  const input = container.querySelector("#chat-input") as HTMLTextAreaElement;
  const sendBtn = container.querySelector("#chat-send") as HTMLButtonElement;
  const messages = container.querySelector("#chat-messages") as HTMLElement;
  let assistantBubble: HTMLElement | null = null;

  const scroll = () => { messages.scrollTop = messages.scrollHeight; };

  const addBubble = (text: string, role: string): HTMLElement => {
    const colors: Record<string, string> = {
      user: "bg-blue-700 ml-auto",
      assistant: "bg-gray-800 border border-gray-700",
      error: "bg-red-900/60 border border-red-800",
    };
    const div = document.createElement("div");
    div.className = "max-w-[80%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap " + colors[role];
    div.textContent = text;
    messages.appendChild(div);
    scroll();
    return div;
  };

  const api: ChatAPI = {
    addUserBubble(text: string) {
      addBubble(text, "user");
    },
    addAssistantChunk(text: string) {
      if (!assistantBubble) {
        assistantBubble = addBubble("", "assistant");
      }
      assistantBubble.textContent += text;
      scroll();
    },
    addAssistantResult(text: string) {
      assistantBubble = null;
      addBubble(text, "assistant");
    },
    addError(text: string) {
      assistantBubble = null;
      addBubble(text, "error");
    },
    reset() {
      messages.innerHTML = "";
      assistantBubble = null;
    },
  };

  const handleSend = () => {
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    api.addUserBubble(msg);
    assistantBubble = null;
    onSend(msg);
  };

  sendBtn.addEventListener("click", handleSend);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  return api;
}
