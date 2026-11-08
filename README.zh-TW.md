# hermes-homepod-speak

讓 Hermes 的 agent 用自己的聲音，從 HomePod 開口說話的 plugin。

它只新增一個工具：`speak_aloud`。agent 自己判斷哪句話值得說出聲，寫成適合用聽的句子，再呼叫這個工具。接下來 plugin 會：

1. 檢查安靜規則、有沒有人在家，以及喇叭是不是正在播東西；
2. 用 Hermes 內建的 `text_to_speech` 生成語音，所以聲音就是你 `tts:` 設定裡的那個；
3. 把音檔上傳到 Home Assistant，從喇叭播出來。

只要有一項檢查沒過，就不會生成、也不會出聲。agent 會拿到原因，自己決定接下來怎麼做。

English: [README.md](README.md)

## 需要的東西

- Hermes 已經設定好 `text_to_speech`。任何供應商都可以，ElevenLabs 的自訂聲音效果很好。
- Home Assistant：
  - 喇叭要是一個 `media_player` 實體。HomePod 請用 **Apple TV** 整合建立的那個實體。
  - 本機媒體庫（`/media`）。Home Assistant OS 預設就有。
- `~/.hermes/.env` 裡要有 `HASS_TOKEN`（長期存取權杖），`HASS_URL` 可選。這兩個跟 Hermes 內建的 Home Assistant 整合用的是同一組變數。
- 只有在 TTS 輸出不是 MP3 或 WAV 時，才需要 `ffmpeg`。

## 安裝

```bash
hermes plugins install SmallYuanSY/hermes-homepod-speak --enable
```

然後在 `~/.hermes/config.yaml` 加上設定：

```yaml
plugins:
  entries:
    hermes-homepod-speak:
      settings:
        speaker: media_player.bedroom_homepod   # 必填
        presence: person.you                     # 選填：這個實體是 home 才說話
        max_chars: 200                           # 選填
        quiet_hours: "00:00-09:00"               # 選填：這段時間一律不說話
        quiet_when:                              # 選填：任一條成立時不說話
          - entity: sensor.your_phone_focus_name #   例如 HA app 回報的專注模式
            states: ["睡眠"]
          - input_boolean.speaker_dnd            #   只寫實體 ID＝它是 on 時安靜
        log_file: ~/.hermes/logs/speak_aloud.jsonl  # 選填（這是預設值）
        media_name: hermes-speak.mp3             # 選填：在 HA 媒體庫裡的檔名
```

重新啟動 gateway（或開一個新的 CLI session），工具才會註冊。設定是每次呼叫時才讀取，之後改設定會立即生效。

## 工具

`speak_aloud(text, force=false)`

| 結果 | 意思 |
|---|---|
| `{"success": true, "spoken": true, ...}` | 說出來了。 |
| `reason: "quiet_hours"` / `"quiet"` | 安靜規則生效中。 |
| `reason: "not_home"` | presence 實體不是 `home`，說了也沒人聽到。 |
| `reason: "speaker_busy"` | 喇叭正在播東西，說話會把它打斷。 |
| `reason: "speaker_unavailable"` | Home Assistant 連不到喇叭。 |
| `reason: "too_long"` / `"empty"` | 台詞要修改。 |
| `reason: "tts_failed"` | `text_to_speech` 出錯了（額度、網路等）。 |
| `reason: "config"` | 沒設定 `speaker` 或 `HASS_TOKEN`。 |
| `reason: "ha_error"` / `"error"` | 跟 Home Assistant 溝通時出了問題。 |

`force=true` 會略過「有沒有人在家」和「喇叭是否在播」這兩項檢查。工具說明會要求模型只在重要的事情上使用。

`speaker` 和 `HASS_TOKEN` 都設定好之後，這個工具才會出現。

## 安靜規則

agent 可能被排程叫醒，包括在你睡覺的時候。如果喇叭放在臥室，安靜規則能確保不管模型怎麼決定，它都不會出聲。**`force=true` 也越不過安靜規則。**

- `quiet_hours`：本地時間的時段，例如 `"00:00-09:00"`。可以跨過午夜（`"23:00-07:00"`）。
- `quiet_when`：一串 Home Assistant 實體。只要任何一個處於它列出的 `states`，就保持安靜。只寫實體 ID 的話，代表它是 `on` 時安靜。

`quiet_when` 讀的是 Home Assistant，所以任何能改實體狀態的東西都能讓 agent 安靜：HA app 回報的 iPhone 專注模式（`sensor.<手機>_focus_name`）、睡前自動化、儀表板上的開關，或是 agent 自己。

## 讓 agent 知道什麼時候該開口

這個 plugin 只提供「能說話」的能力。什麼時候說，屬於個性的一部分，請寫進你 agent 自己的 skill 或人設，例如：你到家時打招呼、提醒你去睡覺、一般聊天不要念出來。工具說明已經提醒模型，台詞要短，也不能有 markdown。

## 行為說明

- HA 媒體庫裡固定用同一個檔名，每次都覆蓋，所以媒體庫不會越堆越多。
- 不會調整喇叭的音量。
- 每一次嘗試，不管有沒有說出來，都會把台詞和結果記進 JSONL log。
- 每說一句，費用照你的 TTS 供應商計算。檢查會先跑，所以被擋下的台詞不花錢。

## 開發

```bash
python3 -m pytest tests/ -v
```

核心邏輯在 `homepod_speak.py`，不依賴 Hermes；`__init__.py` 負責接上 Hermes。

## 授權

MIT
