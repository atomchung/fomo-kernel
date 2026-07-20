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
        description: "Demo-only UI probe. It never reads fomo-kernel sessions or card artifacts.",
        inputSchema: { locale: localeSchema },
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
      description: "Returns the selected canonical demo value. It does not write a review answer.",
      inputSchema: {
        locale: localeSchema,
        question_id: z.literal("codex_ui_probe_choice"),
        choice: z.enum(["rule_a", "rule_b"])
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
