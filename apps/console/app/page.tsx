"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type Choice = { choice_id: string; label: string; consequence_hint: string };
type Action = { action_id: string; type: string; payload: Record<string, unknown>; ts: string };
type SessionStart = { session_id: string; branch_id: string; beat_id: string; stream_token: string };

type SessionState = {
  session_id: string;
  branch_id: string;
  beat_id: string;
  beat_index: number;
  mode: string;
  pacing: string;
  video_budget_remaining: number;
  pending_actions: number;
};

type LiveMessage = {
  type: string;
  text?: string;
  final?: boolean;
  data?: string;
  mime_type?: string;
  event?: Record<string, unknown>;
};

type InterleavedBlock = {
  part_order: number;
  kind: "text" | "image";
  text?: string;
  mime_type?: string;
  uri?: string;
  inline_data_b64?: string;
};

type InterleavedRun = {
  run_id: string;
  beat_id: string;
  trigger: string;
  model_id: string;
  request_id: string;
  blocks: InterleavedBlock[];
  final: boolean;
};
type TranscriptState = { committed: string[]; pending: string };

const API_BASE = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8080";

function parseSampleRate(mimeType: string): number {
  const match = /rate=(\d+)/i.exec(mimeType);
  if (!match) return 24000;
  const value = Number.parseInt(match[1], 10);
  if (!Number.isFinite(value) || value <= 0) return 24000;
  return value;
}

function decodeBase64(data: string): Uint8Array {
  const binary = atob(data);
  const output = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    output[index] = binary.charCodeAt(index);
  }
  return output;
}

