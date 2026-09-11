import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  registerAppResource,
  registerAppTool,
  RESOURCE_MIME_TYPE
} from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import {
  DEMO_CHOICES,
  demoSurface,
  localizedCopy,
  selectedDemoChoice
} from "./lib/policy.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const widgetHtml = readFileSync(path.join(here, "public", "fomo-review-widget.html"), "utf8");
const RESOURCE_URI = "ui://fomo-kernel/review-widget.html";
const localeSchema = z.enum(["zh-TW", "en"]);

function resultFor(surface) {
  const copy = localizedCopy(surface.locale);
  return {
    content: [{ type: "text", text: surface.locale === "zh-TW" ? "已開啟合成 UI probe。" : "Opened the synthetic UI probe." }],
    structuredContent: {
      ...surface,
      copy,
      choices: DEMO_CHOICES.map((choice) => ({
        value: choice.value,
        label: surface.locale === "zh-TW" ? choice.zhTW : choice.en
      }))
    }
  };
}

export function createFomoProbeServer() {
  const server = new McpServer({ name: "fomo-kernel-codex-ui", version: "0.1.0" });

  registerAppResource(server, "fomo-review-widget", RESOURCE_URI, {}, async () => ({
    contents: [{
      uri: RESOURCE_URI,
      mimeType: RESOURCE_MIME_TYPE,
      text: widgetHtml,
      _meta: { ui: { prefersBorder: true } }
    }]
  }));

  for (const kind of ["card", "question"]) {
    registerAppTool(
      server,
      `fomo_show_demo_${kind}`,
      {
        title: kind === "card" ? "Show demo review card" : "Show demo review question",
        description: kind === "card"
          ? "Renders the fixed synthetic demo review card in the host's widget surface, in the requested locale. "
            + "Use it to verify that a styled card can appear inline in this host at all. The card is synthetic copy "
            + "with no user, session, or ledger data; every call returns the same surface, and nothing is read from "
            + "fomo-kernel sessions, ledgers, or card artifacts. It cannot show a real review card."
          : "Renders the fixed synthetic demo review question with two clickable options in the host's widget "
            + "surface, in the requested locale. Use it to verify that a click can reach a tool and return one "
            + "canonical option value; the widget calls fomo_submit_demo_choice on a click. The question is "
            + "synthetic copy, every call returns the same surface, and nothing is read from fomo-kernel sessions "
            + "or answers. It cannot ask a real review question.",
        inputSchema: { locale: localeSchema.describe("Copy language for the synthetic surface: zh-TW or en.") },
        outputSchema: {
          demo: z.literal(true),
          locale: localeSchema,
          kind: z.enum(["card", "question"])
        },
        _meta: {
          ui: { resourceUri: RESOURCE_URI },
          "openai/outputTemplate": RESOURCE_URI,
          "openai/toolInvocation/invoking": "Opening UI probe…",
          "openai/toolInvocation/invoked": "UI probe opened."
        }
      },
      async ({ locale }) => resultFor(demoSurface(locale, kind))
    );
  }

  registerAppTool(
    server,
    "fomo_submit_demo_choice",
    {
      title: "Submit demo choice",
      description: "Records which of the two synthetic demo options the user clicked for the fixed demo question "
        + "and returns that canonical value in its structured result. The widget calls it on a click; it is not "
        + "meant to be called from conversation. It writes nothing: no review answer, no session, no file.",
      inputSchema: {
        locale: localeSchema.describe("Copy language of the confirmation text: zh-TW or en."),
        question_id: z.literal("codex_ui_probe_choice").describe("The only demo question; any other id is rejected."),
        choice: z.enum(["rule_a", "rule_b"]).describe("The clicked option's canonical value.")
      },
      outputSchema: {
        demo: z.literal(true),
        locale: localeSchema,
        question_id: z.literal("codex_ui_probe_choice"),
        choice: z.enum(["rule_a", "rule_b"])
      },
      _meta: { ui: { resourceUri: RESOURCE_URI, visibility: ["app"] } }
    },
    async ({ locale, question_id: questionId, choice }) => ({
      content: [{ type: "text", text: "Demo choice recorded in tool output only." }],
      structuredContent: selectedDemoChoice({ locale, questionId, choice })
    })
  );

  return server;
}

async function runStdio() {
  const server = createFomoProbeServer();
  await server.connect(new StdioServerTransport());
}

function startHttp() {
  const port = Number(process.env.PORT || 8787);
  const httpServer = createServer(async (req, res) => {
    const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, { "content-type": "text/plain" }).end("fomo-kernel Codex UI probe");
      return;
    }
    if (req.method === "OPTIONS" && url.pathname === "/mcp") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "content-type, mcp-session-id",
        "Access-Control-Expose-Headers": "Mcp-Session-Id"
      }).end();
      return;
    }
    if (url.pathname === "/mcp" && req.method && new Set(["POST", "GET", "DELETE"]).has(req.method)) {
      const server = createFomoProbeServer();
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
      res.on("close", () => { transport.close(); server.close(); });
      try {
        await server.connect(transport);
        await transport.handleRequest(req, res);
      } catch (error) {
        console.error("MCP request failed:", error);
        if (!res.headersSent) res.writeHead(500).end("Internal server error");
      }
      return;
    }
    res.writeHead(404).end("Not Found");
  });
  httpServer.listen(port, () => console.log(`fomo-kernel Codex UI probe listening on http://localhost:${port}/mcp`));
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  if (process.argv.includes("--stdio")) {
    runStdio().catch((error) => { console.error(error); process.exit(1); });
  } else {
    startHttp();
  }
}
