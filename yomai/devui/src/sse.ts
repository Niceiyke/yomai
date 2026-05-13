import type { SSEEvent } from "./types";

export class SSEParser {
  private buffer = "";

  feed(chunk: string): SSEEvent[] {
    this.buffer += chunk;
    const events: SSEEvent[] = [];
    const parts = this.buffer.split("\n\n");

    // Last part may be incomplete
    this.buffer = parts.pop() ?? "";

    for (const part of parts) {
      let eventType = "message";
      let dataStr = "{}";

      for (const line of part.split("\n")) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          dataStr = line.slice(6).trim();
        }
      }

      if (eventType === "ping") continue;

      try {
        const data = JSON.parse(dataStr || "{}");
        events.push({ event: eventType, data });
      } catch {
        events.push({ event: eventType, data: { raw: dataStr } });
      }
    }

    return events;
  }
}
