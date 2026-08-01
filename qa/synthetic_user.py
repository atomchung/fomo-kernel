"""Bounded synthetic-user actions for the one #718 walkthrough."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


class SyntheticUserError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    action_type: str
    text: str | None = None
    option_id: str | None = None
    reason: str = ""


def parse_action(payload, *, allowed_actions, allowed_text):
    if not isinstance(payload, dict) or set(payload) - {"action_type", "text", "option_id", "reason"}:
        raise SyntheticUserError("synthetic user must return only action_type, text, option_id, reason")
    kind = payload.get("action_type")
    if kind not in allowed_actions:
        raise SyntheticUserError(f"synthetic action {kind!r} is not allowed on this surface")
    text = payload.get("text")
    if kind == "provide_text":
        if not isinstance(text, str) or text not in allowed_text:
            raise SyntheticUserError("synthetic free text is outside the scenario policy")
    elif text is not None:
        raise SyntheticUserError("only provide_text may carry text")
    option_id = payload.get("option_id")
    if option_id is not None and not isinstance(option_id, str):
        raise SyntheticUserError("option_id must be text when present")
    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        raise SyntheticUserError("reason must be text")
    return Action(kind, text, option_id, reason)


class StubSyntheticUser:
    """Offline primitive: returns only scenario-declared text in sequence."""

    def __init__(self, responses):
        self._responses = iter(responses)

    def choose(self, _surface):
        try:
            return {"action_type": "provide_text", "text": next(self._responses),
                    "reason": "scenario-declared response"}
        except StopIteration as exc:
            raise SyntheticUserError("stub ran out of declared responses") from exc


class AgySyntheticUser:
    """Opt-in LLM boundary. It can return an action, never an answer or command."""

    def __init__(self, scenario, *, model="gemini-3.6-flash-high", effort="high"):
        self.scenario = scenario
        self.model = model
        self.effort = effort

    def choose(self, surface):
        prompt = (
            "You are the synthetic user, never the answer generator. Return one JSON object only. "
            "Choose only one allowed action for the current product surface; do not give investment advice, "
            "write an answer, invoke tools, or add facts.\n"
            f"Persona: {json.dumps(self.scenario, ensure_ascii=False)}\n"
            f"Current product surface: {json.dumps(surface, ensure_ascii=False)}\n"
            "Allowed JSON shape: {\"action_type\":\"provide_text\",\"text\":\"exact scenario fact\",\"reason\":\"brief\"}."
        )
        binary = os.path.expanduser("~/.local/bin/agy")
        last = ""
        for _ in range(3):
            try:
                run = subprocess.run([binary, "-p", prompt, "--model", self.model, "--effort", self.effort],
                                     capture_output=True, text=True, timeout=280)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = str(exc)
                continue
            last = run.stderr or run.stdout
            if run.returncode == 0 and run.stdout.strip():
                text = run.stdout.strip()
                if text.startswith("```"):
                    text = text.strip("`").removeprefix("json").strip()
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise SyntheticUserError("synthetic-user backend returned malformed JSON") from exc
            if "Agent execution terminated due to error" not in last:
                break
        raise SyntheticUserError(f"synthetic-user backend failed: {last[:240]}")
