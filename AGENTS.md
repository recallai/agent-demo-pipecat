# Working on this sample

Keep this a small, runnable meeting agent demo for Zoom, Google Meet, Microsoft Teams, and Webex. The launcher has one meeting-URL input and a submit button.

- The nine conversational tools are `start_screenshare`, `send_chat_message`, `send_current_page_link`, `stop_screenshare`, `leave_call`, `mute_agent`, `unmute_agent`, `stop_listening`, and `start_listening`.
- Require requests to start with `Assistant` or `pipecat demo`. Use finalized OpenAI Realtime input transcription and explicit input item references to keep unrelated audio out of the conversation and prevent it from interrupting replies.
- Join silently. Post one short introduction in chat with the address format and a link to the README's “Try the agent” section. Answer naturally with enough detail to be useful.
- Mute controls speech only. Tools remain available while listening. Stop listening pauses answers and actions until a standalone addressed `start listening` request. Listening changes preserve mute. Discard skipped requests and unheard speech.
- Run tools only on explicit requests. Post results and any errors in meeting chat when delivery is possible. Requested messages and links are their own confirmation. Apply controls before posting feedback, and keep spoken acknowledgments brief. Failed feedback must never undo or retry an action.
- Share public HTTPS pages and post a loading notice. Send the URL only when requested. Preserve the last accepted URL when a later share fails. Stop sharing keeps voice available. Leave removes only the bot.
- Use Pipecat's native OpenAI Realtime audio and `FunctionSchema` tool handlers. Keep model, voice, and reasoning effort configurable in `.env`. Use Simli for the speaking avatar.
- Set the Zoom, Google Meet, Microsoft Teams, and Webex entries in `variant` to `web_4_core` and set `output_media.camera.kind` to `pipecat`. Use SmallWebRTC for media and RTVI for the ready handshake and interruptions. Use the bot ID returned by Create Bot for tool requests.
- Keep the launcher on localhost. Require a short-lived meeting token on both tunnel routes. Recall forwards `config.body` as `requestData` on the WebRTC offer. Keep one peer per meeting through ICE setup and renegotiation. Default to STUN and allow TURN through `WEBRTC_ICE_SERVERS`.
- Configure Recall's Deepgram Nova-3 transcription with Perfect Diarization. Accept finalized `transcript.data` for the session's bot and keep only recent utterances in memory for the launcher. Do not persist meeting content or recordings.
- Keep session wiring in `app/agent.py`, voice admission in `app/meeting_agent.py`, and meeting actions in `app/meeting_tools.py`. Keep tool-specific guidance with its tool. Review responsibilities when a file approaches 300 lines.
- Use Recall MCP docs to verify API behavior. Check the source for new integration fields where the docs leave a gap.
- Use the sample's root `.env` or process environment for credentials. Match the keys to the workspace and region. Do not print or commit secrets.
- Run checks outside the repo. Keep evaluator code, test harnesses, and reports out of the sample. Distinguish local checks from observed meeting behavior.
