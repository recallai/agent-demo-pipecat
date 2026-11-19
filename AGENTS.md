# Working on this sample

Keep this a small Pipecat and Recall Google Meet demo, matching the final behavior
of the sibling LiveKit sample. The launcher has one meeting-URL input.

- Keep the Simli speaking avatar and HTTPS tunnel.
- Use Recall camera kind pipecat with Pipecat's native SmallWebRTCTransport.
  The tunnel carries signaling; audio/video travel over WebRTC. Keep the native
  RTVI handshake and interruption messages; do not restore a browser media bridge.
- Create Google Meet bots with variant.google_meet set to web_4_core.
- Use Pipecat's native OpenAI Realtime service for audio input and speech output.
  The example uses REALTIME_MODEL=gpt-realtime-2.1 and REALTIME_VOICE=marin, with
  REALTIME_REASONING_EFFORT=low. Keep the model and reasoning effort configurable
  through .env. The OpenAI key needs Model capabilities → Request.
  See https://developers.openai.com/api/docs/guides/realtime-conversations.
- Match the LiveKit sample's common file roles: agent.py composes the session,
  meeting_agent.py adapts framework behavior, and meeting_tools.py owns actions.
  Keep Pipecat-specific transport and tool-registration code separate.
- Let Pipecat initialize context and register handlers attached to FunctionSchema.
  Preserve the admission guard that prevents an automatic startup reply.
- app/meeting_agent.py owns request admission and explicit input item references.
  Input transcription is only for address and listening-resume checks. Keep
  unrelated audio out of response context; do not restore a separate STT/LLM/TTS
  pipeline or automatically respond to every detected speech turn.
- Nine tools: start_screenshare, send_chat_message, send_current_page_link,
  stop_screenshare, leave_call, mute_agent, unmute_agent, stop_listening,
  start_listening.
- Require Assistant or pipecat demo at the start of every request. Exclude other
  meeting conversation from inference and prevent it from interrupting a reply.
- Join silently and post a short command introduction in chat. Give natural,
  useful answers: usually a few sentences, with more detail when needed. Do not
  impose a one-sentence limit.
- Keep the introduction in agent.py with the same format as the LiveKit sample:
  how to address the assistant, then a link to this README's "Try the agent" section.
- Mute controls speech only. Tools keep working. Stop listening separately ignores
  requests except a standalone addressed start-listening command. Preserve mute
  across listening changes. Never replay skipped speech or requests.
- Each tool posts chat feedback; requested messages and links are their own
  confirmation. A feedback failure must not repeat or undo an action.
- Stream Realtime audio as it arrives. Brief tool acknowledgments are allowed;
  never hold an entire answer to guarantee silent tools. Keep native Pipecat tool
  handling and interruption truncation; preserve addressed-request admission.
- Page sharing displays a public HTTPS URL; it does not read or control the page.
  Preserve the last accepted URL if a later request fails. Only explicitly
  requested actions are allowed after the startup introduction.
- Keep responsibilities and names clear. Review files approaching 300 lines.
  Avoid speculative abstractions and extra features.
- Follow https://docs.recall.ai/docs/meeting-agents-pipecat. Pass the short-lived
  session token in config.body; Recall forwards it as requestData to /api/offer.
  Keep the bot ID returned by Create Bot on the server for tool calls.
- Use Recall MCP docs to verify API behavior. Record documentation gaps for new
  integration fields and verify their source implementation before using them.
- Keep credentials in the ignored root .env or process environment. Never print
  secrets or commit them. Keep only the recent launcher utterances in memory
  during the session. Do not persist meeting content or recordings.
- Use the credentials supplied for this sample and the matching Recall region.
  Do not substitute credentials from another project or workspace.
- Do not add evaluator code, test harnesses, verification instrumentation, or
  reports to this repository. Run checks externally and report their limits.
- Keep the launcher local. Require the short-lived session token for public
  POST /api/offer and POST /api/transcript/.
  Each session owns one peer, including during ICE setup. Support native renegotiation.
- Default to STUN for local development; allow TURN through WEBRTC_ICE_SERVERS.
  An HTTPS tunnel alone does not relay WebRTC media through restrictive networks.
- Report local checks separately from observed live meeting behavior.