function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index]));
    out[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

function downsampleTo16k(buffer: Float32Array, inputSampleRate: number): Int16Array {
  if (inputSampleRate === 16000) {
    return floatToInt16(buffer);
  }

  const sampleRateRatio = inputSampleRate / 16000;
  const newLength = Math.round(buffer.length / sampleRateRatio);
  const result = new Int16Array(newLength);

  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    let accum = 0;
    let count = 0;

    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
      accum += buffer[i];
      count += 1;
    }

    const sample = count > 0 ? accum / count : 0;
    const clamped = Math.max(-1, Math.min(1, sample));
    result[offsetResult] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    offsetResult += 1;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

function toBrowserUri(uri?: string): string | null {
  if (!uri) return null;
  if (uri.startsWith("gs://")) {
    return `https://storage.googleapis.com/${uri.slice(5)}`;
  }
  return uri;
}
function applyTranscriptDelta(state: TranscriptState, text: string, isFinal: boolean, maxLines = 40): TranscriptState {
  const normalized = text.trim();
  if (!normalized) return state;

  if (!isFinal) {
    if (state.pending === normalized) return state;
    return { committed: state.committed, pending: normalized };
  }

  const committed = state.committed[state.committed.length - 1] === normalized
    ? state.committed
    : [...state.committed, normalized].slice(-maxLines);
  return { committed, pending: "" };
}
function transcriptLines(state: TranscriptState): string[] {
  return state.pending ? [...state.committed, state.pending] : state.committed;
}
function debugLog(event: string, details?: Record<string, unknown>) {
  if (process.env.NODE_ENV === "production") return;
  if (details) {
    console.info(`[whatify-console] ${event}`, details);
    return;
  }
  console.info(`[whatify-console] ${event}`);
}

export default function ConsolePage() {
  const [session, setSession] = useState<SessionStart | null>(null);
  const [state, setState] = useState<SessionState | null>(null);
  const [actions, setActions] = useState<Action[]>([]);
  const [captions, setCaptions] = useState<string[]>([]);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [sceneText, setSceneText] = useState<string>("Start a session to generate scene 1.");
  const [assets, setAssets] = useState<Record<string, string>>({});
  const [interleavedRuns, setInterleavedRuns] = useState<Record<string, InterleavedRun>>({});
  const [interleavedOrder, setInterleavedOrder] = useState<string[]>([]);
  const [status, setStatus] = useState<string>("idle");

  const [liveConnected, setLiveConnected] = useState(false);
  const [liveStatus, setLiveStatus] = useState("disconnected");
  const [liveInput, setLiveInput] = useState("");
  const [liveOutputState, setLiveOutputState] = useState<TranscriptState>({ committed: [], pending: "" });
  const [liveInputState, setLiveInputState] = useState<TranscriptState>({ committed: [], pending: "" });
  const [liveMicOn, setLiveMicOn] = useState(false);

  const actionsSocket = useRef<WebSocket | null>(null);
  const captionsSocket = useRef<WebSocket | null>(null);
  const liveSocket = useRef<WebSocket | null>(null);

  const micStreamRef = useRef<MediaStream | null>(null);
  const micAudioContextRef = useRef<AudioContext | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcessorRef = useRef<ScriptProcessorNode | null>(null);

  const playbackContextRef = useRef<AudioContext | null>(null);
  const playbackCursorRef = useRef(0);

  const wsBase = useMemo(() => API_BASE.replace(/^http/i, "ws"), []);
  const latestInterleavedRun = useMemo(() => {
    const runId = interleavedOrder[interleavedOrder.length - 1];
    if (!runId) return null;
    return interleavedRuns[runId] ?? null;
  }, [interleavedOrder, interleavedRuns]);
  const liveOutput = useMemo(() => transcriptLines(liveOutputState), [liveOutputState]);
  const liveInputTranscript = useMemo(() => transcriptLines(liveInputState), [liveInputState]);

  const fetchState = async (sessionId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/session/${sessionId}/state`);
    if (!res.ok) return;
    setState((await res.json()) as SessionState);
  };

  const ensurePlaybackContext = async (sampleRate: number): Promise<AudioContext> => {
    const existing = playbackContextRef.current;
    if (existing && existing.sampleRate === sampleRate) {
      if (existing.state === "suspended") {
        await existing.resume();
      }
      return existing;
    }

    if (existing) {
      await existing.close();
    }

    const context = new AudioContext({ sampleRate });
    playbackContextRef.current = context;
    playbackCursorRef.current = context.currentTime;
    return context;
  };

  const playPcmChunk = async (base64Data: string, mimeType: string) => {
    const sampleRate = parseSampleRate(mimeType);
    const bytes = decodeBase64(base64Data);
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    const float32 = new Float32Array(int16.length);

    for (let index = 0; index < int16.length; index += 1) {
      float32[index] = int16[index] / 32768;
    }

    const context = await ensurePlaybackContext(sampleRate);
    const buffer = context.createBuffer(1, float32.length, sampleRate);
    buffer.copyToChannel(float32, 0);

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);

    const startAt = Math.max(context.currentTime, playbackCursorRef.current);
    source.start(startAt);
    playbackCursorRef.current = startAt + buffer.duration;
  };

  const handleLiveMessage = async (data: string) => {
    let parsed: LiveMessage;
    try {
      parsed = JSON.parse(data) as LiveMessage;
    } catch {
      return;
    }

    if (parsed.type === "output_transcript" && parsed.text) {
      setLiveOutputState((prev) => applyTranscriptDelta(prev, parsed.text!, Boolean(parsed.final)));
      debugLog("live:output", { final: Boolean(parsed.final), text: parsed.text.slice(0, 120) });
    }

    if (parsed.type === "input_transcript" && parsed.text) {
      setLiveInputState((prev) => applyTranscriptDelta(prev, parsed.text!, Boolean(parsed.final)));
      debugLog("live:input", { final: Boolean(parsed.final), text: parsed.text.slice(0, 120) });
    }

    if (parsed.type === "audio_chunk" && parsed.data && parsed.mime_type) {
      await playPcmChunk(parsed.data, parsed.mime_type);
    }
  };

  const startSession = async () => {
    setStatus("starting");
    const res = await fetch(`${API_BASE}/api/v1/session/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        divergence_point: "What if the Library of Alexandria never burned?",
        tone: "cinematic",
        pacing: "normal"
      })
    });
    const data = (await res.json()) as SessionStart;
    setSession(data);
    setActions([]);
    setCaptions([]);
    setChoices([]);
    setAssets({});
    setInterleavedRuns({});
    setInterleavedOrder([]);
    setSceneText("Session started.");
    setLiveOutputState({ committed: [], pending: "" });
    setLiveInputState({ committed: [], pending: "" });
    setLiveStatus("disconnected");
    setStatus("live");
    debugLog("session:start", { sessionId: data.session_id });
    await fetchState(data.session_id);
  };

  const sendInterrupt = async (kind: "WHY" | "COMPARE" | "REWIND" | "PAUSE") => {
    if (!session) return;
    await fetch(`${API_BASE}/api/v1/session/${session.session_id}/interrupt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, question: "Why did this consequence happen?" })
    });
    await fetchState(session.session_id);
  };

  const choose = async (choiceId: string) => {
    if (!session) return;
    await fetch(`${API_BASE}/api/v1/session/${session.session_id}/choice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice_id: choiceId })
    });
    setChoices([]);
    await fetchState(session.session_id);
  };

  const connectLive = () => {
    if (!session) return;
    if (liveSocket.current && liveSocket.current.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(`${wsBase}/api/v1/session/${session.session_id}/live?user_id=console-user`);
    setLiveStatus("connecting");

    ws.onopen = () => {
      setLiveConnected(true);
      setLiveStatus("connected");
      debugLog("live:open", { sessionId: session.session_id });
    };

    ws.onmessage = async (event) => {
      await handleLiveMessage(event.data);
    };

    ws.onclose = () => {
      void stopMic();
      setLiveConnected(false);
      setLiveStatus("disconnected");
      setLiveMicOn(false);
      debugLog("live:close", { sessionId: session.session_id });
    };

    ws.onerror = () => {
      setLiveStatus("error");
      debugLog("live:error", { sessionId: session.session_id });
    };

    liveSocket.current = ws;
  };

  const disconnectLive = async () => {
    await stopMic();
    const ws = liveSocket.current;
    if (ws) {
      ws.close();
    }
    liveSocket.current = null;
    setLiveConnected(false);
    setLiveStatus("disconnected");
  };

  const sendLiveText = () => {
    const ws = liveSocket.current;
    const text = liveInput.trim();
    if (!ws || ws.readyState !== WebSocket.OPEN || !text) return;

    ws.send(JSON.stringify({ type: "text", text }));
    debugLog("live:text", { text: text.slice(0, 120) });
    setLiveInput("");
  };

  const startMic = async () => {
    const ws = liveSocket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (liveMicOn) return;

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true
      }
    });

    const audioContext = new AudioContext();
    await audioContext.resume();

    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    const mute = audioContext.createGain();
    mute.gain.value = 0;

    ws.send(JSON.stringify({ type: "activity_start" }));
    debugLog("mic:start");

    processor.onaudioprocess = (audioEvent) => {
      const socket = liveSocket.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
      }
      const channelData = audioEvent.inputBuffer.getChannelData(0);
      const pcm16 = downsampleTo16k(channelData, audioContext.sampleRate);
      socket.send(pcm16.buffer);
    };

    source.connect(processor);
    processor.connect(mute);
    mute.connect(audioContext.destination);

    micStreamRef.current = stream;
    micAudioContextRef.current = audioContext;
    micSourceRef.current = source;
    micProcessorRef.current = processor;

    setLiveMicOn(true);
  };

  const stopMic = async () => {
    if (!liveMicOn) return;

    const ws = liveSocket.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "activity_end" }));
    }

    if (micProcessorRef.current) {
      micProcessorRef.current.disconnect();
      micProcessorRef.current.onaudioprocess = null;
      micProcessorRef.current = null;
    }

    if (micSourceRef.current) {
      micSourceRef.current.disconnect();
      micSourceRef.current = null;
    }

    if (micAudioContextRef.current) {
      await micAudioContextRef.current.close();
      micAudioContextRef.current = null;
    }

    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((track) => track.stop());
      micStreamRef.current = null;
    }

    setLiveMicOn(false);
    debugLog("mic:stop");
  };

  useEffect(() => {
    if (!session) return;

    const actionWs = new WebSocket(`${wsBase}/api/v1/session/${session.session_id}/actions`);
    const captionWs = new WebSocket(`${wsBase}/api/v1/session/${session.session_id}/captions`);

    actionWs.onmessage = async (event) => {
      const payload = JSON.parse(event.data) as Action;
      setActions((prev) => [payload, ...prev].slice(0, 120));
      debugLog("action:recv", { type: payload.type, actionId: payload.action_id });

      if (payload.type === "SET_SCENE") {
        const setup = (payload.payload.setup as string | undefined) ?? "";
        const escalation = (payload.payload.escalation as string | undefined) ?? "";
        setSceneText(`${setup}\n\n${escalation}`);
      }

      if (payload.type === "SHOW_CHOICES") {
        const next = (payload.payload.choices as Choice[] | undefined) ?? [];
        setChoices(next);
      }

      if (payload.type === "SHOW_STORYBOARD") {
        const frames = (payload.payload.frames as Array<{ shot_id: string; uri?: string }>) ?? [];
        setAssets((prev) => {
          const out = { ...prev };
          frames.forEach((frame) => {
            if (frame.uri) {
              out[frame.shot_id] = frame.uri;
            }
          });
          return out;
        });
      }

      if (payload.type === "PLAY_VIDEO") {
        const shotId = (payload.payload.shot_id as string | undefined) ?? "hero";
        const uri = (payload.payload.uri as string | undefined) ?? "";
        if (uri) {
          setAssets((prev) => ({ ...prev, [shotId]: uri }));
        }
      }

      if (payload.type === "SHOW_INTERLEAVED_BLOCKS") {
        const runId = (payload.payload.run_id as string | undefined) ?? "run_unknown";
        const blocks = (payload.payload.blocks as InterleavedBlock[] | undefined) ?? [];
        const trigger = (payload.payload.trigger as string | undefined) ?? "BEAT_START";
        const beatId = (payload.payload.beat_id as string | undefined) ?? "beat_unknown";
        const modelId = (payload.payload.model_id as string | undefined) ?? "unknown";
        const requestId = (payload.payload.request_id as string | undefined) ?? "unknown";
        const final = Boolean(payload.payload.final);

        setInterleavedRuns((prev) => {
          const existing = prev[runId];
          const merged = [...(existing?.blocks ?? []), ...blocks].sort(
            (left, right) => left.part_order - right.part_order
          );
          return {
            ...prev,
            [runId]: {
              run_id: runId,
              beat_id: beatId,
              trigger,
              model_id: modelId,
              request_id: requestId,
              blocks: merged,
              final: final || Boolean(existing?.final)
            }
          };
        });
        setInterleavedOrder((prev) => (prev.includes(runId) ? prev : [...prev, runId]));
      }

      await fetch(`${API_BASE}/api/v1/session/${session.session_id}/ack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: payload.action_id })
      });
      await fetchState(session.session_id);
    };

    captionWs.onmessage = (event) => {
      const payload = JSON.parse(event.data) as { text: string };
      debugLog("caption:recv", { text: payload.text.slice(0, 120) });
      setCaptions((prev) => [...prev, payload.text].slice(-16));
    };

    actionsSocket.current = actionWs;
    captionsSocket.current = captionWs;

    return () => {
      actionWs.close();
      captionWs.close();
    };
  }, [session, wsBase]);

  useEffect(() => {
    return () => {
      if (micProcessorRef.current) {
        micProcessorRef.current.disconnect();
        micProcessorRef.current.onaudioprocess = null;
        micProcessorRef.current = null;
      }
      if (micSourceRef.current) {
        micSourceRef.current.disconnect();
        micSourceRef.current = null;
      }
      if (micAudioContextRef.current) {
        void micAudioContextRef.current.close();
        micAudioContextRef.current = null;
      }
      if (micStreamRef.current) {
        micStreamRef.current.getTracks().forEach((track) => track.stop());
        micStreamRef.current = null;
      }
      liveSocket.current?.close();
      actionsSocket.current?.close();
      captionsSocket.current?.close();
      if (playbackContextRef.current) {
        void playbackContextRef.current.close();
      }
    };
  }, []);

  const latestInterleavedText =
    [...(latestInterleavedRun?.blocks ?? [])].reverse().find((block) => block.kind === "text")?.text ?? null;
  const latestInterleavedImageBlock =
    [...(latestInterleavedRun?.blocks ?? [])].reverse().find((block) => block.kind === "image") ?? null;
  const latestInterleavedImageSrc = latestInterleavedImageBlock
    ? toBrowserUri(latestInterleavedImageBlock.uri) ??
      (latestInterleavedImageBlock.inline_data_b64 && latestInterleavedImageBlock.mime_type
        ? `data:${latestInterleavedImageBlock.mime_type};base64,${latestInterleavedImageBlock.inline_data_b64}`
        : null)
    : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-[1400px] flex-col gap-4 px-6 py-6">
      <header className="panel rounded-2xl p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-semibold tracking-tight">WhatIf Control Console</h1>
          <button
            className="rounded-full bg-ember px-5 py-2 font-semibold text-ink transition hover:brightness-110"
            onClick={startSession}
          >
            {session ? "Restart Session" : "Start Session"}
          </button>
        </div>
        <p className="mt-2 text-sm text-slate-300">Status: {status}</p>
      </header>

      <section className="grid grid-cols-12 gap-4">
        <article className="panel col-span-8 rounded-2xl p-4">
          <h2 className="mb-2 text-xl">Scene View</h2>
          <p className="whitespace-pre-wrap text-slate-200">{sceneText}</p>
          <div className="mt-4 rounded-xl border border-slate-700/60 bg-black/35 p-3">
            <p className="text-xs uppercase tracking-wide text-mint">Interleaved Mixed Output</p>
            <p className="mt-2 text-xs text-slate-300">
              {latestInterleavedRun
                ? `${latestInterleavedRun.trigger} | ${latestInterleavedRun.model_id} | req ${latestInterleavedRun.request_id}`
                : "No interleaved run yet"}
            </p>
            <p className="mt-2 text-sm text-slate-100">
              {latestInterleavedText ?? "Waiting for interleaved text block..."}
            </p>
            {latestInterleavedImageSrc ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={latestInterleavedImageSrc}
                alt="Interleaved frame"
                className="mt-3 h-[220px] w-full rounded-lg border border-slate-600/60 object-cover"
              />
            ) : (
              <div className="mt-3 grid h-[220px] place-items-center rounded-lg border border-slate-600/60 bg-black/40 text-xs text-slate-400">
                Waiting for interleaved image block...
              </div>
            )}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3">
            {Object.entries(assets).map(([shotId, uri]) => (
              <div key={shotId} className="rounded-xl border border-slate-700/60 bg-black/30 p-2 text-xs">
                <p className="mb-1 font-medium text-mint">{shotId}</p>
                <p className="break-all text-slate-300">{uri}</p>
              </div>
            ))}
          </div>
        </article>

        <aside className="panel col-span-4 rounded-2xl p-4">
          <h2 className="mb-2 text-xl">Session</h2>
          {state ? (
            <dl className="space-y-1 text-sm text-slate-200">
              <div>Mode: {state.mode}</div>
              <div>Beat: {state.beat_index}</div>
              <div>Video budget: {state.video_budget_remaining}</div>
              <div>Pending ACKs: {state.pending_actions}</div>
            </dl>
          ) : (
            <p className="text-sm text-slate-400">No active session.</p>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {(["WHY", "COMPARE", "REWIND", "PAUSE"] as const).map((kind) => (
              <button
                key={kind}
                className="rounded-lg border border-slate-600 px-3 py-1 text-sm hover:border-ember"
                onClick={() => sendInterrupt(kind)}
                disabled={!session}
              >
                {kind}
              </button>
            ))}
          </div>
        </aside>
      </section>

      <section className="panel rounded-2xl p-4">
        <h2 className="mb-3 text-xl">Gemini Live Voice</h2>
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="rounded-lg border border-slate-600 px-3 py-1 text-sm hover:border-ember disabled:opacity-50"
            onClick={connectLive}
            disabled={!session || liveConnected}
          >
            Connect Live
          </button>
          <button
            className="rounded-lg border border-slate-600 px-3 py-1 text-sm hover:border-ember disabled:opacity-50"
            onClick={() => void disconnectLive()}
            disabled={!liveConnected}
          >
            Disconnect Live
          </button>
          <button
            className="rounded-lg border border-slate-600 px-3 py-1 text-sm hover:border-ember disabled:opacity-50"
            onClick={() => void startMic()}
            disabled={!liveConnected || liveMicOn}
          >
            Start Mic
          </button>
          <button
            className="rounded-lg border border-slate-600 px-3 py-1 text-sm hover:border-ember disabled:opacity-50"
            onClick={() => void stopMic()}
            disabled={!liveMicOn}
          >
            Stop Mic
          </button>
          <p className="text-sm text-slate-300">Live: {liveStatus}</p>
        </div>

        <div className="mt-3 flex gap-2">
          <input
            className="w-full rounded-lg border border-slate-600 bg-black/20 px-3 py-2 text-sm"
            placeholder="Send text to live agent"
            value={liveInput}
            onChange={(event) => setLiveInput(event.target.value)}
          />
          <button
            className="rounded-lg border border-slate-600 px-3 py-2 text-sm hover:border-ember disabled:opacity-50"
            onClick={sendLiveText}
            disabled={!liveConnected || liveInput.trim().length === 0}
          >
            Send
          </button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div className="rounded-lg border border-slate-700/60 bg-black/20 p-3">
            <h3 className="mb-2 text-sm font-semibold text-mint">Model Transcript</h3>
            <div className="h-[160px] overflow-y-auto text-sm text-slate-200">
              {liveOutput.map((line, index) => (
                <p key={`${index}-${line.slice(0, 12)}`} className="mb-1">
                  {line}
                </p>
              ))}
            </div>
          </div>
          <div className="rounded-lg border border-slate-700/60 bg-black/20 p-3">
            <h3 className="mb-2 text-sm font-semibold text-mint">Input Transcript</h3>
            <div className="h-[160px] overflow-y-auto text-sm text-slate-200">
              {liveInputTranscript.map((line, index) => (
                <p key={`${index}-${line.slice(0, 12)}`} className="mb-1">
                  {line}
                </p>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="grid grid-cols-12 gap-4">
        <article className="panel col-span-6 rounded-2xl p-4">
          <h2 className="mb-2 text-xl">Choices</h2>
          <div className="space-y-2">
            {choices.map((choice) => (
              <button
                key={choice.choice_id}
                className="block w-full rounded-lg border border-slate-600 bg-slate-900/40 p-3 text-left text-sm hover:border-ember"
                onClick={() => choose(choice.choice_id)}
              >
                <p className="font-medium">{choice.label}</p>
                <p className="mt-1 text-slate-400">{choice.consequence_hint}</p>
              </button>
            ))}
            {choices.length === 0 ? <p className="text-sm text-slate-400">No choices currently exposed.</p> : null}
          </div>
        </article>

        <article className="panel col-span-6 rounded-2xl p-4">
          <h2 className="mb-2 text-xl">Captions</h2>
          <div className="h-[220px] overflow-y-auto rounded-lg border border-slate-700/60 bg-black/20 p-3 text-sm text-slate-200">
            {captions.map((line, index) => (
              <p key={`${index}-${line.slice(0, 12)}`} className="mb-2">
                {line}
              </p>
            ))}
          </div>
        </article>
      </section>

      <section className="panel rounded-2xl p-4">
        <h2 className="mb-2 text-xl">Action Stream</h2>
        <div className="h-[220px] overflow-y-auto rounded-lg border border-slate-700/60 bg-black/20 p-3 text-xs text-slate-200">
          {actions.map((action) => (
            <pre key={action.action_id} className="mb-2 whitespace-pre-wrap break-all">
              {JSON.stringify(action, null, 2)}
            </pre>
          ))}
        </div>
      </section>
    </main>
  );
}
