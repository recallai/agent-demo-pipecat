# Pipecat meeting agent

A small agent demo featuring [Recall.ai's Output Media API](https://docs.recall.ai/docs/stream-media) and [Pipecat](https://docs.pipecat.ai/). It includes an interactive Simli avatar, native [OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations) speech-to-speech, and nine tool calls.

## Behavior

Address the avatar as **“Assistant”** or **“pipecat demo”**, followed by a question or a command such as “show https://example.com.” The agent joins silently and posts an introduction in meeting chat. Answers stream as OpenAI generates them. Tool calls can include a brief spoken acknowledgment and also post their result in chat; requested messages and links are their own chat confirmation.

| Action | Tool |
| --- | --- |
| Start or replace a screen share with a public webpage; post a loading notice | `start_screenshare(url)` |
| Post text in meeting chat | `send_chat_message(message)` |
| Post the exact last accepted page URL | `send_current_page_link()` |
| Stop the screen share | `stop_screenshare()` |
| Post a departure message and leave the meeting | `leave_call()` |
| Silence speech; keep tools available | `mute_agent()` |
| Enable new speech without replaying earlier responses | `unmute_agent()` |
| Stop listening for requests | `stop_listening()` |
| Start listening for requests; preserve the mute setting | `start_listening()` |

Start each request with the agent's name. After stopping listening, say **“Assistant, start listening”** as a standalone command.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Recall workspace and its regional REST API key.
- An OpenAI project API key with **Model capabilities → Request** permission and access to your selected Realtime model (the example uses `gpt-realtime-2.1`) and `gpt-4o-mini-transcribe`. See [OpenAI permissions](https://developers.openai.com/api/docs/guides/rbac#permissions).
- A Simli API key and a face available to that account.
- A public HTTPS tunnel to the local media server, with WebSocket support.
- A Google account to host and admit the agent into Google Meet.

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
| `OPENAI_API_KEY` | Project key with Model capabilities → Request access. |
| `REALTIME_MODEL` | Native audio conversation model; the example uses `gpt-realtime-2.1`. |
| `REALTIME_REASONING_EFFORT` | `low` by default. Use `minimal`, `low`, `medium`, `high`, or `xhigh` on a model that supports reasoning; `default` leaves the choice to the provider. |
| `REALTIME_VOICE` | Realtime voice; the example uses `marin`. |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | Simli account key and avatar face ID. |
| `PIPECAT_PUBLIC_URL` | The tunnel's HTTPS origin, without a path or trailing slash. |
| `SESSION_TIMEOUT_SECONDS` | Overall session limit; defaults to `600` seconds. |

OpenAI supplies the voice and Simli animates the avatar. Both keys stay on the Python server; this sample uses Recall's `webpage` Output Media integration and requires no provider credentials in the Recall dashboard.

Tune the demo in `.env`, without changing Python code:

```dotenv
REALTIME_MODEL=gpt-realtime-2.1
REALTIME_REASONING_EFFORT=low
REALTIME_VOICE=marin
```

Choose an OpenAI Realtime model with native audio output and function calling. A text-only model cannot replace it. Lower reasoning effort favors responsiveness; higher effort allows more deliberation and may increase latency. Supported effort levels depend on the model. Restart the app and launch a new bot after changing these settings; an existing session keeps its original model.

### 3. Start an HTTPS tunnel

Forward a public HTTPS tunnel to **`http://127.0.0.1:7861`**. For example, with [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) installed:

```sh
cloudflared tunnel --url http://127.0.0.1:7861
```

Copy the tunnel's HTTPS origin into `PIPECAT_PUBLIC_URL` in `.env`. Keep this terminal running.

Only tunnel the media server. The launcher on port **8001** must remain local. Each meeting receives a separate, expiring media URL; do not publish those URLs or enable access logging for them.

### 4. Start the app

Open another terminal in the repository root:

```sh
uv run --locked python -m app.web
```

This starts the local launcher on port **8001** and the media server on port **7861** in one process. Keep it running alongside the tunnel, then open [the launcher](http://127.0.0.1:8001).

### 5. Send the agent to a meeting

Create and join a meeting at [meet.new](https://meet.new/). Paste its URL into the launcher and select **Send agent**, then admit **pipecat demo** once it enters the waiting room.

### 6. Try the agent

Try these phrases. Each tool call also posts its result in meeting chat.

| Try saying |
| --- |
| “Assistant, what is two plus two?” |
| “Assistant, show recall.ai/agent homepage.” |
| “Assistant, send the shared webpage link in chat.” |
| “Assistant, mute.” |
| “Assistant, post Hello from the demo in chat.” |
| “Assistant, stop sharing.” |
| “Assistant, unmute.” |
| “Assistant, stop listening.” |
| “Assistant, start listening.” |
| “Assistant, leave the meeting.” |

Verify the share and chat messages in the meeting. Confirm that tools work while muted, that stopped listening ignores requests, and that the bot leaves without ending anyone else's call.

After the bot leaves, stop both terminal processes with Ctrl+C.

The avatar and meeting session have a duration limit; launch another bot to repeat the demo.

## Code map

| File | Responsibility |
| --- | --- |
| `app/web.py` | Serve the form, create a Recall bot, and run both HTTP servers. |
| `app/templates/index.html` | Single form and request status. |
| `app/agent.py` | Instructions, Realtime configuration, avatar startup, and cleanup. |
| `app/meeting_agent.py` | Admit addressed requests and select their native audio context; stream current Realtime replies and tool calls. |
| `app/meeting_tools.py` | Nine tools, listening/mute state, the current page URL, and chat feedback. |
| `app/speech_output.py` | Suppress muted and interrupted audio before and after the avatar. |
| `app/recall_client.py` | Recall HTTP requests, regional URL validation, and clear errors. |
| `app/config.py` | Load and validate `.env`, including model and reasoning settings. |
| `app/media_server.py` | Expiring meeting URLs, authenticated media connections, and session cleanup. |
| `app/browser_transport.py` | Adapt browser PCM audio and JPEG video to Pipecat frames. |
| `app/tool_registration.py` | Describe and register the nine Pipecat tools. |
| `app/request_filter.py` | Recognize agent addresses and the standalone listening-resume command. |
| `app/templates/renderer.html` | Play the avatar and stream meeting audio through the private WebSocket. |
| `app/templates/microphone.js` | Convert meeting microphone samples to PCM audio packets. |

The common files follow the [LiveKit sample](https://github.com/recallai/agent-demo-livekit#code-map): session setup in `agent.py`, framework behavior in `meeting_agent.py`, meeting actions in `meeting_tools.py`, and Recall requests in `recall_client.py`. Pipecat also needs the browser media connection and explicit tool registration shown above.

Recall runs an Output Media webpage in a **four-core Google Meet bot** (`variant.google_meet = web_4_core`). The page sends meeting audio to Pipecat over the HTTPS tunnel and plays the returned voice and avatar. Five tools call Recall; four control speech and request processing locally.

Pipecat sends the original audio to OpenAI Realtime, which generates speech directly. Simli animates that speech. There is no separate STT → LLM → TTS pipeline. Input transcription checks only whether a request is addressed to the agent or is the standalone start-listening command; unrelated speech is excluded from response context and cannot interrupt an answer.

The native Realtime service streams speech directly into Simli; the app does not buffer a whole response before playback. Pipecat's function-calling interface executes the tools and returns results to the model for a brief acknowledgment or another explicitly requested action. An in-progress Recall action survives a speech interruption and is not retried automatically.

OpenAI's semantic turn detector identifies utterance boundaries. The app waits for the completed input transcript to recognize an addressed request before answering or interrupting; ordinary meeting discussion does neither. This meeting-specific check adds latency compared with an always-addressed, one-to-one voice assistant. Muting drops speech while leaving tools available. Stopping listening discards requests except for an addressed standalone “start listening” command; skipped requests and unheard answers cannot replay when listening or speech resumes.

The browser media connection uses an authenticated WebSocket because the local HTTPS tunnel carries that traffic end to end. Pipecat [recommends WebRTC for browser voice applications](https://docs.pipecat.ai/client/concepts/choosing-a-transport); switching this sample to SmallWebRTC would also require reachable media ports or a TURN relay. The HTTPS tunnel alone only supplies WebRTC signaling.

## Boundaries and troubleshooting

- Use meeting chat to see command results and whether speech or listening is disabled. Clearly say “Assistant” before a request.
- Chat messages are limited to 500 characters. Showing a webpage does not automatically post its URL.
- The agent hears meeting audio. It cannot see participants, read shared pages, click, scroll, sign in, or summarize webpage contents.
- A screen-share request can be accepted before the page appears. Some public websites block automated browsers; check the meeting before retrying an uncertain result.
- Only share trusted public webpages. URL validation does not enforce where a hostname resolves or where a webpage redirects; network restrictions belong at the renderer.
- If the agent is silent or has no avatar, check provider permissions, model access, Simli face access, and the HTTPS tunnel's WebSocket connection to port **7861**.
- If a session fails, verify that the bot has departed. Failed actions and uncertain creation requests are not retried automatically.
- The app writes no meeting audio or transcript files locally. It does not disable Recall recording or delete provider recordings when the bot leaves; configure provider retention for your needs.
