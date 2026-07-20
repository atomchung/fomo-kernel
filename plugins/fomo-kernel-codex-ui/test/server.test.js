import test from "node:test";
import assert from "node:assert/strict";
import { createFomoProbeServer } from "../server.js";

test("server can be imported without binding an HTTP port", async () => {
  const server = createFomoProbeServer();
  assert.ok(server);
  await server.close();
});
