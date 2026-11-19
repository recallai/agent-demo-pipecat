# agent-demo-pipecat

A small agent demo featuring [Recall.ai's native Pipecat integration](https://docs.recall.ai/docs/meeting-agents-pipecat) and an interactive Simli avatar you can command through nine tool calls.

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
- Deepgram configured in the same Recall workspace for the launcher's live transcript. [Deepgram setup](https://docs.recall.ai/docs/deepgram#setup)
- An OpenAI project API key with **Model capabilities → Request** access to Realtime and `gpt-4o-mini-transcribe`.
- A Simli API key and a face available to that account.
- An HTTPS tunnel, such as cloudflared, and a network that permits WebRTC connections.
- A Google account to host the meeting.

## Quickstart

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

Fill in the root `.env`. Existing environment variables take precedence.

| Variable | Value |
| --- | --- |
| `RECALL_API_KEY` | REST API key for the Recall workspace. |
| `RECALL_API_BASE_URL` | Matching regional API URL, such as `https://us-west-2.recall.ai/api/v1`. |
| `OPENAI_API_KEY` | OpenAI project key. |
| `REALTIME_MODEL`, `REALTIME_VOICE`, `REALTIME_REASONING_EFFORT` | OpenAI Realtime settings from [.env.example](.env.example). |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | Simli account key and avatar face ID. |
| `PIPECAT_PUBLIC_URL` | The tunnel's HTTPS origin, without a path or trailing slash. |

### 3. Start an HTTPS tunnel

With [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installed:

```sh
cloudflared tunnel --url http://127.0.0.1:7861
```

Copy the tunnel's HTTPS origin into `PIPECAT_PUBLIC_URL` in `.env` and keep the tunnel running. The launcher stays local on port **8001**.

### 4. Start the launcher

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

The session defaults to 10 minutes. Set `SESSION_TIMEOUT_SECONDS` to change it.

## Code map

| File | Responsibility |
| --- | --- |
| `app/web.py` | Serve the form, create a Recall bot, and run both HTTP servers. |
| `app/templates/index.html` | Local launcher page. |
| `app/agent.py` | Instructions, Realtime configuration, avatar startup, and cleanup. |
| `app/meeting_agent.py` | Admit addressed requests and stream Realtime replies. |
| `app/meeting_tools.py` | Nine tools, listening/mute state, and the current page URL. |
| `app/speech_output.py` | Suppress muted and interrupted audio before and after the avatar. |
| `app/recall_client.py` | Recall HTTP requests and clear errors. |
| `app/config.py` | Load and validate `.env`. |
| `app/media_server.py` | Handle Recall callbacks and close meeting sessions. |

Recall runs a four-core Google Meet bot with `output_media.camera.kind = "pipecat"`. The tunnel carries signaling, and WebRTC carries audio and video.

OpenAI Realtime streams speech into Simli for synchronized audio and avatar video. Five tools call Recall using the bot ID returned by Create Bot. Four control speech and listening locally.

## Boundaries and troubleshooting

- If the bot is silent, say “Assistant” clearly and check chat for its mute or listening status.
- If the avatar does not appear, check provider credentials and the tunnel to port **7861**.
- If signaling succeeds but media cannot connect, configure a TURN relay in `WEBRTC_ICE_SERVERS`. [.env.example](.env.example)
- Chat messages are limited to 500 characters.
- Share trusted public webpages. The agent can display them but cannot read or interact with them; some sites may not load.
- Recall recordings follow your workspace's retention settings; the app does not delete them.
