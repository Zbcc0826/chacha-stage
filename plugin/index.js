/** dsh 组合包入口。舞台进程仍由 python server.py 提供，这里只注册模型可以调用的工具。 */
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "chacha-stage";
export const inject = ["tools"];

const STAGE_URL = "http://127.0.0.1:8787";

/** 访问本机舞台。不走系统代理，也不带密钥。 */
async function stageFetch(path, payload) {
  const response = await fetch(STAGE_URL + path, {
    method: payload ? "POST" : "GET",
    headers: payload ? { "Content-Type": "application/json" } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "舞台没有接上");
  return data;
}

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: "stage_say",
    description: "Speak one line through the local Live2D stage or desktop pet. The stage server must already be running at http://127.0.0.1:8787. Call this once per sentence so playback can start before later sentences are ready.",
    parameters: {
      say: { type: "string", required: true, description: "The sentence to speak." },
      mood: { type: "string", description: "Engine mood: neutral, happy, shy, angry, sad, surprise, star, or think." },
      motion: { type: "string", description: "A motion id from the current model. Omit when none fits." },
      expression: { type: "string", description: "An expression id from the current model. Omit when none fits." },
    },
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }],
    },
    async execute(args) {
      const data = await stageFetch("/api/perform", {
        text: args.say,
        mood: args.mood,
        motion: args.motion,
        expression: args.expression,
        speak: true,
      });
      return data.say ? "已交给舞台：" + data.say : "舞台已收到这一句。";
    },
  }));

  ctx.tools.register(defineTool({
    name: "stage_status",
    description: "Read which Live2D model the local stage has loaded, and whether the user's dsh key is connected. Does not return the key.",
    parameters: {},
    output: {
      schema: { type: "string" },
      render: (_args, value) => [{ type: "text", text: value }],
    },
    async execute() {
      const data = await stageFetch("/api/status");
      const names = (data.models || []).map((item) => item.name || item.id).filter(Boolean);
      return "当前模型：" + (names.join("、") || "还没有") + "。dsh " + (data.llmReady ? "已连接" : "还没有密钥") + "。";
    },
  }));
}
