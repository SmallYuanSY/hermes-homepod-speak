"""hermes-homepod-speak: let the agent say things out loud on a HomePod through Home Assistant.

Registers one tool, ``speak_aloud``. It reuses Hermes' own ``text_to_speech`` tool, so the voice is
whatever ``tts:`` in config.yaml says, and then plays the result on a Home Assistant media player.
Settings are read on every call, so config changes apply without a restart.
"""

import json
import os

try:
    from .homepod_speak import HomeAssistant, load_settings, speak
except ImportError:  # loaded as a top-level module (tests, some loaders)
    from homepod_speak import HomeAssistant, load_settings, speak

SCHEMA = {
    "name": "speak_aloud",
    "description": (
        "Say a short line out loud through the home speaker, in your configured text_to_speech voice. "
        "Use it when hearing your voice matters more than reading text: a greeting, a reminder, a small "
        "moment. Do not use it for ordinary chat replies. Write the line for the ear: one or two short "
        "sentences, no markdown, emoji or URLs. Before any audio is generated it checks the quiet rules "
        "(quiet hours, do-not-disturb), that someone is home, and that the speaker is not already playing; "
        "if a check fails it returns success=false with a reason and nothing is spoken. force=true skips the "
        "home and busy checks only, never the quiet rules; use it only for something important enough to "
        "interrupt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The exact words to say out loud."},
            "force": {
                "type": "boolean",
                "description": "Skip the someone-is-home and speaker-busy checks (not the quiet rules). "
                               "Only for important things.",
                "default": False,
            },
        },
        "required": ["text"],
    },
}


def register(ctx) -> None:
    def settings():
        return load_settings(ctx.get_config, os.environ)

    def available() -> bool:
        s = settings()
        return bool(s.speaker and s.ha_token)

    def handle(args: dict, **kwargs) -> str:
        del kwargs
        s = settings()
        result = speak(
            text=str(args.get("text", "")),
            force=bool(args.get("force", False)),
            s=s,
            ha=HomeAssistant(s.ha_url, s.ha_token),
            synthesize=lambda line: ctx.dispatch_tool("text_to_speech", {"text": line}),
        )
        return json.dumps(result, ensure_ascii=False)

    ctx.register_tool(
        name="speak_aloud",
        toolset="homepod_speak",
        schema=SCHEMA,
        handler=handle,
        check_fn=available,
        emoji="🔊",
    )
