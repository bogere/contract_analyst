import type {
  AgAiConversationItem,
  AgAiOutputItem,
  AgLlmAdapter,
  AgLlmRequest,
} from "ag-studio";

type ChatMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
};

type AssistantReply = {
  id: string;
  model: string;
  message: {
    content: string | null;
    tool_calls: Array<{
      id: string;
      function: { name: string; arguments: string };
    }>;
  };
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toChatMessages(input: AgAiConversationItem[], instructions?: string): ChatMessage[] {
  const messages: ChatMessage[] = [];
  if (instructions) messages.push({ role: "system", content: instructions });

  for (const item of input) {
    if (item.type === "function_call_output") {
      messages.push({ role: "tool", tool_call_id: item.callId, content: item.output });
      continue;
    }

    if (item.kind === "input") {
      messages.push({
        role: item.role,
        content: item.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("\n"),
      });
      continue;
    }

    if (item.type === "function_call") {
      messages.push({
        role: "assistant",
        content: null,
        tool_calls: [{
          id: item.callId,
          type: "function",
          function: { name: item.name, arguments: item.arguments },
        }],
      });
      continue;
    }

    if (item.type === "message") {
      messages.push({
        role: "assistant",
        content: item.content
          .map((part) => part.type === "text" ? part.text : part.refusal)
          .join("\n"),
      });
    }
  }

  return messages;
}

function toResponseFormat(request: AgLlmRequest): Record<string, unknown> | undefined {
  if (request.responseFormat.type === "text") return undefined;
  return {
    type: "json_schema",
    json_schema: {
      name: request.responseFormat.name,
      description: request.responseFormat.description,
      strict: true,
      schema: request.responseFormat.schema,
    },
  };
}

function parseAssistantReply(value: unknown): AssistantReply {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.model !== "string") {
    throw new Error("The payments assistant returned an invalid response.");
  }
  const message = value.message;
  if (!isRecord(message) || (message.content !== null && typeof message.content !== "string")) {
    throw new Error("The payments assistant returned an invalid response.");
  }
  const toolCalls = message.tool_calls;
  if (toolCalls !== undefined && !Array.isArray(toolCalls)) {
    throw new Error("The payments assistant returned an invalid response.");
  }

  const parsedToolCalls = (toolCalls ?? []).map((toolCall: unknown) => {
    if (!isRecord(toolCall) || typeof toolCall.id !== "string" || !isRecord(toolCall.function)) {
      throw new Error("The payments assistant returned an invalid tool call.");
    }
    const fn = toolCall.function;
    if (typeof fn.name !== "string" || typeof fn.arguments !== "string") {
      throw new Error("The payments assistant returned an invalid tool call.");
    }
    return { id: toolCall.id, function: { name: fn.name, arguments: fn.arguments } };
  });

  return {
    id: value.id,
    model: value.model,
    message: { content: message.content, tool_calls: parsedToolCalls },
  };
}

async function requestAssistant(
  endpoint: string,
  request: AgLlmRequest,
  signal?: AbortSignal,
): Promise<AssistantReply> {
  const response = await fetch(`${endpoint}/api/admin/payments/assistant`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      messages: toChatMessages(request.input, request.instructions),
      tools: request.tools?.map((tool) => ({
        type: "function",
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
        },
      })),
      tool_choice: request.toolChoice === undefined
        ? undefined
        : typeof request.toolChoice === "string"
          ? request.toolChoice
          : { type: "function", function: { name: request.toolChoice.name } },
      response_format: toResponseFormat(request),
    }),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = isRecord(body) && typeof body.detail === "string"
      ? body.detail
      : "Unable to contact the payments assistant.";
    throw new Error(detail);
  }
  return parseAssistantReply(body);
}

export function createPaymentsAiAdapter(endpoint: string): AgLlmAdapter {
  return {
    executeTurn(request, options) {
      const reply = requestAssistant(endpoint, request, options?.signal);
      const stream = (async function* () {
        try {
          const result = await reply;
          if (result.message.content) {
            yield {
              type: "TEXT_MESSAGE_CHUNK" as const,
              messageId: `${result.id}-message`,
              role: "assistant" as const,
              delta: result.message.content,
            };
          }
          for (const toolCall of result.message.tool_calls) {
            yield {
              type: "TOOL_CALL_CHUNK" as const,
              toolCallId: toolCall.id,
              toolCallName: toolCall.function.name,
              delta: toolCall.function.arguments,
            };
          }
        } catch {
          return;
        }
      })();
      const complete = reply.then((result) => {
        const output: AgAiOutputItem[] = [];
        if (result.message.content) {
          output.push({
            id: `${result.id}-message`,
            kind: "output",
            type: "message",
            role: "assistant",
            status: "completed",
            content: [{ type: "text", text: result.message.content, annotations: [] }],
          });
        }
        for (const toolCall of result.message.tool_calls) {
          output.push({
            id: `${result.id}-${toolCall.id}`,
            kind: "output",
            type: "function_call",
            callId: toolCall.id,
            name: toolCall.function.name,
            arguments: toolCall.function.arguments,
            status: "completed",
          });
        }
        return {
          id: result.id,
          createdAt: Date.now(),
          output,
          status: "completed" as const,
          model: result.model,
        };
      }).catch((cause: unknown) => ({
        id: crypto.randomUUID(),
        createdAt: Date.now(),
        output: [],
        status: "failed" as const,
        error: {
          code: "payments_assistant_error",
          message: cause instanceof Error ? cause.message : "The payments assistant request failed.",
        },
      }));

      return { stream, complete };
    },
  };
}
