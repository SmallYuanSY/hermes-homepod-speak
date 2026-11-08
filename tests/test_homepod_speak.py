"""Unit tests for homepod_speak (no Hermes, no network).

Run from the plugin root:
    python3 -m pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime as dt  # noqa: E402

from homepod_speak import (  # noqa: E402
    QuietRule, Settings, ensure_playable, in_window, load_settings, parse_quiet_when, speak)


class FakeHA:
    def __init__(self, states=None):
        self.states = states or {}
        self.uploads, self.plays = [], []

    def state(self, entity_id):
        return self.states[entity_id]

    def upload(self, name, audio, content_type):
        self.uploads.append((name, audio, content_type))

    def play(self, speaker, name):
        self.plays.append((speaker, name))


@pytest.fixture
def mp3(tmp_path):
    p = tmp_path / "line.mp3"
    p.write_bytes(b"ID3fake")
    return p


@pytest.fixture
def settings(tmp_path):
    return Settings(speaker="media_player.bedroom", presence="person.me", ha_token="t",
                    log_file=tmp_path / "log.jsonl")


def idle_home():
    return FakeHA({"person.me": {"state": "home"}, "media_player.bedroom": {"state": "idle"}})


def tts_ok(path):
    calls = []

    def synth(text):
        calls.append(text)
        return json.dumps({"success": True, "file_path": str(path)})
    synth.calls = calls
    return synth


def log_lines(s):
    return [json.loads(line) for line in s.log_file.read_text().splitlines()]


def test_happy_path_uploads_and_plays(settings, mp3):
    ha, synth = idle_home(), tts_ok(mp3)
    r = speak("おかえり", False, settings, ha, synth)
    assert r["success"] and r["spoken"]
    assert synth.calls == ["おかえり"]
    assert ha.uploads == [("hermes-speak.mp3", b"ID3fake", "audio/mpeg")]
    assert ha.plays == [("media_player.bedroom", "hermes-speak.mp3")]
    assert log_lines(settings)[-1]["result"] == "spoken"


@pytest.mark.parametrize("states, reason", [
    ({"person.me": {"state": "not_home"}, "media_player.bedroom": {"state": "idle"}}, "not_home"),
    ({"person.me": {"state": "home"},
      "media_player.bedroom": {"state": "playing", "attributes": {"media_title": "Song"}}}, "speaker_busy"),
    ({"person.me": {"state": "home"}, "media_player.bedroom": {"state": "unavailable"}}, "speaker_unavailable"),
])
def test_refusals_happen_before_any_audio_is_generated(settings, mp3, states, reason):
    ha, synth = FakeHA(states), tts_ok(mp3)
    r = speak("hi", False, settings, ha, synth)
    assert not r["success"] and r["reason"] == reason
    assert synth.calls == [] and ha.plays == []
    assert log_lines(settings)[-1]["reason"] == reason


def test_force_skips_checks(settings, mp3):
    ha = FakeHA({"media_player.bedroom": {"state": "playing"}})  # no presence entity at all
    r = speak("important", True, settings, ha, tts_ok(mp3))
    assert r["spoken"] and ha.plays


def test_presence_is_optional(settings, mp3):
    settings.presence = None
    ha = FakeHA({"media_player.bedroom": {"state": "idle"}})
    assert speak("hi", False, settings, ha, tts_ok(mp3))["spoken"]


def test_too_long_and_empty(settings, mp3):
    settings.max_chars = 5
    assert speak("123456", False, settings, idle_home(), tts_ok(mp3))["reason"] == "too_long"
    assert speak("   ", False, settings, idle_home(), tts_ok(mp3))["reason"] == "empty"


def test_missing_config(mp3):
    s = Settings(speaker="", ha_token="")
    assert speak("hi", False, s, idle_home(), tts_ok(mp3))["reason"] == "config"


def test_tts_failure_is_reported(settings):
    r = speak("hi", False, settings, idle_home(), lambda t: json.dumps({"error": "quota exceeded"}))
    assert r["reason"] == "tts_failed" and "quota exceeded" in r["message"]


def test_ensure_playable_keeps_mp3(mp3):
    assert ensure_playable(mp3) == mp3


def test_load_settings_defaults_and_env(tmp_path):
    cfg = {"speaker": "media_player.x"}
    s = load_settings(lambda k, d=None: cfg.get(k, d),
                      {"HASS_TOKEN": "tok", "HASS_URL": "http://ha:8123/", "HERMES_HOME": str(tmp_path)})
    assert s.speaker == "media_player.x" and s.presence is None
    assert s.ha_url == "http://ha:8123" and s.ha_token == "tok"
    assert s.log_file == tmp_path / "logs" / "speak_aloud.jsonl"


# --- quiet rules -------------------------------------------------------------

def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(2026, 1, 1, h, m)


@pytest.mark.parametrize("now, quiet", [("00:30", True), ("08:59", True), ("09:00", False), ("23:00", False)])
def test_quiet_hours(settings, mp3, now, quiet):
    settings.quiet_hours = "00:00-09:00"
    synth = tts_ok(mp3)
    r = speak("hi", False, settings, idle_home(), synth, now=at(now))
    assert (r.get("reason") == "quiet_hours") is quiet
    assert (synth.calls == []) is quiet


def test_quiet_hours_wrap_past_midnight():
    t = dt.time
    assert in_window(t(23, 30), t(23, 0), t(7, 0)) and in_window(t(6, 59), t(23, 0), t(7, 0))
    assert not in_window(t(7, 0), t(23, 0), t(7, 0)) and not in_window(t(12, 0), t(23, 0), t(7, 0))


def test_force_does_not_bypass_quiet(settings, mp3):
    settings.quiet_hours = "00:00-09:00"
    assert speak("urgent", True, settings, idle_home(), tts_ok(mp3), now=at("03:00"))["reason"] == "quiet_hours"
    settings.quiet_hours = None
    settings.quiet_when = [QuietRule("input_boolean.dnd")]
    ha = idle_home()
    ha.states["input_boolean.dnd"] = {"state": "on"}
    assert speak("urgent", True, settings, ha, tts_ok(mp3))["reason"] == "quiet"


def test_quiet_when_matches_listed_states_only(settings, mp3):
    settings.quiet_when = [QuietRule("sensor.phone_focus_name", ("Sleep",))]
    ha = idle_home()
    ha.states["sensor.phone_focus_name"] = {"state": "Work"}
    assert speak("hi", False, settings, ha, tts_ok(mp3))["spoken"]
    ha.states["sensor.phone_focus_name"] = {"state": "Sleep"}
    r = speak("hi", False, settings, ha, tts_ok(mp3))
    assert r["reason"] == "quiet" and r["entity"] == "sensor.phone_focus_name"


def test_bad_quiet_hours_is_a_config_error(settings, mp3):
    settings.quiet_hours = "late night"
    assert speak("hi", False, settings, idle_home(), tts_ok(mp3))["reason"] == "config"


def test_parse_quiet_when_forms():
    rules = parse_quiet_when(["input_boolean.dnd", {"entity": "sensor.f", "states": "Sleep"},
                              {"entity": "sensor.g", "states": ["A", "B"]}, {"nope": 1}])
    assert [(r.entity, r.states) for r in rules] == [
        ("input_boolean.dnd", ("on",)), ("sensor.f", ("Sleep",)), ("sensor.g", ("A", "B"))]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_ensure_playable_transcodes_ogg_to_mp3(tmp_path):
    import subprocess
    ogg = tmp_path / "voice.ogg"  # what Opus-preferring platforms (e.g. Telegram) get from text_to_speech
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.3", "-c:a", "libopus", str(ogg)], check=True)
    out = ensure_playable(ogg)
    assert out.suffix == ".mp3" and out.stat().st_size > 0
