"""Core logic for hermes-homepod-speak. No Hermes imports, so it can be tested on its own.

speak() runs in this order, and stops at the first problem:
  1. validate the text
  2. quiet rules (quiet_hours, quiet_when) - these hold even with force=true
  3. preflight against Home Assistant (someone home? speaker free?) - skipped with force=true
  4. synthesize with the agent's own text_to_speech tool (injected as a callable)
  5. make sure the file is something the speaker can play (transcode with ffmpeg if needed)
  6. upload it to Home Assistant's local media library and play it on the speaker
Nothing is generated before steps 1-3 pass. Every outcome is appended to a JSONL log.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Tuple

DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_MAX_CHARS = 200
DEFAULT_MEDIA_NAME = "hermes-speak.mp3"
PLAYABLE = {".mp3": "audio/mpeg", ".wav": "audio/wav"}


@dataclass
class QuietRule:
    entity: str
    states: Tuple[str, ...] = ("on",)


@dataclass
class Settings:
    speaker: str
    presence: Optional[str] = None
    max_chars: int = DEFAULT_MAX_CHARS
    media_name: str = DEFAULT_MEDIA_NAME
    log_file: Optional[Path] = None
    ha_url: str = DEFAULT_HA_URL
    ha_token: str = ""
    quiet_hours: Optional[str] = None
    quiet_when: List[QuietRule] = field(default_factory=list)


def parse_quiet_when(raw: object) -> List[QuietRule]:
    """Accept a list of {entity, states} mappings (states defaults to ["on"]) or bare entity ids."""
    rules: List[QuietRule] = []
    for item in raw or []:
        if isinstance(item, str):
            rules.append(QuietRule(item))
        elif isinstance(item, Mapping) and item.get("entity"):
            states = item.get("states") or ["on"]
            if isinstance(states, str):
                states = [states]
            rules.append(QuietRule(str(item["entity"]), tuple(str(x) for x in states)))
    return rules


def parse_hours(spec: str) -> Tuple[dt.time, dt.time]:
    """'01:00-09:00' -> (01:00, 09:00). Raises ValueError on anything else."""
    start, end = (part.strip() for part in spec.split("-"))
    return dt.time.fromisoformat(start), dt.time.fromisoformat(end)


def in_window(now: dt.time, start: dt.time, end: dt.time) -> bool:
    """True when now is in [start, end); windows may wrap past midnight (e.g. 23:00-07:00)."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def hermes_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def load_settings(get_config: Callable[[str, object], object], environ: Mapping[str, str]) -> Settings:
    """Read plugin settings (plugins.entries.<id>.settings.*) plus HASS_URL / HASS_TOKEN."""
    log_file = get_config("log_file", None)
    presence = get_config("presence", None)
    return Settings(
        speaker=str(get_config("speaker", "") or ""),
        presence=str(presence) if presence else None,
        max_chars=int(get_config("max_chars", DEFAULT_MAX_CHARS) or DEFAULT_MAX_CHARS),
        media_name=str(get_config("media_name", DEFAULT_MEDIA_NAME) or DEFAULT_MEDIA_NAME),
        log_file=Path(str(log_file)).expanduser() if log_file
        else hermes_home(environ) / "logs" / "speak_aloud.jsonl",
        ha_url=str(environ.get("HASS_URL") or DEFAULT_HA_URL).rstrip("/"),
        ha_token=str(environ.get("HASS_TOKEN") or ""),
        quiet_hours=str(get_config("quiet_hours", "") or "") or None,
        quiet_when=parse_quiet_when(get_config("quiet_when", None)),
    )


class HomeAssistant:
    def __init__(self, url: str, token: str, timeout: int = 30):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _call(self, method: str, path: str, body: Optional[bytes] = None, ctype: Optional[str] = None) -> bytes:
        headers = {"Authorization": f"Bearer {self.token}"}
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def state(self, entity_id: str) -> dict:
        return json.loads(self._call("GET", f"/api/states/{entity_id}"))

    def upload(self, name: str, audio: bytes, content_type: str) -> None:
        b = uuid.uuid4().hex
        body = b"".join([
            f'--{b}\r\nContent-Disposition: form-data; name="media_content_id"\r\n\r\n'
            "media-source://media_source/local/.\r\n".encode(),
            (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
             f"Content-Type: {content_type}\r\n\r\n").encode() + audio + b"\r\n",
            f"--{b}--\r\n".encode(),
        ])
        self._call("POST", "/api/media_source/local_source/upload", body, f"multipart/form-data; boundary={b}")

    def play(self, speaker: str, name: str) -> None:
        body = json.dumps({"entity_id": speaker,
                           "media_content_id": f"media-source://media_source/local/{name}",
                           "media_content_type": "music"}).encode()
        self._call("POST", "/api/services/media_player/play_media", body, "application/json")


