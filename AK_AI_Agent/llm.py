"""
llm.py  -  one place for the model settings and the model connection
====================================================================
Both agent.py and tools.py call the model through chat() below, so the
provider, model name and timeout are set once instead of in two files.

Three providers, chosen with LLM_PROVIDER:

    anthropic   Claude via the Anthropic API (fast, accurate tool use)
    openai      OpenAI API, or any OpenAI-compatible API (OPENAI_BASE_URL)
    ollama      local model in the Ollama container (no API key, slow on CPU)

Settings come from environment variables (the Docker .env file sets them
on the server):

    LLM_PROVIDER      anthropic | openai | ollama           default ollama
    MODEL             model name; empty = provider default  (see DEFAULT_MODELS)
    LLM_TIMEOUT       seconds one model call may take       default 300
    LLM_MAX_TOKENS    longest reply for API providers       default 4096
    ANTHROPIC_API_KEY needed for anthropic
    OPENAI_API_KEY    needed for openai (OPENAI_BASE_URL optional)
    OLLAMA_HOST       where Ollama runs (read by the client)

Whatever the provider, chat() returns an object shaped like the ollama
library's reply, so agent.py and tools.py did not have to change:

    response.message.content                     text reply ("" if none)
    response.message.tool_calls                  list or None
    tool_call.function.name / .arguments (dict)  one requested tool call
"""

import inspect
import json
import os
import re
import uuid


PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
    "ollama": "qwen3:1.7b",
}

MODEL = os.getenv("MODEL", "").strip()

# A leftover Ollama model name (e.g. qwen3:1.7b from an old .env) can't be
# used by an API provider, so fall back to that provider's default.
if not MODEL or (PROVIDER != "ollama" and MODEL.startswith(("qwen", "llama", "gemma", "mistral", "phi"))):
    MODEL = DEFAULT_MODELS.get(PROVIDER, DEFAULT_MODELS["ollama"])

# Without a timeout, a stuck call waits forever.
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))


# ---------------------------------------------------------------------
# Reply objects with the same shape as the ollama library's reply
# ---------------------------------------------------------------------

class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content, tool_calls=None):
        self.role = "assistant"
        self.content = content or ""
        self.tool_calls = tool_calls or None


class _Response:
    def __init__(self, message):
        self.message = message


# ---------------------------------------------------------------------
# Helpers shared by the API providers
# ---------------------------------------------------------------------

def _clean(text):
    """Remove Qwen's /no_think switch - API models don't need it."""
    return (text or "").replace("/no_think", "").strip()


