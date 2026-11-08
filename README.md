# hermes-homepod-speak

A Hermes plugin that lets your agent say things out loud on a HomePod, in its own voice.

It adds one tool, `speak_aloud`. The agent decides when a line is worth saying out loud, writes it for the ear, and calls the tool. The plugin then:

1. checks your quiet rules, that someone is home, and that the speaker is not already playing something,
2. generates the line with Hermes' own `text_to_speech` tool, so the voice is whatever your `tts:` config uses,
3. uploads the audio to Home Assistant and plays it on the speaker.

If a check fails, nothing is generated and nothing is spoken. The agent gets a reason back and can decide what to do instead.

中文文檔：[README.zh-TW.md](README.zh-TW.md)

## Requirements

- Hermes with `text_to_speech` configured (any provider; a custom ElevenLabs voice works well).
- Home Assistant with:
  - the speaker as a `media_player` entity. For a HomePod, use the entity created by the **Apple TV** integration.
  - the local media source (`/media`), which Home Assistant OS enables by default.
- `HASS_TOKEN` (a long-lived access token) and optionally `HASS_URL` in `~/.hermes/.env`. These are the same variables Hermes' Home Assistant integration uses.
- `ffmpeg` only if your TTS provider produces something other than MP3 or WAV.

## Install

```bash
hermes plugins install SmallYuanSY/hermes-homepod-speak --enable
```

Then add the settings to `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    hermes-homepod-speak:
      settings:
        speaker: media_player.bedroom_homepod   # required
        presence: person.you                     # optional: only speak when this is "home"
        max_chars: 200                           # optional
        quiet_hours: "00:00-09:00"               # optional: never speak in this window
        quiet_when:                              # optional: never speak while any of these match
          - entity: sensor.your_phone_focus_name #   e.g. the HA companion app's Focus sensor
            states: ["Sleep"]
          - input_boolean.speaker_dnd            #   bare entity id = quiet while it is "on"
        log_file: ~/.hermes/logs/speak_aloud.jsonl  # optional (this is the default)
        media_name: hermes-speak.mp3             # optional: file name in HA's media library
```

Restart the gateway (or start a new CLI session) so the tool is registered. Settings are read on every call, so later changes apply right away.

## The tool

`speak_aloud(text, force=false)`

| Result | Meaning |
|---|---|
| `{"success": true, "spoken": true, ...}` | Said out loud. |
| `reason: "quiet_hours"` / `"quiet"` | A quiet rule is active. |
| `reason: "not_home"` | The presence entity is not `home`, so nobody would hear it. |
| `reason: "speaker_busy"` | The speaker is playing something; speaking would interrupt it. |
| `reason: "speaker_unavailable"` | Home Assistant cannot reach the speaker. |
| `reason: "too_long"` / `"empty"` | Fix the line. |
| `reason: "tts_failed"` | `text_to_speech` returned an error (quota, network, ...). |
| `reason: "config"` | `speaker` or `HASS_TOKEN` is missing. |
| `reason: "ha_error"` / `"error"` | Something went wrong talking to Home Assistant. |

`force=true` skips the home and busy checks. The tool description asks the model to use it only for important things.

The tool only appears when `speaker` and `HASS_TOKEN` are both set.

## Quiet rules

Agents can wake up on a schedule, including while you sleep. If the speaker is in your bedroom, quiet rules make sure it stays silent no matter what the model decides. **`force=true` does not override them.**

- `quiet_hours`: a local-time window such as `"00:00-09:00"`. Windows can wrap past midnight (`"23:00-07:00"`).
- `quiet_when`: a list of Home Assistant entities. The agent stays quiet while any of them is in one of its `states`. A bare entity id means quiet while it is `on`.

Because `quiet_when` reads Home Assistant, anything that can set an entity can mute the agent: an iPhone Focus reported by the HA companion app (`sensor.<phone>_focus_name`), a bedtime automation, a dashboard toggle, or the agent itself.

## Giving your agent a sense of when to speak

The plugin only provides the ability. When to use it is a matter of personality, so put that in your agent's own skill or persona. For example: greet them when they get home, remind them to sleep, do not narrate ordinary chat. The tool description already tells the model to keep lines short and free of markdown.

## Behavior notes

- One file name in Home Assistant's media library is overwritten on every call, so the library does not fill up.
- The speaker's volume is never changed.
- Every attempt, spoken or not, is appended to the JSONL log with the text and the outcome.
- Each spoken line costs whatever your TTS provider charges. Checks run first, so a refused line costs nothing.

## Development

```bash
python3 -m pytest tests/ -v
```

The core logic lives in `homepod_speak.py` and has no Hermes imports. `__init__.py` wires it to Hermes.

## License

MIT
