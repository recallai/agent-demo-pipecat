# Pipecat meeting agent

A small agent demo featuring [Recall.ai's Output Media API](https://docs.recall.ai/docs/stream-media) and [Pipecat](https://docs.pipecat.ai/). It includes an interactive Simli avatar, native [OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations) speech-to-speech, and nine tool calls.

## Behavior

Address the avatar as **“Assistant”** or **“pipecat demo”**, followed by a question or command. The agent joins silently and posts an introduction in chat. Tool calls post their results in chat and may include a brief spoken acknowledgment.

| Action | Tool |
| --- | --- |
| Start or replace a screen share with a public webpage | `start_screenshare(url)` |
| Post text in meeting chat | `send_chat_message(message)` |
| Post the exact last accepted page URL | `send_current_page_link()` |
| Stop the screen share | `stop_screenshare()` |
| Leave the meeting | `leave_call()` |
| Silence speech; keep tools available | `mute_agent()` |
| Enable speech | `unmute_agent()` |
| Stop listening for requests | `stop_listening()` |
| Start listening for requests; preserve the mute setting | `start_listening()` |

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Recall workspace and its regional REST API key.
- An OpenAI project API key with **Model capabilities → Request** access to Realtime and `gpt-4o-mini-transcribe`.
- A Simli API key and a face available to that account.
- An HTTPS tunnel with WebSocket support, such as cloudflared.
- A Google account to host the meeting.

## Quickstart

Set up and run the sample locally.

### 1. Clone the repository and install dependencies

```sh
git clone https://github.com/recallai/agent-demo-pipecat.git
cd agent-demo-pipecat
uv sync --locked
```

### 2. Configure the environment

Create the local configuration file:

```sh
cp .env.example .env
```

Fill in the root `.env`. It is ignored by Git and never sent to the browser. Existing process environment variables take precedence.

| Variable | Value |
| --- | --- |
| `RECALL_API_KEY` | REST API key for the Recall workspace. |
| `RECALL_API_BASE_URL` | Matching regional API URL, such as `https://us-west-2.recall.ai/api/v1`. |
| `OPENAI_API_KEY` | OpenAI project key. |
| `REALTIME_MODEL` | Realtime model with native audio and tool calling; the example uses `gpt-realtime-2.1`. |
| `REALTIME_REASONING_EFFORT` | `low` in the example; see [.env.example](.env.example) for options. |
| `REALTIME_VOICE` | Realtime voice; the example uses `marin`. |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | Simli account key and avatar face ID. |
| `PIPECAT_PUBLIC_URL` | The tunnel's HTTPS origin, without a path or trailing slash. |
| `SESSION_TIMEOUT_SECONDS` | Overall session limit; defaults to `600` seconds. |

Restart the app and launch a new bot after changing `.env`. No provider credentials need to be entered in the Recall dashboard.

### 3. Start an HTTPS tunnel

With [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installed:

```sh
cloudflared tunnel --url http://127.0.0.1:7861
```

Copy the tunnel's HTTPS origin into `PIPECAT_PUBLIC_URL` in `.env`. Keep this terminal running.

Only tunnel the media server on port **7861**. Keep the launcher on port **8001** local.

### 4. Start the app

Open another terminal in the repository root:

```sh
uv run --locked python -m app.web
```

Keep this process running alongside the tunnel, then open [the launcher](http://127.0.0.1:8001).

### 5. Send the agent to a meeting

Create and join a meeting at [meet.new](https://meet.new/). Paste its URL into the launcher and select **Send agent**, then admit **pipecat demo** once it enters the waiting room.

### 6. Try the agent

Try these phrases. Each tool call also posts its result in meeting chat.

| Try saying |
| --- |
| “Assistant, what is two plus two?” |
| “Assistant, show https://example.com.” |
| “Assistant, send the shared webpage link in chat.” |
| “Assistant, mute.” |
| “Assistant, post Hello from the demo in chat.” |
| “Assistant, stop sharing.” |
| “Assistant, unmute.” |
| “Assistant, stop listening.” |
| “Assistant, start listening.” |
| “Assistant, leave the meeting.” |

After the bot leaves, stop both terminal processes with Ctrl+C.

The session defaults to 10 minutes; launch another bot to repeat the demo.

## Code map

| File | Responsibility |
| --- | --- |
| `app/web.py` | Serve the form, create a Recall bot, and run both HTTP servers. |
| `app/templates/` | Launcher form, avatar playback, and microphone capture. |
| `app/agent.py` | Instructions, Realtime configuration, avatar startup, and cleanup. |
| `app/meeting_agent.py` | Admit addressed requests and stream Realtime replies. |
| `app/meeting_tools.py` | Nine tools, listening/mute state, and the current page URL. |
| `app/speech_output.py` | Suppress muted and interrupted audio before and after the avatar. |
| `app/recall_client.py` | Recall HTTP requests and clear errors. |
| `app/config.py` | Load and validate `.env`. |
| `app/media_server.py` | Authenticated media connections and session cleanup. |
| `app/browser_transport.py` | Carry browser audio and avatar output through Pipecat. |
| `app/tool_registration.py` | Describe and register the nine Pipecat tools. |
| `app/request_filter.py` | Recognize agent addresses and the standalone listening-resume command. |

Recall runs a **four-core Google Meet bot** whose Output Media webpage connects to Pipecat over the HTTPS/WebSocket tunnel. OpenAI Realtime generates speech directly and streams it into Simli, which supplies the speaking avatar. Five tools call Recall; four control speech and listening locally.

Input transcription checks for addressed requests before they can trigger or interrupt an answer. Muting silences speech while leaving tools available. Stopping listening ignores requests until a standalone **“Assistant, start listening”** command; skipped requests are not replayed.

## Boundaries and troubleshooting

- If the bot is silent, say “Assistant” clearly and check chat for its mute/listening status.
- If the avatar does not appear, check provider credentials and the tunnel to port **7861**.
- Chat messages are limited to 500 characters.
- Share trusted public webpages. The agent can display them but cannot read or interact with them; some sites may not load.
- Recall recordings follow your workspace's retention settings; the app does not delete them.
