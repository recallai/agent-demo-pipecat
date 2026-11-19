# Pipecat meeting agent

A small agent demo using [Recall.ai's native Pipecat integration](https://docs.recall.ai/docs/meeting-agents-pipecat) and the [full Deepgram Voice Agent API](https://developers.deepgram.com/reference/voice-agent/voice-agent). It includes a lip-synced Simli avatar and nine meeting tools.

```text
Recall meeting audio → Flux address check → Deepgram Voice Agent → Simli → Recall
```

Flux checks for “Assistant” before the app sends any request audio to Voice Agent. Voice Agent transcribes that approved audio and handles reasoning, tools, and speech. The extra transcription pass lets the app keep background speech from triggering or interrupting replies while still using Voice Agent's audio input.

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
- A [Deepgram project API key](https://developers.deepgram.com/docs/create-additional-api-keys) with access to streaming Flux STT and Voice Agent.
- A Simli API key and a face available to that account.
- An HTTPS tunnel, such as cloudflared, and a network that permits WebRTC connections.
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
| `DEEPGRAM_API_KEY` | Project key for Flux STT and Voice Agent. |
| `DEEPGRAM_AGENT_MODEL`, `DEEPGRAM_AGENT_VOICE` | Hosted model and voice for Voice Agent. The example uses `gpt-4o-mini` and `aura-2-thalia-en`. |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | Simli account key and avatar face ID. |
| `PIPECAT_PUBLIC_URL` | The tunnel's HTTPS origin, without a path or trailing slash. |
| `SESSION_TIMEOUT_SECONDS` | Overall session limit; defaults to `600` seconds. |
| `WEBRTC_ICE_SERVERS` | Optional STUN/TURN configuration; see [.env.example](.env.example). |

Restart the app and launch a new bot after changing `.env`. This sample authenticates offers with an expiring session token, so it needs no saved Pipecat credentials in Recall. Deployments using bearer authentication must register their token and exact offer URL in **Agents → Pipecat**, as described in the [integration guide](https://docs.recall.ai/docs/meeting-agents-pipecat#2-add-your-credentials-to-recallai).

### 3. Start an HTTPS tunnel

With [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installed:

```sh
cloudflared tunnel --url http://127.0.0.1:7861
```

Copy the tunnel's HTTPS origin into `PIPECAT_PUBLIC_URL` in `.env`. Keep this terminal running.

Only tunnel the signaling server on port **7861**. Keep the launcher on port **8001** local. Audio and video travel over WebRTC, separately from the tunnel.

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
| [app/agent.py](app/agent.py) | Settings and Pipecat pipeline. |
| [app/addressed_audio.py](app/addressed_audio.py) | Match approved requests to buffered meeting audio. |
| [app/meeting_agent.py](app/meeting_agent.py), [app/voice_agent_connection.py](app/voice_agent_connection.py) | Stream each Voice Agent turn and carry conversation context forward. |
| [app/tool_registration.py](app/tool_registration.py), [app/meeting_tools.py](app/meeting_tools.py) | Define and run the nine meeting actions. |
| [app/recall_client.py](app/recall_client.py) | Create the bot and call Recall's meeting APIs. |

Recall runs a **four-core Google Meet bot** with one `output_media.camera.kind = "pipecat"` source for meeting audio and avatar video. The launcher passes an expiring session token in `config.body`; Recall forwards it as `requestData` to `/api/offer`. Audio and video connect directly over WebRTC.

Each admitted request opens a new Voice Agent connection so interrupted audio and tool calls stay with that request. The app carries a bounded conversation history forward. Simli returns synchronized speech and video. The app checks mute both before sending speech to Simli and after Simli returns buffered audio.

Client-side tools run on our server using the bot ID returned by Create Bot. Five call Recall and four control speech and listening locally. Muting silences speech while leaving tools available. Stopping listening ignores requests until a standalone **“Assistant, start listening”** command. As the agent hears mixed meeting audio, overlapping voices can be present inside an admitted request.

## Boundaries and troubleshooting

- If the bot is silent, say “Assistant” clearly and check chat for its mute/listening status.
- If the avatar does not appear, check provider credentials and the tunnel to port **7861**.
- If signaling succeeds but media cannot connect, configure a TURN relay in `WEBRTC_ICE_SERVERS`. The default STUN server cannot traverse every firewall; use TURN for reliable production connectivity.
- Chat messages are limited to 500 characters.
- Share trusted public webpages. The agent can display them but cannot read or interact with them; some sites may not load.
- Recall recordings follow your workspace's retention settings; the app does not delete them.
