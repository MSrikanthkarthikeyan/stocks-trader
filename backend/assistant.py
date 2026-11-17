from __future__ import annotations

import json
import os
from typing import Any

from . import nse


def _json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON Schema to Gemini's function declaration schema."""
    allowed = {"type", "properties", "required", "description", "items", "enum", "nullable"}
    result = {key: value for key, value in schema.items() if key in allowed}
    if isinstance(result.get("properties"), dict):
        result["properties"] = {key: _json_schema(value) for key, value in result["properties"].items()}
    if isinstance(result.get("items"), dict):
        result["items"] = _json_schema(result["items"])
    if isinstance(result.get("type"), list):
        result["type"] = next((item for item in result["type"] if item != "null"), "string")
    return result


def ask_nse(question: str) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY in your .env file to enable the NSE AI assistant.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Install dependencies with pip install -r requirements.txt.") from exc

    endpoints = {"bhavcopy": nse.BHAVCOPY_URL, "cm-market": nse.LIVE_URL}
    tools: list[dict[str, Any]] = []
    tool_urls: dict[str, str] = {}
    for server, url in endpoints.items():
        for definition in nse.list_tools(url):
            schema = _json_schema(definition.get("inputSchema", {"type": "object", "properties": {}}))
            tools.append({"name": f"{server.replace('-', '_')}__{definition['name']}",
                          "description": f"NSE {server}: {definition.get('description', definition['name'])}",
                          "parameters": schema})
            tool_urls[f"{server.replace('-', '_')}__{definition['name']}"] = url
    if not tools:
        raise RuntimeError("NSE MCP servers returned no tools.")
    declarations = [types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"]) for t in tools]
    tool_config = types.Tool(function_declarations=declarations)
    client = genai.Client(api_key=api_key)
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    contents: list[Any] = [question]
    calls_used: list[str] = []
    for _ in range(6):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=("You are the research assistant in a paper-trading market portal. Use NSE MCP data tools for all current or historical market facts. "
                                   "Never invent prices or imply data is real-time: CM market data is delayed 1-3 minutes and bhavcopy is end-of-day. "
                                   "State the data date/source, distinguish observation from opinion, and do not give personalized financial advice or claim certainty."),
                tools=[tool_config], temperature=0.2,
            ),
        )
        calls = [part.function_call for candidate in (response.candidates or []) for part in (candidate.content.parts or []) if part.function_call]
        if not calls:
            return {"answer": response.text or "I could not form an answer from NSE data.", "tools_used": calls_used, "model": model}
        contents.append(response.candidates[0].content)
        replies = []
        for call in calls:
            try:
                tool_name = call.name.split("__", 1)[1]
                result = nse.call_tool(tool_urls[call.name], tool_name, dict(call.args or {}))
                replies.append(types.Part.from_function_response(name=call.name, response={"result": result}))
                calls_used.append(tool_name)
            except Exception as exc:
                replies.append(types.Part.from_function_response(name=call.name, response={"error": str(exc)}))
        contents.append(types.Content(role="user", parts=replies))
    raise RuntimeError("Gemini reached its NSE tool-call limit. Try a narrower question.")

