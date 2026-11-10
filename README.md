<div align="center">

<img src="shared/branding/icon.svg" width="96" height="96" alt="">

# Natro

### Your personal AI assistant, across devices.

**A voice-first assistant for Windows and Android that connects your apps, files, and online services—with a private server as its brain.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/PC-Windows-0078D4?logo=windows)](https://www.microsoft.com/windows)
[![Android](https://img.shields.io/badge/Android-14%2B-3DDC84?logo=android&logoColor=white)](https://developer.android.com/)
[![Python](https://img.shields.io/badge/Backend-Python-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Kotlin](https://img.shields.io/badge/Android-Kotlin-7F52FF?logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Tauri](https://img.shields.io/badge/Desktop-Tauri%202-24C8D8?logo=tauri&logoColor=white)](https://tauri.app/)

*Speak naturally. Get things done. Keep control.*

</div>

---

## Overview

Natro is a personal AI assistant that runs across your Windows PC and Android phone. It understands spoken requests—including Georgian speech mixed with English technical terms—and can use your devices, files, and connected services to carry out tasks.

The assistant's core runs on a server you control. Your devices connect to it over a private [Tailscale](https://tailscale.com/) network, and audio stays on the device until you ask for Natro: the phone listens for its wake phrase locally, and the PC records only while you use its talk key.

Natro is built around a simple principle: **automation should be useful, observable, and under your control.** Actions with meaningful consequences require confirmation, and routine commands can bypass the language model entirely.

## Highlights

- **Voice-first interaction** — say “OK Natro” or use a keyboard shortcut to make a request.
- **Cross-device assistance** — use Natro from Windows or Android, with a shared conversation. Each device has a switch, changed by tap or by voice, that makes requests made on it act on the other device.
- **PC control** — launch applications, search and inspect files, interact with the screen, and run approved system commands.
- **Android control** — open apps, manage alarms and media, interact with the screen, and work with contacts, calls, messages, and notifications.
- **Connected services** — integrate Google Calendar, Tasks, Gmail, Drive, Docs, Spotify, web search, and MCP tools.
- **Browser automation** — use your own Microsoft Edge session through Playwright.
- **File transfer** — send files between the PC and phone through the server, including when a device is temporarily offline.
- **Memory and notes** — retain useful context and keep longer notes as Markdown files.
- **Reminders and background jobs** — schedule reminders, request briefings, and let longer tasks run while you continue chatting.
- **Proactive notifications** — monitor selected events, such as messages from a particular contact or upcoming meetings.
- **File conversion** — convert common document, image, and media formats without sending the file to an AI model.
- **Spending controls** — set a monthly API budget and require approval before exceeding it.

## How it works

```mermaid
flowchart TB
    subgraph Devices
        PC[Windows PC app]
        Android[Android app]
    end

    subgraph PrivateNetwork[Private network · Tailscale]
        Server[ Natro server ]
    end

    subgraph ServerComponents[Server components]
        Voice[Speech recognition & translation]
        Agent[Agent · model routing · memory]
        Tools[Device tools · Google · Spotify · MCP]
        Guardrails[Confirmations · privacy rules · budget cap]
    end

    PC <--> Server
    Android <--> Server
    Server --> Voice
    Voice --> Agent
    Agent <--> Tools
    Agent <--> Guardrails
```

### A request, from voice to action

1. **Start locally.** On the PC, hold or tap the talk key (Ctrl+Alt+Space); Android listens for “OK Natro” with on-device keyword spotting. Audio is not sent before then.
2. **Send securely.** The device streams the request to the server over a WebSocket connection on your private Tailscale network.
3. **Recognize and translate.** Google Chirp 3 transcribes speech, and Claude converts Georgian or mixed-language input into clear English for the agent.
4. **Choose a model.** Natro routes routine requests to Claude Haiku and personal or complex requests to Claude Sonnet. The agent can escalate when a task gets stuck.
5. **Use tools.** The agent selects from server-side integrations and tools exposed by connected devices.
6. **Respond.** The answer returns to the requesting device and is shown or spoken aloud.

Common deterministic commands—such as controlling music or changing a PC setting—can skip the model call, reducing latency and cost.

## Privacy and control

Natro is designed to keep personal data and consequential actions behind explicit boundaries.

- **Private connectivity:** devices connect to the server over Tailscale rather than exposing the service directly to the public internet.
- **Local wake-word detection:** on Android, audio is not sent before the wake phrase is detected; the PC records only while its talk key is used.
- **Model routing:** requests go to Claude only: routine ones to Claude Haiku, and those involving personal data or risky actions, such as email, calendar, files, memory, or notifications, to Claude Sonnet.
- **Action confirmation:** actions such as sending, deleting, calling, buying, or force-closing an application require approval.
- **Banking and payment sites:** browser access is read-only.
- **Offline-device safeguards:** requests for an offline device wait for that device to reconnect and require approval before execution.
- **Budget limit:** Natro can warn when spending reaches 80% of the configured monthly cap and ask before exceeding it.
- **Local file conversion:** conversion runs without sending the file to a language model.

These controls depend on your configuration and the permissions granted to each integration. Review the settings and connected services before using Natro with sensitive accounts.

## Project status

Natro is actively developed and used day to day. The core assistant, the Windows desktop app, the Android app, device communication, and the main integrations are working.

| Component | Status |
|---|---|
| Server-side agent, memory, routing, and confirmations | Working |
| Windows desktop app (`desktop/`) | Working |
| Android assistant and device tools | Working |
| Google and Spotify integrations | Working |
| File transfer, reminders, and background jobs | Working |
| PC tools and spoken replies, in the desktop app itself (`desktop/src-tauri/`) | Working |

The PC app is Windows-only. The Android app targets Android 14+ on ARM64 devices and is installed directly rather than through the Play Store.

## Getting started

Natro is modular: start with text chat, then add voice, connected accounts, a server, and Android.

### Requirements

- Windows 10 or 11 PC
- [uv](https://docs.astral.sh/uv/) for Python environment management
- An [Anthropic API key](https://console.anthropic.com/)
- Optional: Google Cloud credentials, Spotify developer credentials, and API keys for additional integrations

### 1. Clone and configure

```powershell
git clone https://github.com/natro-ge/natro.git
cd natro
powershell -ExecutionPolicy Bypass -File desktop\setup-pc.ps1 -SkipModels
```

Add your Anthropic key to `.env`:

```dotenv
ANTHROPIC_API_KEY=your_key_here
```

Then edit `server/identity/NATRO.md` to personalize the assistant, and set `NATRO_TIMEZONE` in `.env`.

### 2. Run text chat

From the repository root:

```powershell
.venv\Scripts\python.exe -m natro_agent.chat
```

Text chat includes memory and notes. To test PC actions without executing them, use the supported `--dry-run-pc` option.

### 3. Enable voice on Windows

Run the setup script again without `-SkipModels` to download the voice for spoken replies (Kokoro, approximately 350 MB).

Configure Google Cloud Speech-to-Text:

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Cloud Speech-to-Text API**.
3. Create a service account with the **Cloud Speech Client** role.
4. Save its JSON key as `google-key.json` in the repository root and configure `GOOGLE_CLOUD_PROJECT` in `.env`.

Start the server, then the desktop app in another terminal (it needs Rust, Node.js, and the Visual Studio C++ build tools):

```powershell
.venv\Scripts\python.exe -m natro_agent.server
```

```powershell
cd desktop
npm install
npx tauri dev
```

Hold or tap Ctrl+Alt+Space and speak, or open the chat with Ctrl+Space to type. The app connects to `NATRO_SERVER` with `NATRO_DEVICE_TOKEN` from `.env`, and runs the PC's tools and spoken replies itself.

### Optional integrations

| Integration | Setup |
|---|---|
| Web search | Add `TAVILY_API_KEY` to `.env` |
| Weather | Set `NATRO_WEATHER_PLACE`; no API key required |
| Routes and travel times | Configure `GOOGLE_MAPS_API_KEY`, `NATRO_HOME_ADDRESS`, and `NATRO_TRAVEL_MODE` |
| Google Calendar, Tasks, Gmail, Drive, Docs | Enable the APIs, configure OAuth, and run `natro_agent.google_signin` |
| Spotify | Configure `SPOTIFY_CLIENT_ID` and run `natro_agent.spotify_signin`; Premium is required for playback control |
| Additional tools | Configure MCP servers in `server/config/mcp.json` |

Integration-specific setup, Android installation, and server deployment are described in the sections below and in the linked project documentation.

### Deploy the server

To keep Natro available while your PC is off, deploy the agent to an always-on Linux server. A small instance (around 2 vCPU and 4 GB RAM) is sufficient for the current setup.

1. Install Tailscale on the server, PC, and phone, using the same account.
2. Follow [`server/deploy/README.md`](server/deploy/README.md).
3. Configure the server to listen on its Tailscale address, not a public interface.
4. Set a strong `NATRO_DEVICE_TOKEN` and use the same token on connected devices.

Keep `.env`, API keys, OAuth tokens, and device tokens private. Never commit them to the repository.

### Build the Android app

Requirements: JDK 17+, Android SDK, platform 37, and build-tools 37.0.0.

Create `android/local.properties` with your SDK path and server connection:

```properties
sdk.dir=C\:\\Users\\you\\AppData\\Local\\Android\\Sdk
natro.server=ws://<server-tailscale-address>:8700
natro.token=<NATRO_DEVICE_TOKEN>
```

Add the server's Tailscale address to `android/app/src/main/res/xml/network_security_config.xml`, then build and install:

```powershell
android\gradlew.bat -p android assembleDebug
adb install -r android\app\build\outputs\apk\debug\app-debug.apk
```

On first launch, grant only the permissions required for the features you intend to use. The app's Settings screen includes setup steps and controls for its assistant capabilities.

## Repository layout

| Path | Purpose |
|---|---|
| `server/` | What runs on the server: agent loop, model routing, identity, memory, tools, device server, integrations, and tests (`natro_agent/`, `tests/`); speech recognition and translation (`natro_voice/`); Linux server setup and service deployment (`deploy/`) |
| `desktop/` | What runs on Windows: the Tauri 2 desktop app (a Rust core with the PC tools and voice output in `src-tauri/`, Svelte pages in `src/`); Windows setup script (`setup-pc.ps1`) |
| `android/` | Android app built with Kotlin and Jetpack Compose |
| `shared/` | The protocol between the server and the apps (`PROTOCOL.md`), and the icon (`branding/`) |
| `scripts/` | The icon's drawing script |

Runtime files such as `.env`, credentials, tokens, memory, notes, logs, and downloaded models are created locally and never go into git: on Windows they're in `%LOCALAPPDATA%\Natro`, on the server at the top of its checkout (git-ignored); `NATRO_HOME` sets either place.

## Development and tests

Install and configure the project using the setup steps above. The repository's golden tests exercise the real tool interfaces without calling paid APIs. They focus on privacy routing, action confirmations, and behavior when a device is offline.

Run the available test suites from the repository root:

```powershell
.venv\Scripts\python.exe -m pytest server
android\gradlew.bat -p android testDebugUnitTest
cd desktop\src-tauri; cargo test
```

## Customization

- **Assistant identity:** edit `server/identity/NATRO.md`.
- **Speech vocabulary:** add names and preferred spellings to `server/natro_voice/wordlist.txt`.
- **MCP tools:** configure server entries in `server/config/mcp.json`. Each entry can name the tools that require confirmation.
- **Budget and runtime settings:** configure them in `.env`.

## Current limitations

- The Windows application runs on Windows 10/11.
- The Android app targets Android 14+ on ARM64 and is distributed as a direct install.
- Spoken requests can be in Georgian with English terms mixed in; spoken replies are currently in English.
- Spotify playback control requires Premium and is subject to Spotify's personal-app API limitations.
- The Tauri desktop app is still under development.

## License

Natro is released under the [MIT License](LICENSE).
