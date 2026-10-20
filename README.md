# Natro

Natro is a personal voice assistant, like Google Assistant but your own. She
lives on your Windows PC and your Android phone, with an always-on "brain" on
a small server you control. Say "OK Natro" and ask in Georgian (English words
mixed in are fine). She works out what you mean, acts on your PC, your phone
and your online accounts, and answers on screen and out loud, in English.

- **On the PC:** open and close apps; find, read and open files anywhere on
  the PC; run PowerShell commands for its settings and questions about it
  (dark mode, Bluetooth, IP address, disk space): harmless ones right away,
  the rest after your yes; use any app through its screen (read, click,
  type, press keys, scroll), as on the phone.
- **On the phone:** be the phone's assistant (long-press the power button) and
  listen for "OK Natro" or "Hey Natro"; open apps, set alarms and timers,
  control media, the flashlight and volume; read notifications and texts,
  find contacts, call and text; use any app through its screen (read, tap,
  type, scroll).
- **Google:** Calendar, Tasks, Gmail (read, and send after you say yes),
  Drive (read) and Docs.
- **Spotify:** play any song, album, artist, playlist or podcast; pause, skip,
  volume, queue, like; make playlists and fill them, many songs at once,
  without playing them; on whichever device has Spotify open.
- **Files between the PC and the phone** (up to 1 GB): ask her ("send the
  invoice to my phone", "send my last photo to the PC"), share to "Send to
  PC" on the phone, or right-click > Send to > "Phone (Natro)" on the PC. They
  land in Downloads. The server only passes them along: a file waits there
  for a device that's offline (up to 7 days) and is deleted once it arrives.
- **One chat on the PC and the phone:** what you ask on either shows on both,
  with her replies, until you start a new chat on either. It lives on the
  server, in memory only. "Show me my PC screen" (or the phone's) puts a
  screenshot in it. She sees pictures: send one into it and ask "what's
  this?", "read this receipt" or "what does this error mean?", and she can
  take a screenshot to look at a screen herself.
  When she asks before something risky, you can answer on either: the phone
  shows the PC's question as a notification with Yes and No, and the PC's
  reply box shows the phone's.
- **Requests wait for a device that's offline:** ask on the phone for
  something on the PC while it's off, and when the PC is back it asks you
  whether to run it (Run or Skip; up to 24 hours). The same the other way.
- **Memory and notes:** she remembers what you tell her and preferences she
  notices herself; longer notes are Markdown files.
- **Reminders and a briefing:** "remind me in 20 minutes to check the oven",
  "every Monday at 9", "when my PC is back": at that moment she says it and
  shows it on your devices, with no model call. "Brief me" gives the
  weather, what's left on your calendar, tasks due, reminders still to come
  and new email, in a few sentences.
- **Background jobs:** long work ("in the background, compare flights to
  Berlin for every weekend in November") runs while you go on talking to
  her. "What are you working on?" and "cancel that" work on it, and when
  it's done she tells you the answer, with the details in the chat.
- **She keeps an eye out:** "always tell me when Nino emails", "let me know
  right away when the bank texts me", and a heads-up ten minutes before each
  meeting, or ten minutes before you have to leave for one further away,
  with the trip from where your phone is. The checks are cheap rules
  with no model call, and the phone matches its own notifications, so only the
  ones you asked about leave it.
- **The phone's messages on the PC:** a message that comes to the phone
  (WhatsApp, Messenger, Telegram, texts...) shows in the PC's reply box; its
  Reply opens the chat, and what you type goes from the phone, in that app,
  with no model in between. Or ask her: "reply to Nino that I'm on my way".
  A switch in the phone's settings turns it off.
- **Your own browser:** she uses your Edge, logged in as you, through
  Playwright and Microsoft's Playwright Extension: open a site, read the page,
  click and type, in tabs of her own. What sends, buys or deletes asks first;
  bank and payment sites are read only.
- **Getting places:** "how long to the airport?", "when should I leave for the
  dentist?", by car with traffic or by public transport with its lines
  (Google's Routes API). "What are my options?" compares up to five ways,
  each step by step: the walks, lines, stops, times, changes and fare;
  fewer changes, less walking, or there by a set time. Your own route, leg by
  leg: "the metro to Station Square, then the bus to the airport", each leg
  leaving when you get there. With Google Maps links to follow it on the
  phone.
- **File conversion:** "make the newest PDF in my Downloads a Word file",
  "this video as an MP3". The server converts it (LibreOffice, pandoc,
  Pillow, ffmpeg) and the result lands in Downloads; nothing is kept, and no
  model sees the file.
- **She remembers earlier conversations:** "what was that restaurant I asked
  about on Tuesday?" searches what you asked and what she answered before.
- **Web search, whole web pages** (she reads a link in full, and never a
  private address), **the weather** (Open-Meteo, no key), and any other tools you
  plug in as MCP servers.
- **Asks before anything risky:** deleting, sending, calling, force-closing
  an app, or a tap that sends, buys or deletes waits for your yes (by voice,
  hotkey or tap).
- **A monthly spending cap:** she warns at 80% and asks before spending more.

## How it works

```
        ┌─────────────────────────── Server (always on) ───────────────────────────┐
        │  Voice pipeline: Google Chirp 3 recognition → Claude translates to English │
        │  Agent (the brain): Claude Haiku / Sonnet · identity · memory · notes      │
        │  routing · confirmations · spending cap                                    │
        │  Cloud tools: Google, Spotify, web search, memory, notes, MCP servers      │
        └───────────▲─────────────────────────────────────────────▲─────────────────┘
          Tailscale │ (private network, no public ports)          │
     ┌──────────────┴──────────────┐                ┌──────────────┴──────────────┐
     │ PC app (Windows)            │                │ Android app                 │
     │ "OK Natro" checked locally  │                │ "OK Natro" / long-press     │
     │ records → sends audio       │                │ records → sends audio       │
     │ PC tools: apps, files       │                │ phone tools: apps, alarms,  │
     │ speaks replies (Kokoro)     │                │ media, texts, screen control│
     └─────────────────────────────┘                │ speaks replies (Android TTS)│
                                                    └─────────────────────────────┘
```

A request, step by step:

1. **Wake word, on the device.** The PC listens with a local Whisper model
   (whisper-base via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)), the
   phone with sherpa-onnx keyword spotting. Nothing leaves the device before
   "OK Natro", and recordings without speech are never sent.
2. **To the server.** The device records until you stop talking and sends the
   audio over a WebSocket. Devices reach the server only over
   [Tailscale](https://tailscale.com) and must send a shared token.
3. **Speech to English.** Google's Chirp 3 recognizes the Georgian (and English)
   speech; Claude turns it into accurate English, helped by a list of the names
   and terms you use.
4. **The agent decides.** Plain requests ("open Chrome", "play Yellow",
   "weather tomorrow") and anything about the PC's screen go to Claude Haiku,
   which is quick and cheap. A request that needs anything personal (email,
   calendar, files, memory, notifications) or a risky action goes to Claude
   Sonnet, which keeps the rest of that conversation; Sonnet also takes over
   whenever Haiku gets stuck. Tests check this.
5. **Tools.** The server has its own tools (memory, notes, web search, Google,
   Spotify, MCP servers) and each connected device offers its own. Natro
   knows which devices are online and says so when one isn't.
6. **The reply** streams back to the device that asked, which shows it and
   speaks it: Kokoro (offline) on the PC, the phone's own text-to-speech on
   Android. Common commands ("pause the music", "next song") skip the model
   entirely and take a fraction of a second.

Cost: a spoken request usually costs about a cent or less (roughly $0.001 to
$0.005 for the voice and $0.001 to $0.03 for the agent). The cap is set in
`.env`.

## What's in this repository

| Folder | What it is | Runs on |
|---|---|---|
| `voice/` | Speech recognition and translation (`natro_voice`) | Server |
| `agent/` | The brain: agent loop, identity, routing, tools, device server (`natro_agent`) | Server |
| `pc/` | The PC app: wake word, recording, PC tools, spoken replies, tray app (`natro_pc`) | Windows PC |
| `desktop/` | A new PC app in progress (Tauri): a glass dock at the top of the screen that shows what Natro is doing (Ctrl+Alt+Space), and a chat to type to her at the bottom (Ctrl+Space); it will replace `pc/` | Windows PC |
| `android/` | The Android app (Kotlin, Jetpack Compose) | Android 14+ phone (arm64) |
| `deploy/` | Putting the brain on a Linux server: systemd service, backups | Server |
| `scripts/` | Setting up a Windows PC | PC |

Git-ignored and created as you go: `.env`, key files, `tokens/` (sign-ins),
`memory/`, `notes/`, `logs/`, `models/`.

## Progress

Natro is in daily use and grows a piece at a time. Where it stands
(October 2026):

**Working**

- The brain on a server: speech to English, the agent on Claude (Haiku for
  plain requests, Sonnet for personal or risky ones), memory and notes,
  confirmations, the spending cap; Google, Spotify, web search and MCP servers.
- The PC app (`pc/`): "OK Natro", the talk key, apps and files, spoken
  replies, the tray.
- The Android app: the phone's assistant, "OK Natro" and "Hey Natro", a
  bubble over other apps (drag it to an edge to park it, down to the ✕ to
  cancel), the phone's tools and any app through its screen, settings with an
  off switch for all of Natro.
- Files between the PC and the phone, up to 1 GB, through the server.
- Speed: the commonest music and PC commands skip the model (0.2 to 0.4 s;
  "lock the PC", "dark mode on", "open Downloads"), and a simple one-tool
  request answers with the tool's own result. Requests about the PC or a
  screen, and those that come to use the PC's screen, go to Claude Haiku
  (about 20 times cheaper), with Sonnet taking over when Haiku gets stuck.
  The PC's screen is read through Windows UI Automation in about 0.3 s.
- Tests: the golden list (privacy, confirmations, offline devices) over the
  real tools, and per-request records (recording and log) to measure how
  often she gets it right.

**In progress: the new desktop app** (`desktop/`, Tauri 2: Rust and Svelte)

- Done: the glass dock at the top (Ctrl+Alt+Space) that shows each step with
  the app's own icon ("Opening Spotify", "Playing Yellow") and takes a click
  to answer her questions; the chat (Ctrl+Space, or Alt tapped twice), a
  glass box rising from the bottom with the conversation above it as bubbles,
  typed or spoken, answered as text; files to and from the phone (Explorer's
  Send to, the tray); a Verbose switch that keeps each request for testing; a
  Settings window with the switches (Start with Windows among them), the
  keybinds and the API keys behind a PIN, to change them without opening the
  server.
- Next: the PC tools and Kokoro in Rust (no Python helper), the wake word and
  a better end-of-speech detector (Silero VAD). Then it replaces `pc/`.

**Later:** a Georgian voice for her replies, a more playful persona, and
deeper app control (the browser through Playwright, for one).

## Setup

Natro is made to grow one piece at a time: start with the typed chat, then add
voice, accounts, a server and the phone.

**You need:** a Windows 10/11 PC, [uv](https://docs.astral.sh/uv/)
(`winget install astral-sh.uv`; it installs Python 3.13 for you), and an
[Anthropic API key](https://console.anthropic.com). Everything else is
optional and listed in the step that needs it.

### 1. Try it typed

```powershell
git clone https://github.com/tuxa4life/Natro.git
cd Natro
powershell -ExecutionPolicy Bypass -File scripts\setup-pc.ps1 -SkipModels
```

The script makes `.venv` with all three Python programs and copies
`.env.example` to `.env`. Put your key in `.env` (`ANTHROPIC_API_KEY=...`), and
tell Natro who you are: in `agent/identity/NATRO.md`, replace "Your Name" and
adjust the rest to taste. Set `NATRO_TIMEZONE` to yours. Then, from the top of
the repository (like every command here):

```powershell
.venv\Scripts\python.exe -m natro_agent.chat
```

Type in English. She has memory and notes already; `--dry-run-pc` adds a
pretend PC whose tools only say what they would do. Each reply ends with the
model, the time and the cost.

**Free extras for `.env`:** `TAVILY_API_KEY`
([tavily.com](https://tavily.com), 1,000 free searches a month) turns on web
search. `GOOGLE_MAPS_API_KEY` (a Google Cloud key for the Routes API) turns on
travel time and routes, from `NATRO_HOME_ADDRESS` by `NATRO_TRAVEL_MODE` (drive, transit,
walk or bicycle). The weather needs no key; `NATRO_WEATHER_PLACE` is where you live
(default Tbilisi).

### 2. Talk to her on the PC

1. Run the setup script again without `-SkipModels`: it downloads the wake
   word and voice models (about 500 MB) into `models/`.
2. In [Google Cloud](https://console.cloud.google.com), make a project, enable
   the **Cloud Speech-to-Text API**, and create a service account with the
   "Cloud Speech Client" role. Save its JSON key as `google-key.json` at the top
   of the repository and set `GOOGLE_CLOUD_PROJECT` in `.env`. Google gives
   60 free minutes a month; after that it's about $1 per hour of speech.
3. Start the brain and the PC app, each in its own terminal:

   ```powershell
   .venv\Scripts\python.exe -m natro_agent.server
   ```

   ```powershell
   .venv\Scripts\python.exe -m natro_pc.app --wake
   ```

   After a short pause, say "OK Natro" and your request. Without `--wake`,
   press Enter to record; `--type` lets you type to the running service.
4. Or use the tray app instead of the console app:
   `.venv\Scripts\pythonw.exe -m natro_pc.tray`. Hold Ctrl+Alt+Space and talk,
   or tap it and talk hands-free; the icon shows whether Natro is online.

**For file search,** install [Everything](https://www.voidtools.com) and its
command line (`winget install voidtools.Everything voidtools.Everything.Cli`).
File contents are searched with the Windows Search index.

**Another language?** The recognizer's languages are in
`voice/natro_voice/speech.py` and the translation instructions in
`voice/natro_voice/translate.py`. Add the names and terms you use to
`voice/wordlist.txt`.

### 3. Google: Calendar, Tasks, Gmail, Drive and Docs

1. In the same Google Cloud project, enable the Google Calendar, Tasks, Gmail,
   Drive and Docs APIs.
2. Set up the OAuth consent screen as **External** and then **publish it ("In
   production")**. In "Testing", Google ends the sign-in after 7 days. Google
   shows an "unverified app" warning once; that's expected for a personal app.
3. Create an OAuth client of type **Desktop app** and save its JSON as
   `google-oauth-client.json` at the top of the repository.
4. Sign in once: `.venv\Scripts\python.exe -m natro_agent.google_signin`. It
   writes `tokens/google.json`; Natro offers the Google tools once it's there.

Permissions are kept narrow: Gmail read and send, Drive read-only.

### 4. Spotify (Premium)

1. At [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   create an app with the Web API and the redirect URI
   `http://127.0.0.1:8899/callback`.
2. Put its Client ID in `.env` as `SPOTIFY_CLIENT_ID`.
3. Sign in once: `.venv\Scripts\python.exe -m natro_agent.spotify_signin`. It
   writes `tokens/spotify.json`. Spotify ends a sign-in after six months; Natro
   reminds you in the last two weeks.

### 5. Put the brain on a server

So that Natro works with the PC off, the brain runs on an always-on Linux
server (2 vCPU and 4 GB are plenty; Natro uses about 100 MB).

1. Install [Tailscale](https://tailscale.com) on the server, the PC and the
   phone, on the same account.
2. Follow [`deploy/README.md`](deploy/README.md): a user, the code, a venv, the
   service. Copy `.env`, `google-key.json` and `tokens/` to the server. Set
   `NATRO_LISTEN` to the server's Tailscale address (`tailscale ip -4`) with
   `:8700`, so it is never reachable from the internet, and make a long random
   `NATRO_DEVICE_TOKEN`.
3. On the PC, `.env` then needs only `NATRO_SERVER=ws://<server's Tailscale
   address>:8700` and the same `NATRO_DEVICE_TOKEN`.

### 6. The Android app

You need a JDK 17 or newer and the Android SDK (Android Studio, or just the
command-line tools with platform 37 and build-tools 37.0.0).

1. Create `android/local.properties`:

   ```properties
   sdk.dir=C\:\\Users\\you\\AppData\\Local\\Android\\Sdk
   natro.server=ws://<server's Tailscale address>:8700
   natro.token=<NATRO_DEVICE_TOKEN>
   ```

2. Put the server's Tailscale address in
   `android/app/src/main/res/xml/network_security_config.xml` too: the app
   allows unencrypted WebSockets only to that address (Tailscale encrypts the
   traffic itself).
3. Build and install over USB (USB debugging on):

   ```powershell
   android\gradlew.bat -p android assembleDebug
   adb install -r android\app\build\outputs\apk\debug\app-debug.apk
   ```

   The first build downloads sherpa-onnx and the wake word model.
4. Open Natro on the phone, then Settings (top right) > Set up: microphone
   and notifications, the assistant role, display over other apps (for the
   bubble), contacts, calls and texts, notification access, screen control,
   and all files access (to find and send the phone's files when you ask). Settings > Test the wake word shows which of your tries she
   catches. Her replies open from the bubble as text (tap to fold them back);
   Settings > "Show replies as text" turns that off.

## Make her yours

- `agent/identity/NATRO.md`: who she is, who you are, and her house rules.
- `voice/wordlist.txt`: names, brands and terms, spelled the way they should
  come out in English.
- `agent/config/mcp.json`: more tools as MCP servers. Each entry has a
  `command` (or a `url`), and says whether its results are `personal` (Claude
  only) and which tools need your yes (`confirm_tools`). `${NAME}` takes a
  value from `.env`. See `agent/natro_agent/mcp_client.py`.
- `.env`: the monthly budget, time zone, voice and talk key.

## Tests

None of them call a paid API. `agent/tests/test_golden.py` is the golden list:
it checks over Natro's real tools that personal data never reaches the free
tier, that risky actions always ask, and that offline devices are reported.

```powershell
.venv\Scripts\python.exe -m pytest agent
.venv\Scripts\python.exe -m pytest pc
android\gradlew.bat -p android testDebugUnitTest
cd desktop\src-tauri; cargo test
```

## Limits

- The PC app is Windows only. The phone app needs Android 14 or newer on an
  arm64 phone, and is installed directly, not from the Play Store.
- She hears Georgian with English mixed in, and replies in English.
- Spotify control needs Premium. Spotify's rules for personal apps leave out
  recommendations and Spotify's own playlists (On Repeat, Discover Weekly).

## License

[MIT](LICENSE).
