"""
llm.py  -  one place for the model settings and the Ollama connection
=====================================================================
Both agent.py and tools.py call the model through chat() below,
so the model name and timeout are set once instead of in two files.

Settings come from environment variables (the Docker .env file sets them
on the server); the defaults below are used when running locally.

    MODEL         which Ollama model to use               default qwen3:1.7b
    LLM_TIMEOUT   seconds one model call may take         default 300
    OLLAMA_HOST   where Ollama runs (read by the client)  default http://localhost:11434
"""

import os

from ollama import Client


MODEL = os.getenv("MODEL", "qwen3:1.7b")

# Without a timeout, a stuck Ollama call waits forever (the ollama
# library's default is no timeout). 300 s matches Day 20's AGENT_TIMEOUT.
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))

# Client() reads OLLAMA_HOST from the environment when no host is given.
_client = Client(timeout=LLM_TIMEOUT)


def chat(messages, **kwargs):
    """
    Call the model. Same as ollama.chat(model=MODEL, ...).
    Extra keyword arguments (tools=..., format="json") are passed through.
    """
    return _client.chat(
        model=MODEL,
        messages=messages,
        **kwargs
    )