def _field(item, key, default=None):
    """Read a field from a dict or an object (history holds both)."""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _normalize(messages):
    """
    Turn the history (dicts, our _Message objects, ollama Message objects)
    into simple dicts. Tool results don't carry the id of the call they
    answer, so ids are matched in order: the Nth tool result after an
    assistant turn answers its Nth tool call.
    """
    out = []
    pending_ids = []

    for item in messages:
        role = _field(item, "role")

        if role == "assistant":
            calls = []
            for call in _field(item, "tool_calls") or []:
                fn = _field(call, "function")
                call_id = _field(call, "id") or f"call_{uuid.uuid4().hex[:12]}"
                args = _field(fn, "arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                calls.append({"id": call_id, "name": _field(fn, "name"), "arguments": dict(args)})
            pending_ids = [c["id"] for c in calls]
            out.append({"role": "assistant", "content": _clean(_field(item, "content")), "tool_calls": calls})

        elif role == "tool":
            call_id = pending_ids.pop(0) if pending_ids else None
            out.append({
                "role": "tool",
                "content": str(_field(item, "content") or ""),
                "tool_call_id": call_id,
            })

        else:
            out.append({"role": role, "content": _clean(_field(item, "content"))})

    return out


def _tool_schema(func):
    """
    JSON schema for one Python tool function, from its signature and its
    Google-style docstring (the same information Ollama reads).
    """
    from ollama._utils import convert_function_to_tool

    tool = convert_function_to_tool(func).model_dump(exclude_none=True)
    fn = tool["function"]
    params = fn.get("parameters", {"type": "object", "properties": {}})

    # Parameters with a default value are optional.
    sig = inspect.signature(func)
    params["required"] = [
        name for name, p in sig.parameters.items()
        if p.default is inspect.Parameter.empty and name in params.get("properties", {})
    ]
    params.pop("defs", None)

    return {
        "name": fn["name"],
        "description": fn.get("description") or func.__name__,
        "parameters": params,
    }


def _extract_json(text):
    """Return just the JSON object from a reply (drops code fences / chatter)."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    return match.group(0) if match else (text or "{}")


# ---------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------

_anthropic_client = None


def _chat_anthropic(messages, tools=None, format=None, **_):
    global _anthropic_client
    import anthropic

    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(timeout=LLM_TIMEOUT, max_retries=2)

    system_parts = []
    turns = []

    def add(role, blocks):
        # Anthropic wants user/assistant turns to alternate; merge neighbours.
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for m in _normalize(messages):
        if m["role"] == "system":
            if m["content"]:
                system_parts.append(m["content"])
        elif m["role"] == "assistant":
            blocks = []
            if m["content"]:
                blocks.append({"type": "text", "text": m["content"]})
            for c in m["tool_calls"]:
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]})
            if blocks:
                add("assistant", blocks)
        elif m["role"] == "tool":
            if m["tool_call_id"]:
                add("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
            else:
                add("user", [{"type": "text", "text": f"Tool result:\n{m['content']}"}])
        else:
            if m["content"]:
                add("user", [{"type": "text", "text": m["content"]}])

    if format == "json":
        system_parts.append("Reply with only one valid JSON object and nothing else.")

    kwargs = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": turns,
    }
    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)
    if tools:
        kwargs["tools"] = [
            {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]}
            for s in (_tool_schema(t) for t in tools)
        ]

    reply = _anthropic_client.messages.create(**kwargs)

    text = "".join(b.text for b in reply.content if b.type == "text")
    calls = [
        _ToolCall(b.id, b.name, dict(b.input or {}))
        for b in reply.content if b.type == "tool_use"
    ]

    if format == "json":
        text = _extract_json(text)

    return _Response(_Message(text, calls))


# ---------------------------------------------------------------------
# OpenAI (or any OpenAI-compatible API)
# ---------------------------------------------------------------------

_openai_client = None


def _chat_openai(messages, tools=None, format=None, **_):
    global _openai_client
    import openai

    if _openai_client is None:
        _openai_client = openai.OpenAI(
            timeout=LLM_TIMEOUT,
            max_retries=2,
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )

    out = []
    for m in _normalize(messages):
        if m["role"] == "assistant":
            msg = {"role": "assistant", "content": m["content"] or None}
            if m["tool_calls"]:
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                    for c in m["tool_calls"]
                ]
            out.append(msg)
        elif m["role"] == "tool":
            if m["tool_call_id"]:
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": f"Tool result:\n{m['content']}"})
        else:
            out.append({"role": m["role"], "content": m["content"]})

    kwargs = {"model": MODEL, "messages": out, "max_tokens": MAX_TOKENS}
    if tools:
        kwargs["tools"] = [{"type": "function", "function": _tool_schema(t)} for t in tools]
    if format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    reply = _openai_client.chat.completions.create(**kwargs)
    msg = reply.choices[0].message

    calls = []
    for c in msg.tool_calls or []:
        try:
            args = json.loads(c.function.arguments or "{}")
        except ValueError:
            args = {}
        calls.append(_ToolCall(c.id, c.function.name, args))

    text = msg.content or ""
    if format == "json":
        text = _extract_json(text)

    return _Response(_Message(text, calls))


# ---------------------------------------------------------------------
# Ollama (local model)
# ---------------------------------------------------------------------

_ollama_client = None


def _chat_ollama(messages, **kwargs):
    global _ollama_client
    from ollama import Client

    if _ollama_client is None:
        # Client() reads OLLAMA_HOST from the environment when no host is given.
        _ollama_client = Client(timeout=LLM_TIMEOUT)

    return _ollama_client.chat(model=MODEL, messages=messages, **kwargs)


# ---------------------------------------------------------------------
# The one function the rest of the app uses
# ---------------------------------------------------------------------

def chat(messages, **kwargs):
    """
    Call the model. Same arguments as ollama.chat(model=MODEL, ...):
    messages, plus optional tools=[python functions] and format="json".
    """
    if PROVIDER == "anthropic":
        return _chat_anthropic(messages, **kwargs)
    if PROVIDER == "openai":
        return _chat_openai(messages, **kwargs)
    return _chat_ollama(messages, **kwargs)