def refusal(reason: str, message: str, **extra) -> dict:
    return {"success": False, "spoken": False, "reason": reason, "message": message, **extra}


def quiet_check(ha: HomeAssistant, s: Settings, now: dt.datetime) -> Optional[dict]:
    """Quiet rules: a refusal dict when it is quiet time, else None. force=true does not skip these."""
    if s.quiet_hours:
        start, end = parse_hours(s.quiet_hours)
        if in_window(now.time(), start, end):
            return refusal("quiet_hours", f"It is quiet hours ({s.quiet_hours}); nothing is spoken now, "
                                          "even with force=true.")
    for rule in s.quiet_when:
        state = ha.state(rule.entity).get("state")
        if state in rule.states:
            return refusal("quiet", f"{rule.entity} is '{state}', which means be quiet; nothing is spoken "
                                    "now, even with force=true.", entity=rule.entity, state=state)
    return None


def preflight(ha: HomeAssistant, s: Settings) -> Optional[dict]:
    """Return a refusal dict when speaking now would be pointless or rude, else None."""
    if s.presence:
        who = ha.state(s.presence).get("state")
        if who != "home":
            return refusal("not_home", f"{s.presence} is '{who}', so nobody would hear it.", presence=who)
    sp = ha.state(s.speaker)
    state = sp.get("state")
    if state == "playing":
        title = (sp.get("attributes") or {}).get("media_title")
        what = f"'{title}'" if title else "something"
        return refusal("speaker_busy", f"The speaker is playing {what}; speaking now would interrupt it. "
                                       "Try again later, or use force=true only if it is important.",
                       title=title)
    if state in ("unavailable", "unknown", None):
        return refusal("speaker_unavailable", f"Home Assistant cannot reach {s.speaker} (state: {state}).")
    return None


def ensure_playable(path: Path) -> Path:
    """Return a file the speaker can play; transcode anything else (e.g. .ogg) to MP3 with ffmpeg."""
    if path.suffix.lower() in PLAYABLE:
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(f"text_to_speech produced {path.suffix} and ffmpeg is not installed to convert it")
    out = Path(tempfile.gettempdir()) / f"{path.stem}.speak.mp3"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), str(out)],
                   check=True, timeout=60)
    return out


def write_log(s: Settings, text: str, result: dict) -> None:
    if not s.log_file:
        return
    try:
        s.log_file.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "text": text, "chars": len(text),
               "result": "spoken" if result.get("spoken") else "not_spoken",
               **({"reason": result["reason"]} if "reason" in result else {})}
        with s.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # logging must never break speaking


def _parse_tts(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return {"error": str(raw)[:300]}


def speak(text: str, force: bool, s: Settings, ha: HomeAssistant,
          synthesize: Callable[[str], object], now: Optional[dt.datetime] = None) -> dict:
    """Run the whole pipeline and return a JSON-able result for the model."""
    now = now or dt.datetime.now()
    text = (text or "").strip()
    if not text:
        return refusal("empty", "Nothing to say: text is empty.")
    if len(text) > s.max_chars:
        return refusal("too_long", f"The line is {len(text)} characters; the limit is {s.max_chars}. "
                                   "Spoken lines should be one or two short sentences.")
    if not s.speaker or not s.ha_token:
        return refusal("config", "Set plugins.entries.hermes-homepod-speak.settings.speaker in config.yaml "
                                 "and HASS_TOKEN in the environment.")
    if s.quiet_hours:
        try:
            parse_hours(s.quiet_hours)
        except ValueError:
            return refusal("config", f"quiet_hours must look like '01:00-09:00', got {s.quiet_hours!r}.")
    try:
        refused = quiet_check(ha, s, now) or (None if force else preflight(ha, s))
        if refused:
            write_log(s, text, refused)
            return refused

        tts = _parse_tts(synthesize(text))
        audio_path = tts.get("file_path") or (tts.get("file_paths") or [None])[0]
        if not audio_path or tts.get("error") or tts.get("success") is False:
            result = refusal("tts_failed", f"text_to_speech failed: {tts.get('error') or tts}")
            write_log(s, text, result)
            return result

        playable = ensure_playable(Path(audio_path))
        ha.upload(s.media_name, playable.read_bytes(), PLAYABLE[playable.suffix.lower()])
        ha.play(s.speaker, s.media_name)
    except urllib.error.HTTPError as e:
        result = refusal("ha_error", f"Home Assistant returned HTTP {e.code}: "
                                     f"{e.read()[:200].decode(errors='replace')}")
        write_log(s, text, result)
        return result
    except (urllib.error.URLError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
        result = refusal("error", f"{type(e).__name__}: {e}")
        write_log(s, text, result)
        return result

    result = {"success": True, "spoken": True, "speaker": s.speaker, "chars": len(text),
              "file_path": str(audio_path)}
    write_log(s, text, result)
    return result
