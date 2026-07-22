import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const widget = readFileSync(path.join(here, "..", "public", "fomo-review-widget.html"), "utf8");
const bridgeSource = widget.match(/<script data-fomo-app-bridge>([\s\S]*?)<\/script>/)?.[1];

function loadBridge() {
  assert.ok(bridgeSource, "widget must contain the standalone MCP Apps bridge");
  const listeners = new Map();
  const posted = [];
  const parent = { postMessage: (message, targetOrigin) => posted.push({ message: JSON.parse(JSON.stringify(message)), targetOrigin }) };
  const window = {
    parent,
    addEventListener: (type, handler) => listeners.set(type, handler)
  };
  vm.runInNewContext(bridgeSource, { window, Map, Error, Object });
  return {
    bridge: window.FomoAppBridge,
    posted,
    reply: (message) => listeners.get("message")({ source: parent, data: message })
  };
}

test("initializes through the MCP Apps JSON-RPC postMessage handshake", async () => {
  const { bridge, posted, reply } = loadBridge();
  const initialized = bridge.initialize();
  assert.deepEqual(posted, [{
    message: {
      jsonrpc: "2.0",
      id: 1,
      method: "ui/initialize",
      params: {
        appInfo: { name: "fomo-kernel-codex-ui-probe", version: "0.1.0" },
        appCapabilities: {},
        protocolVersion: "2026-01-26"
      }
    },
    targetOrigin: "*"
  }]);

  reply({ jsonrpc: "2.0", id: 1, result: { hostCapabilities: { serverTools: {} } } });
  await initialized;
  assert.deepEqual(posted[1], {
    message: { jsonrpc: "2.0", method: "ui/notifications/initialized" },
    targetOrigin: "*"
  });
});

test("button action requests only the app-only synthetic tool and renders its structured response", async () => {
  const { bridge, posted, reply } = loadBridge();
  const resultPromise = bridge.requestTool("fomo_submit_demo_choice", {
    locale: "zh-TW",
    question_id: "codex_ui_probe_choice",
    choice: "rule_b"
  });
  assert.deepEqual(posted[0], {
    message: {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: {
        name: "fomo_submit_demo_choice",
        arguments: { locale: "zh-TW", question_id: "codex_ui_probe_choice", choice: "rule_b" }
      }
    },
    targetOrigin: "*"
  });

  const expected = { structuredContent: { demo: true, choice: "rule_b" } };
  reply({ jsonrpc: "2.0", id: 1, result: expected });
  assert.deepEqual(await resultPromise, expected);
});

test("JSON-RPC errors reject the synthetic action without a host-specific fallback", async () => {
  const { bridge, reply } = loadBridge();
  const resultPromise = bridge.requestTool("fomo_submit_demo_choice", {
    locale: "en",
    question_id: "codex_ui_probe_choice",
    choice: "rule_a"
  });
  reply({ jsonrpc: "2.0", id: 1, error: { code: -32000, message: "Approval denied" } });
  await assert.rejects(resultPromise, /Approval denied/);
});

test("widget does not use legacy OpenAI globals or message-sending fallbacks", () => {
  assert.doesNotMatch(widget, /window\.openai|sendFollowUpMessage|ui\/message/);
  assert.match(widget, /request\('tools\/call'/);
  assert.match(widget, /fomo_submit_demo_choice/);
});
