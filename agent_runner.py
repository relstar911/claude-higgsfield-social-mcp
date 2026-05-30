"""Agent runner (cron trigger).

Starts an Anthropic Messages API conversation, hands Claude this MCP server, and
instructs it to run one cycle per active persona and to overwrite the template
captions itself. The MCP server does not start itself -- this is the trigger.

Usage:
    python agent_runner.py                 # real run (needs ANTHROPIC_API_KEY)
    python agent_runner.py --print-only    # print the request, no API call

The runner is tolerant of SDK differences in how the MCP-connector parameter is
named/passed across anthropic-sdk versions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = (
    "You operate AI-influencer personas through the higgsfield_social MCP server. "
    "Each run: call run_all to execute one cycle per active persona. For every "
    "cycle, read the caption brief and write a platform-native caption in the "
    "persona's voice, overriding the deterministic template caption. Respect each "
    "persona's autonomy and dry_run flags -- never bypass an approval gate. Keep "
    "the AI-generated disclosure when required. Report a concise per-persona summary."
)

USER_PROMPT = (
    "Run one content cycle for every active persona now. For each, write the final "
    "caption yourself from the brief instead of using the template, then summarise "
    "what was produced and whether it was published, held for approval, or dry-run."
)


def _mcp_server_descriptor() -> dict[str, str]:
    """Describe how to reach this MCP server.

    For the hosted MCP connector this would be a URL; for a locally spawned
    stdio server the orchestrator launches `python -m higgsfield_social.server`.
    """
    url = os.environ.get("HF_SOCIAL_MCP_URL")
    if url:
        return {"type": "url", "url": url, "name": "higgsfield_social"}
    return {"type": "stdio", "command": "python -m higgsfield_social.server", "name": "higgsfield_social"}


def build_request(model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": USER_PROMPT}],
        "mcp_servers": [_mcp_server_descriptor()],
    }


def run(model: str, print_only: bool) -> int:
    request = build_request(model)

    if print_only:
        print(json.dumps(request, indent=2))
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is not set -- cannot orchestrate.\n"
            "Set it, or use --print-only to inspect the request.",
            file=sys.stderr,
        )
        return 2

    try:
        import anthropic
    except ImportError:
        print("The anthropic SDK is not installed: pip install anthropic", file=sys.stderr)
        return 2

    client = anthropic.Anthropic(api_key=api_key)

    # Tolerate SDK variation in how the MCP-connector parameter is exposed.
    attempts = (
        ("beta.messages", {"mcp_servers": request["mcp_servers"], "betas": ["mcp-client-2025-04-04"]}),
        ("messages", {"mcp_servers": request["mcp_servers"]}),
        ("messages", {}),  # last resort: no connector param
    )
    base_kwargs = {
        "model": request["model"],
        "max_tokens": request["max_tokens"],
        "system": request["system"],
        "messages": request["messages"],
    }
    last_error: Exception | None = None
    for path, extra in attempts:
        try:
            target = client
            for part in path.split("."):
                target = getattr(target, part)
            response = target.create(**base_kwargs, **extra)
            _print_response(response)
            return 0
        except (AttributeError, TypeError) as exc:
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - surface API errors clearly
            print(f"Anthropic API call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    print(f"Could not find a compatible MCP-connector call path: {last_error}", file=sys.stderr)
    return 1


def _print_response(response: object) -> None:
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cron trigger for AI-influencer cycles.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Anthropic model id.")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the request JSON without calling the API.",
    )
    args = parser.parse_args()
    return run(args.model, args.print_only)


if __name__ == "__main__":
    raise SystemExit(main())
