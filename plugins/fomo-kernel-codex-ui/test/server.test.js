import test from "node:test";
import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createFomoProbeServer } from "../server.js";

test("server can be imported without binding an HTTP port", async () => {
  const server = createFomoProbeServer();
  assert.ok(server);
  await server.close();
});

test("the submit tool is app-only and returns the selected synthetic value", async () => {
  const server = createFomoProbeServer();
  const client = new Client({ name: "fomo-probe-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    const { tools } = await client.listTools();
    const submit = tools.find((tool) => tool.name === "fomo_submit_demo_choice");
    assert.deepEqual(submit?._meta?.ui?.visibility, ["app"]);

    const result = await client.callTool({
      name: "fomo_submit_demo_choice",
      arguments: { locale: "zh-TW", question_id: "codex_ui_probe_choice", choice: "rule_a" }
    });
    assert.deepEqual(result.structuredContent, {
      demo: true,
      locale: "zh-TW",
      question_id: "codex_ui_probe_choice",
      choice: "rule_a"
    });
  } finally {
    await client.close();
    await server.close();
  }
});
