"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Action = { action_id: string; type: string; payload: Record<string, unknown>; ts: string; retry_count: number };
type SessionStart = { session_id: string; branch_id: string; beat_id: string; stream_token: string };
type SessionState = {
  session_id: string;
  branch_id: string;
  beat_id: string;
  beat_index: number;
  mode: "ONBOARDING" | "STORY" | "CHOICE" | "EXPLAIN" | "INTERMISSION" | "COMPLETE" | "NAV";
  pacing: string;
  video_budget_remaining: number;
  pending_actions: number;
  target_beats: number;
  phase: string;
  awaiting_continue: boolean;
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
  final: boolean;
  blocks: InterleavedBlock[];
};
type VisualAssetFrame = {
  asset_id: string;
  shot_id: string;
  uri?: string | null;
  ready: boolean;
};
type VisualStateResponse = {
  session_id: string;
  beat_id: string;
  storyboard_frames: VisualAssetFrame[];
  hero_video_uri?: string | null;
  interleaved_run?: InterleavedRun | null;
};
type LiveMessage = {
  type: string;
  text?: string;
  final?: boolean;
  data?: string;
  mime_type?: string;
  event?: Record<string, unknown>;
  status?: string;
  message?: string;
  attempt?: number;
  retry_in_ms?: number;
};
type BrowserSpeechRecognitionAlternative = { transcript: string };
type BrowserSpeechRecognitionResult = { isFinal: boolean; 0: BrowserSpeechRecognitionAlternative };
type BrowserSpeechRecognitionResultList = { length: number; [index: number]: BrowserSpeechRecognitionResult };
type BrowserSpeechRecognitionEvent = Event & { resultIndex: number; results: BrowserSpeechRecognitionResultList };
type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onstart: ((event: Event) => void) | null;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: ((event: Event) => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type BrowserSpeechRecognitionCtor = new () => BrowserSpeechRecognition;
type UiPhase = "intro" | "onboarding" | "processing" | "actReveal" | "acting" | "actSummary" | "complete";
type TranscriptState = { committed: string[]; pending: string };
type ActRevealState = { beatId: string; beatIndex: number; targetBeats: number; actTitle: string; actTimeLabel: string };
type SceneState = {
  beatId: string;
  title: string;
  setup: string;
  escalation: string;
  actTimeLabel: string;
  narrationScript: string;
};
type SceneMoment = {
  id: string;
  caption: string;
  body: string;
  imageSrc: string | null;
  backdrop: string;
};
type QuestionEntry = { question: string; answer: string; pending: boolean };
type VoiceCommand = { kind: "continue" } | { kind: "replay"; momentIndex: number } | null;
type ActTheme = {
  accent: string;
  rgb: string;
  ambient: string;
  videoGrad: string;
};

const API_BASE = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8080";
const WS_BASE = API_BASE.replace(/^http/i, "ws");
const MAX_SCENE_MOMENTS = 5;
const NARRATION_RESPONSE_TIMEOUT_MS = 6500;
const NARRATION_AUDIO_GRACE_TIMEOUT_MS = 12000;
const MAX_NARRATION_RECOVERY_ATTEMPTS = 2;
const DEFAULT_SUMMARY_PROMPT = "Continue when you are ready for the next act.";
const FINAL_SUMMARY_PROMPT = "The final act is complete.";
const WAVE_HEIGHTS = [5, 12, 22, 8, 18, 28, 6, 20, 14, 26, 10, 24, 4, 16, 30, 8, 22, 12, 18, 6];
const WAVE_DELAYS = [0, 0.15, 0.3, 0.05, 0.22, 0.38, 0.1, 0.28, 0.18, 0.4, 0.08, 0.33, 0.02, 0.2, 0.45, 0.12, 0.35, 0.15, 0.25, 0.07];
const ACT_THEMES: ActTheme[] = [
  {
    accent: "#c4781a",
    rgb: "196,120,26",
    ambient: "linear-gradient(180deg,#090402 0%,#1c0d05 55%,#0c0503 100%)",
    videoGrad: "linear-gradient(135deg,#1a0c04 0%,#2e1508 42%,#160a03 100%)",
  },
  {
    accent: "#3a78c4",
    rgb: "58,120,196",
    ambient: "linear-gradient(190deg,#040910 0%,#0a1224 55%,#060a16 100%)",
    videoGrad: "linear-gradient(135deg,#04080f 0%,#0c1830 42%,#060a18 100%)",
  },
  {
    accent: "#2a9450",
    rgb: "42,148,80",
    ambient: "linear-gradient(185deg,#050a06 0%,#0a1c0d 52%,#060f07 100%)",
    videoGrad: "linear-gradient(135deg,#040905 0%,#0a1c0c 42%,#060b07 100%)",
  },
  {
    accent: "#8552c8",
    rgb: "133,82,200",
    ambient: "linear-gradient(195deg,#070510 0%,#0f0a1e 52%,#080610 100%)",
    videoGrad: "linear-gradient(135deg,#060410 0%,#10081e 42%,#07050f 100%)",
  },
  {
    accent: "#c43c36",
    rgb: "196,60,54",
    ambient: "linear-gradient(180deg,#090303 0%,#170808 52%,#0b0404 100%)",
    videoGrad: "linear-gradient(135deg,#0a0303 0%,#1a0707 42%,#0c0404 100%)",
  },
  {
    accent: "#a09070",
    rgb: "160,144,112",
    ambient: "linear-gradient(185deg,#090807 0%,#120f0d 52%,#0a0908 100%)",
    videoGrad: "linear-gradient(135deg,#080706 0%,#130f0c 42%,#090807 100%)",
  },
];

function emptyTranscriptState(): TranscriptState {
  return { committed: [], pending: "" };
}

function emptySceneState(): SceneState {
  return {
    beatId: "",
    title: "",
    setup: "",
    escalation: "",
    actTimeLabel: "",
    narrationScript: "",
  };
}

function summaryPromptForAct(act: Pick<ActRevealState, "beatIndex" | "targetBeats">): string {
  return act.beatIndex >= act.targetBeats ? FINAL_SUMMARY_PROMPT : DEFAULT_SUMMARY_PROMPT;
}

function clearTimeoutRef(timerRef: { current: number | null }): void {
  if (timerRef.current === null) return;
  window.clearTimeout(timerRef.current);
  timerRef.current = null;
}

async function closeAudioContext(context: AudioContext | null | undefined): Promise<void> {
  if (!context || context.state === "closed") return;
  try {
    await context.close();
  } catch {
    // Multiple teardown paths can race during websocket shutdown; closing twice is harmless.
  }
}

function sceneFromPayload(payload: Record<string, unknown>): SceneState {
  return {
    beatId: (payload.beat_id as string) ?? "",
    title: (payload.title as string) ?? "",
    setup: (payload.setup as string) ?? "",
    escalation: (payload.escalation as string) ?? "",
    actTimeLabel: (payload.act_time_label as string) ?? "",
    narrationScript: (payload.narration_script as string) ?? "",
  };
}

function actRevealFromPayload(payload: Record<string, unknown>): ActRevealState {
  return {
    beatId: (payload.beat_id as string) ?? "",
    beatIndex: Number(payload.beat_index ?? 1),
    targetBeats: Number(payload.target_beats ?? 6),
    actTitle: (payload.act_title as string) ?? "Act",
    actTimeLabel: (payload.act_time_label as string) ?? "Unknown Era",
  };
}

function mergeInterleavedBlocks(
  existingBlocks: InterleavedBlock[] | undefined,
  incomingBlocks: InterleavedBlock[],
): InterleavedBlock[] {
  return [...(existingBlocks ?? []), ...incomingBlocks]
    .sort((left, right) => left.part_order - right.part_order)
    .filter(
      (block, index, array) =>
        index === array.findIndex((item) => item.part_order === block.part_order && item.kind === block.kind),
    );
}

function resolvePendingAnswer(previous: QuestionEntry[], answer: string, pending: boolean): QuestionEntry[] {
  const next = [...previous];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].pending) {
      next[index] = { ...next[index], answer, pending };
      break;
    }
  }
  return next;
}

function speechRecognitionCtor(): BrowserSpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const speechWindow = window as Window & {
    SpeechRecognition?: BrowserSpeechRecognitionCtor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionCtor;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

function parseSampleRate(mimeType: string): number {
  const match = /rate=(\d+)/i.exec(mimeType);
  if (!match) return 24000;
  const rate = Number.parseInt(match[1], 10);
  return Number.isFinite(rate) && rate > 0 ? rate : 24000;
}

function decodeBase64(data: string): Uint8Array {
  const raw = atob(data);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

function floatToInt16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index]));
    output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

function downsampleTo16k(buffer: Float32Array, inputRate: number): Int16Array {
  if (inputRate === 16000) return floatToInt16(buffer);
  const ratio = inputRate / 16000;
  const length = Math.round(buffer.length / ratio);
  const output = new Int16Array(length);
  for (let readIndex = 0, bufferIndex = 0; readIndex < length; readIndex += 1) {
    const next = Math.round((readIndex + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let index = bufferIndex; index < next && index < buffer.length; index += 1) {
      sum += buffer[index];
      count += 1;
    }
    const sample = count > 0 ? Math.max(-1, Math.min(1, sum / count)) : 0;
    output[readIndex] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    bufferIndex = next;
  }
  return output;
}

function toBrowserUri(uri?: string): string | null {
  if (!uri) return null;
  if (uri.startsWith("inline://")) return null;
  if (uri.startsWith("gs://") || uri.startsWith("https://storage.googleapis.com/")) {
    return `${API_BASE}/api/v1/assets/proxy?uri=${encodeURIComponent(uri)}`;
  }
  return uri;
}

function blockImageSource(block: InterleavedBlock | undefined): string | null {
  if (!block || block.kind !== "image") return null;
  if (block.inline_data_b64 && block.mime_type) return `data:${block.mime_type};base64,${block.inline_data_b64}`;
  const uri = toBrowserUri(block.uri);
  if (uri) return uri;
  return null;
}

function mergeTranscriptText(previous: string, incoming: string): string {
  const earlier = previous.trim();
  const latest = incoming.trim();
  if (!earlier) return latest;
  if (!latest) return earlier;
  if (earlier === latest) return earlier;
  if (latest.startsWith(earlier)) return latest;
  if (earlier.endsWith(latest)) return earlier;

  const earlierWords = earlier.split(/\s+/).filter(Boolean);
  const latestWords = latest.split(/\s+/).filter(Boolean);
  let overlapWords = 0;
  const maxWordOverlap = Math.min(earlierWords.length, latestWords.length);
  for (let size = maxWordOverlap; size > 0; size -= 1) {
    if (earlierWords.slice(-size).join(" ").toLowerCase() === latestWords.slice(0, size).join(" ").toLowerCase()) {
      overlapWords = size;
      break;
    }
  }

  const suffix = overlapWords > 0 ? latestWords.slice(overlapWords).join(" ") : latest;
  if (!suffix) return earlier;
  const spacer = /[([{/"'\s-]$/.test(earlier) || /^[,.;:!?)}\]"']/.test(suffix) ? "" : " ";
  return `${earlier}${spacer}${suffix}`.trim();
}

function applyTranscriptDelta(state: TranscriptState, text: string, isFinal: boolean, maxLines = 60): TranscriptState {
  const normalized = text.trim();
  if (!normalized) return state;

  if (!isFinal) {
    const pending = mergeTranscriptText(state.pending, normalized);
    if (state.pending === pending) return state;
    return { committed: state.committed, pending };
  }
  const finalLine = mergeTranscriptText(state.pending, normalized);
  const committed = state.committed[state.committed.length - 1] === finalLine
    ? state.committed
    : [...state.committed, finalLine].slice(-maxLines);
  return { committed, pending: "" };
}

function transcriptLines(state: TranscriptState): string[] {
  return state.pending ? [...state.committed, state.pending] : state.committed;
}

const ONBOARDING_WELCOME_SCRIPT = "Welcome to WhatIf. Say the one moment that breaks history, and I will show you the world that follows.";

function stripSpeechControlText(text: string): string {
  return text.replace(/<[^>]+>/gu, " ").replace(/\s+/g, " ").trim();
}

function sanitizeOnboardingNarration(text: string): string {
  const normalized = stripSpeechControlText(text);
  if (!normalized) return "";
  const withoutMarkdownLabel = normalized
    .replace(/^(?:[*_`#>\-\s]+)+/, "")
    .replace(/^(?:\*\*|__|`)([^*_`]{1,80}?)(?:\*\*|__|`)\s+/u, "")
    .replace(/^(?:Delivering|Crafting|Refining|Preparing|Testing)\b[^.!?]{0,120}[.!?]\s*/iu, "")
    .replace(/^I(?:'ve| have)? crafted\b[^.!?]{0,180}[.!?]\s*/iu, "")
    .replace(/^I aimed\b[^.!?]{0,180}[.!?]\s*/iu, "");
  const lowered = withoutMarkdownLabel.toLowerCase();
  const welcomeIndex = lowered.indexOf("welcome to whatif");
  const candidate = welcomeIndex >= 0 ? withoutMarkdownLabel.slice(welcomeIndex).trim() : withoutMarkdownLabel.trim();
  if (!candidate) return "";
  if (candidate.toLowerCase().includes(ONBOARDING_WELCOME_SCRIPT.toLowerCase())) return ONBOARDING_WELCOME_SCRIPT;

  const looksLikeMetaLeak = /^(?:considering|anchoring|crafting|narrating|visualizing|describing|focusing|initiating|delivering|refining|preparing|testing|analyzing|analysing|zeroing)\b/iu.test(candidate)
    || /\b(?:critical requirement|requested wording|verbatim|line meticulously|strict adherence|precise replication|deliver the given|focused on setting it up)\b/iu.test(candidate);
  if (looksLikeMetaLeak && !candidate.toLowerCase().startsWith("welcome to whatif")) return "";
  return candidate;
}

function sanitizeLiveNarration(text: string, phase: UiPhase): string {
  const normalized = stripSpeechControlText(text);
  if (!normalized) return "";
  if (phase === "onboarding") return sanitizeOnboardingNarration(normalized);

  const withoutMarkdownLabel = normalized.replace(/^(?:\*\*|__|`)([^*_`]{1,80}?)(?:\*\*|__|`)\s+/u, "").trim();
  const looksLikeMetaLeak = /^(?:considering|anchoring|crafting|narrating|visualizing|describing|focusing|initiating|delivering|refining|preparing)\b/iu.test(withoutMarkdownLabel)
    || /\b(?:i(?:'m| am)(?:\s+currently)?|i(?:'ll| have|’ll|’ve)?|my)\b/iu.test(withoutMarkdownLabel)
    || /\b(?:objective|understanding|as instructed|provided script|focusing on|crafting the|describing the|integrating the|concentrating on|immersed in|executing the|setting the scene|building up)\b/iu.test(withoutMarkdownLabel);
  if ((phase === "acting" || phase === "actReveal") && looksLikeMetaLeak) return "";
  return looksLikeMetaLeak ? "" : withoutMarkdownLabel;
}

function hasNestedFlag(value: unknown, keys: readonly string[]): boolean {
  if (!value || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.some((item) => hasNestedFlag(item, keys));
  return Object.entries(value).some(([key, nested]) => {
    if (keys.includes(key) && nested === true) return true;
    return hasNestedFlag(nested, keys);
  });
}

function parseMomentIndex(text: string, maxMoments: number): number {
  const normalized = text.toLowerCase();
  const numeric = normalized.match(/\b([1-6])\b/);
  if (numeric) return Math.max(0, Math.min(Number.parseInt(numeric[1], 10) - 1, maxMoments - 1));

  const ordinals: Array<[RegExp, number]> = [
    [/\bfirst\b/, 0],
    [/\bsecond\b/, 1],
    [/\bthird\b/, 2],
    [/\bfourth\b/, 3],
    [/\bfifth\b/, 4],
    [/\bsixth\b/, 5],
    [/\blast\b/, Math.max(maxMoments - 1, 0)],
  ];
  for (const [pattern, index] of ordinals) {
    if (pattern.test(normalized)) return Math.max(0, Math.min(index, maxMoments - 1));
  }
  return 0;
}

function parseVoiceCommand(text: string, maxMoments: number): VoiceCommand {
  const normalized = text.trim().toLowerCase();
  if (!normalized) return null;

  if (/\b(continue|next act|move on|go on|proceed)\b/.test(normalized)) {
    return { kind: "continue" };
  }

  if (
    /\b(repeat|again|re-?narrate|say that again|go back|show me|rewind|replay|revisit)\b/.test(normalized)
    && /\b(scene|image|shot|moment|act)\b/.test(normalized)
  ) {
    return { kind: "replay", momentIndex: parseMomentIndex(normalized, maxMoments) };
  }

  if (/\b(repeat|again|re-?narrate|rewind|replay)\b/.test(normalized)) {
    return { kind: "replay", momentIndex: 0 };
  }

  return null;
}

function momentDurationMs(moment: SceneMoment | undefined, text?: string): number {
  const source = text?.trim() || moment?.body || "";
  const words = source.split(/\s+/).filter(Boolean).length;
  return Math.min(10000, Math.max(3800, words * 145));
}

function debugLog(event: string, details?: Record<string, unknown>) {
  if (process.env.NODE_ENV === "production") return;
  if (details) {
    console.info(`[whatify-ui] ${event}`, details);
    return;
  }
  console.info(`[whatify-ui] ${event}`);
}

function actTheme(beatIndex: number): ActTheme {
  return ACT_THEMES[(Math.max(beatIndex, 1) - 1) % ACT_THEMES.length];
}

function firstSentence(text: string): string {
  const trimmed = text.replace(/\s+/g, " ").trim();
  if (!trimmed) return "";
  const sentence = trimmed.split(/(?<=[.!?])\s+/)[0] ?? trimmed;
  return sentence.slice(0, 140);
}

function narrativeSegments(text: string, maxSegments = MAX_SCENE_MOMENTS): string[] {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return [];

  const sentences = normalized
    .split(/(?<=[.!?])\s+/)
    .map((segment) => segment.trim())
    .filter(Boolean);

  if (sentences.length === 0) return [normalized];
  if (sentences.length <= maxSegments) return sentences;

  const grouped: string[] = [];
  const bucketSize = Math.ceil(sentences.length / maxSegments);
  for (let index = 0; index < sentences.length; index += bucketSize) {
    grouped.push(sentences.slice(index, index + bucketSize).join(" "));
  }
  return grouped.slice(0, maxSegments);
}

function sortedStoryboardImages(storyboardAssets: Record<string, string>): string[] {
  return Object.entries(storyboardAssets)
    .sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }))
    .map(([, uri]) => toBrowserUri(uri))
    .filter(Boolean) as string[];
}

function fallbackBackdrop(theme: ActTheme, index: number): string {
  const variants = [
    `radial-gradient(circle at 22% 78%, rgba(${theme.rgb},.35) 0%, transparent 34%), radial-gradient(circle at 78% 24%, rgba(255,255,255,.06) 0%, transparent 18%), ${theme.ambient}`,
    `radial-gradient(circle at 62% 28%, rgba(${theme.rgb},.3) 0%, transparent 32%), radial-gradient(circle at 20% 24%, rgba(255,255,255,.04) 0%, transparent 16%), ${theme.ambient}`,
    `radial-gradient(circle at 50% 84%, rgba(${theme.rgb},.32) 0%, transparent 36%), radial-gradient(circle at 78% 35%, rgba(255,255,255,.05) 0%, transparent 18%), ${theme.ambient}`,
  ];
  return variants[index % variants.length];
}

function splitFallbackMoments(scene: SceneState, theme: ActTheme): SceneMoment[] {
  const source = [scene.setup, scene.escalation, scene.narrationScript]
    .map((line) => line.trim())
    .filter(Boolean)
    .join(" ");

  const segments = narrativeSegments(source);
  if (segments.length === 0) {
    return [
      {
        id: "fallback-0",
        caption: scene.title || "A new act begins",
        body: "History bends around a single altered decision, and the consequences begin to spread outward.",
        imageSrc: null,
        backdrop: fallbackBackdrop(theme, 0),
      },
    ];
  }
  return segments.slice(0, MAX_SCENE_MOMENTS).map((text, index) => ({
    id: `fallback-${index}`,
    caption: firstSentence(text) || scene.title || `Scene ${index + 1}`,
    body: text,
    imageSrc: null,
    backdrop: fallbackBackdrop(theme, index),
  }));
}

function buildMoments(
  run: InterleavedRun | null,
  storyboardAssets: Record<string, string>,
  scene: SceneState,
  theme: ActTheme,
): SceneMoment[] {
  const storyboardImages = sortedStoryboardImages(storyboardAssets);
  const moments: SceneMoment[] = [];
  const pendingText: string[] = [];

  if (run) {
    for (const block of run.blocks) {
      if (block.kind === "text" && block.text) {
        pendingText.push(block.text.trim());
        continue;
      }
      if (block.kind === "image") {
        const body = pendingText.join(" ").trim() || scene.setup || scene.narrationScript || scene.escalation;
        moments.push({
          id: `${run.run_id}-${moments.length}`,
          caption: firstSentence(body) || scene.title || `Scene ${moments.length + 1}`,
          body,
          imageSrc: blockImageSource(block),
          backdrop: fallbackBackdrop(theme, moments.length),
        });
        pendingText.length = 0;
      }
    }

    if (pendingText.length > 0) {
      const body = pendingText.join(" ").trim();
      moments.push({
        id: `${run.run_id}-${moments.length}`,
        caption: firstSentence(body) || scene.title || `Scene ${moments.length + 1}`,
        body,
        imageSrc: null,
        backdrop: fallbackBackdrop(theme, moments.length),
      });
    }
  }

  if (moments.length === 0) {
    const fallback = splitFallbackMoments(scene, theme);
    fallback.forEach((moment, index) => {
      moments.push({
        ...moment,
        imageSrc: storyboardImages[index] ?? null,
      });
    });
    return moments.slice(0, MAX_SCENE_MOMENTS);
  }

  let storyboardIndex = 0;
  for (let index = 0; index < moments.length; index += 1) {
    if (!moments[index].imageSrc && storyboardImages[storyboardIndex]) {
      moments[index] = { ...moments[index], imageSrc: storyboardImages[storyboardIndex] };
      storyboardIndex += 1;
    }
  }

  while (storyboardIndex < storyboardImages.length && moments.length < MAX_SCENE_MOMENTS) {
    const fallback = splitFallbackMoments(scene, theme);
    const seed = fallback[moments.length % fallback.length];
    moments.push({
      ...seed,
      id: `storyboard-${storyboardIndex}`,
      imageSrc: storyboardImages[storyboardIndex],
      backdrop: fallbackBackdrop(theme, storyboardIndex),
    });
    storyboardIndex += 1;
  }

  return moments.slice(0, MAX_SCENE_MOMENTS);
}

function expectedActImageCount(
  run: InterleavedRun | null,
  storyboardAssets: Record<string, string>,
  storyboardExpectedCount: number,
  moments: SceneMoment[],
): number {
  const interleavedImageCount = run?.blocks.filter((block) => block.kind === "image").length ?? 0;
  return Math.min(
    MAX_SCENE_MOMENTS,
    Math.max(interleavedImageCount, storyboardExpectedCount, Object.keys(storyboardAssets).length, moments.length),
  );
}

function isActReadyForPlayback(moments: SceneMoment[], expectedImageCount: number): boolean {
  if (moments.length === 0) return false;
  if (expectedImageCount <= 0) return true;
  return Boolean(moments[0]?.imageSrc);
}

function sceneNarrationPrompt(payload: SceneState, moment: SceneMoment, momentIndex: number, totalMoments: number): string {
  return [
    `Narrate scene ${momentIndex + 1} of ${totalMoments} from the current act only.`,
    payload.title ? `Act title: ${payload.title}.` : "",
    payload.actTimeLabel ? `Time label: ${payload.actTimeLabel}.` : "",
    moment.caption ? `Visible image: ${moment.caption}.` : "",
    `On-screen story text: ${moment.body}.`,
    "First describe exactly what the viewer sees in this image, then deliver the on-screen story text as polished cinematic narration.",
    "Stay inside this single scene. Do not mention future scenes, the next act, hidden planning, or your process.",
    "Keep it vivid, grounded, and no longer than four spoken sentences.",
  ].filter(Boolean).join(" ");
}

function followupExcerpt(text: string, maxLength = 180): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength).trimEnd()}...`;
}

function FilmGrain() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 999,
        pointerEvents: "none",
        opacity: 0.052,
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        backgroundSize: "180px 180px",
        animation: "grain .38s steps(1) infinite",
      }}
    />
  );
}

function Letterbox({ show }: { show: boolean }) {
  return (
    <>
      <div
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          height: "8.5vh",
          background: "#000",
          zIndex: 90,
          transform: show ? "scaleY(1)" : "scaleY(0)",
          transformOrigin: "top",
          transition: "transform .95s cubic-bezier(.4,0,.2,1)",
        }}
      />
      <div
        style={{
          position: "fixed",
          bottom: 0,
          left: 0,
          right: 0,
          height: "8.5vh",
          background: "#000",
          zIndex: 90,
          transform: show ? "scaleY(1)" : "scaleY(0)",
          transformOrigin: "bottom",
          transition: "transform .95s cubic-bezier(.4,0,.2,1)",
        }}
      />
    </>
  );
}

function Orb({
  size = "large",
  listening = false,
  onClick,
  label,
  accent,
}: {
  size?: "large" | "medium" | "small";
  listening?: boolean;
  onClick?: () => void;
  label?: string;
  accent?: string;
}) {
  const base = size === "large" ? 148 : size === "medium" ? 86 : 40;
  const glow = listening ? 1.8 : 1;
  const ring = accent ?? "#c4781a";
  const ringRgb = ring === "#c4781a" ? "196,120,38" : ring === "#3a78c4" ? "58,120,196" : ring === "#2a9450" ? "42,148,80" : "196,120,38";
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: base * 0.09,
        cursor: onClick ? "pointer" : "default",
        userSelect: "none",
      }}
    >
      <div style={{ position: "relative", width: base, height: base, display: "flex", alignItems: "center", justifyContent: "center" }}>
        {listening && [0, 0.55, 1.1].map((delay, index) => (
          <div
            key={index}
            style={{
              position: "absolute",
              width: base,
              height: base,
              borderRadius: "50%",
              border: `1px solid rgba(${ringRgb},${0.35 - index * 0.08})`,
              animation: `ring-pulse 2.2s ease-out ${delay}s infinite`,
              pointerEvents: "none",
            }}
          />
        ))}
        <div
          style={{
            position: "absolute",
            width: base,
            height: base,
            borderRadius: "50%",
            background: `radial-gradient(circle at 38% 33%, rgba(${ringRgb},${0.16 * glow}) 0%, transparent 68%)`,
            boxShadow: `0 0 ${base * 0.37}px rgba(${ringRgb},${0.13 * glow})`,
            animation: listening ? "orb-listen 1.6s ease-in-out infinite" : "orb-breathe 4.5s ease-in-out infinite",
          }}
        />
        <div
          style={{
            position: "absolute",
            width: base * 0.72,
            height: base * 0.72,
            borderRadius: "50%",
            background: `radial-gradient(circle at 36% 30%, rgba(215,148,48,${0.28 * glow}) 0%, rgba(140,72,16,.18) 50%, transparent 70%)`,
            boxShadow: `0 0 ${base * 0.18}px rgba(${ringRgb},${0.22 * glow}), inset 0 0 ${base * 0.12}px rgba(0,0,0,.42)`,
          }}
        />
        <div
          style={{
            position: "absolute",
            width: base * 0.42,
            height: base * 0.42,
            borderRadius: "50%",
            background: `radial-gradient(circle at 33% 28%, #f2c258 0%, ${ring} 38%, #6a2e0c 78%, #180a04 100%)`,
            boxShadow: `0 0 ${base * 0.12}px rgba(240,178,72,${0.55 * glow}), 0 0 ${base * 0.24}px rgba(${ringRgb},${0.32 * glow})`,
            animation: listening ? "orb-listen 1.6s ease-in-out infinite" : "orb-breathe 4.5s ease-in-out infinite",
          }}
        />
      </div>
      {label && (
        <div
          style={{
            fontFamily: "'Cinzel',serif",
            fontSize: base * 0.07 + 1,
            letterSpacing: ".38em",
            color: accent ? accent : `rgba(196,128,38,${listening ? 0.9 : 0.6})`,
            textTransform: "uppercase",
            transition: "color .4s",
          }}
        >
          {label}
        </div>
      )}
    </div>
  );
}

function NarrationWords({
  text,
  progress,
  accent,
}: {
  text: string;
  progress: number;
  accent: string;
}) {
  const words = text.split(" ").filter(Boolean);
  const visible = Math.min(words.length, Math.floor(words.length * Math.min(progress, 1)));
  return (
    <p
      style={{
        fontFamily: "'EB Garamond',serif",
        fontWeight: 400,
        fontSize: "clamp(18px,2vw,25px)",
        lineHeight: 1.66,
        color: "rgba(242,234,212,.92)",
        textShadow: "0 2px 22px rgba(0,0,0,.78)",
      }}
    >
      {words.map((word, index) => (
        <span
          key={`${word}-${index}`}
          style={{
            opacity: index < visible ? 1 : 0.14,
            transition: "opacity .22s ease",
            color: index < visible && index === visible - 1 ? accent : undefined,
          }}
        >
          {word}{" "}
        </span>
      ))}
    </p>
  );
}

function IntroScreen({ onStart, busy }: { onStart: () => void; busy: boolean }) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#060504",
        animation: "fade-in 1.2s ease",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: 480,
          height: 480,
          borderRadius: "50%",
          background: "radial-gradient(circle,rgba(196,120,26,.052) 0%,transparent 70%)",
          top: "50%",
          left: "50%",
          transform: "translate(-50%,-50%)",
          pointerEvents: "none",
        }}
      />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <div
          style={{
            fontFamily: "'Raleway',sans-serif",
            fontWeight: 300,
            fontSize: 10,
            letterSpacing: ".5em",
            color: "rgba(196,120,38,.38)",
            textTransform: "uppercase",
            animation: "fade-up 1s ease .4s both",
          }}
        >
          An Alternate History Engine
        </div>
        <div
          style={{
            fontFamily: "'Cinzel',serif",
            fontWeight: 600,
            fontSize: "clamp(52px,8.5vw,96px)",
            letterSpacing: ".18em",
            color: "#f2ead6",
            lineHeight: 1,
            animation: "fade-up 1s ease .65s both, flicker 8s ease-in-out 2s infinite",
            textShadow: "0 0 80px rgba(196,120,26,.07)",
          }}
        >
          WHATIFY
        </div>
        <div
          style={{
            width: 72,
            height: 1,
            background: "linear-gradient(90deg,transparent,rgba(196,120,38,.38),transparent)",
            animation: "fade-in 1s ease 1s both",
          }}
        />
        <div style={{ animation: "fade-up 1s ease 1.1s both" }}>
          <Orb size="large" listening={busy} onClick={busy ? undefined : onStart} label={busy ? "Opening" : "Enter"} />
        </div>
        <div
          style={{
            maxWidth: 420,
            fontFamily: "'EB Garamond',serif",
            fontStyle: "italic",
            fontSize: 19,
            lineHeight: 1.7,
            color: "rgba(242,234,214,.62)",
            textAlign: "center",
            animation: "fade-up 1s ease 1.35s both",
          }}
        >
          Enter once, say the exact moment history changes, and the chronicle begins only after your voice lands.
        </div>
        <div
          style={{
            fontFamily: "'EB Garamond',serif",
            fontStyle: "italic",
            fontSize: 15,
            color: "rgba(242,234,214,.22)",
            letterSpacing: ".08em",
            animation: "fade-in 1s ease 1.9s both",
          }}
        >
          {busy ? "Opening the chamber" : "Voice-first. One spoken divergence. Then the story starts."}
        </div>
      </div>
    </div>
  );
}

function OnboardingScreen({
  theme,
  aiLine,
  onMicStart,
  listening,
  liveConnected,
  introPlaying,
  handoffPlaying,
}: {
  theme: ActTheme;
  aiLine: string;
  onMicStart: () => void;
  listening: boolean;
  liveConnected: boolean;
  introPlaying: boolean;
  handoffPlaying: boolean;
}) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#060504",
        animation: "fade-in .6s ease",
      }}
    >
      <div style={{ position: "absolute", inset: 0, background: fallbackBackdrop(theme, 0), opacity: 0.4 }} />
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,rgba(0,0,0,.68) 0%,rgba(0,0,0,.44) 35%,rgba(0,0,0,.78) 100%)" }} />
      <div style={{ position: "relative", zIndex: 2, width: "min(640px,88vw)" }}>
        <div
          style={{
            padding: "38px 32px 34px",
            border: `1px solid rgba(${theme.rgb},.2)`,
            background: "rgba(8,6,5,.72)",
            backdropFilter: "blur(10px)",
            boxShadow: `0 30px 100px rgba(0,0,0,.48), inset 0 1px 0 rgba(255,255,255,.04)`,
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 22 }}>
            <div
              style={{
                fontFamily: "'Raleway',sans-serif",
                fontWeight: 200,
                fontSize: 10,
                letterSpacing: ".55em",
                color: `rgba(${theme.rgb},.46)`,
                textTransform: "uppercase",
                textAlign: "center",
              }}
            >
              State The Divergence
            </div>
            <p
              style={{
                margin: 0,
                fontFamily: "'EB Garamond',serif",
                fontStyle: "italic",
                fontSize: "clamp(22px,2.5vw,30px)",
                lineHeight: 1.56,
                color: "rgba(242,234,214,.86)",
                textAlign: "center",
                maxWidth: 440,
              }}
            >
              Tap speak, say the one moment where history changes, then pause.
            </p>
            <div
              style={{
                fontFamily: "'EB Garamond',serif",
                fontStyle: "italic",
                fontSize: 16,
                lineHeight: 1.65,
                color: "rgba(242,234,214,.46)",
                textAlign: "center",
                maxWidth: 460,
              }}
            >
              Example: “What if the Library of Alexandria never burned?”
            </div>
            <div
              style={{
                minHeight: 56,
                maxWidth: 470,
                overflowY: "auto",
                paddingRight: 6,
                scrollbarWidth: "thin",
                fontFamily: "'EB Garamond',serif",
                fontStyle: "italic",
                fontSize: 17,
                lineHeight: 1.55,
                color: "rgba(242,234,214,.72)",
                textAlign: "center",
              }}
            >
              {aiLine || "The voice guide will welcome you, echo the divergence, and then hand the story off to the engine."}
            </div>
            <Orb
              size="large"
              listening={listening}
              onClick={listening || introPlaying || handoffPlaying || !liveConnected ? undefined : onMicStart}
              label={introPlaying ? "Intro" : handoffPlaying ? "Opening" : listening ? "Listening" : "Speak"}
              accent={theme.accent}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 4, height: 36 }}>
              {WAVE_HEIGHTS.map((height, index) => (
                <div
                  key={index}
                  style={{
                    width: 3,
                    height,
                    background: `rgba(${theme.rgb},${0.38 + (height / 30) * 0.5})`,
                    borderRadius: 2,
                    transformOrigin: "center",
                    animation: `wave-b ${0.5 + WAVE_DELAYS[index] * 1.2}s ease-in-out ${WAVE_DELAYS[index]}s infinite`,
                  }}
                />
              ))}
            </div>
            <div
              style={{
                fontFamily: "'EB Garamond',serif",
                fontStyle: "italic",
                fontSize: 16,
                color: "rgba(242,234,214,.48)",
                textAlign: "center",
                lineHeight: 1.7,
                minHeight: 50,
                maxHeight: 110,
                overflowY: "auto",
                paddingRight: 6,
                scrollbarWidth: "thin",
                maxWidth: 480,
              }}
            >
              {listening
                ? "Listening to your microphone. State the divergence once, then pause."
                : introPlaying
                  ? "The cinematic intro is speaking."
                  : handoffPlaying
                    ? "Opening the story while the timeline is being built."
                    : "Nothing starts until you speak."}
            </div>
            <div
              style={{
                fontFamily: "'Raleway',sans-serif",
                fontWeight: 200,
                fontSize: 10,
                letterSpacing: ".45em",
                color: `rgba(${theme.rgb},.46)`,
                textTransform: "uppercase",
                textAlign: "center",
              }}
            >
              {introPlaying
                ? "Intro is speaking. Mic unlocks when it ends."
                : handoffPlaying
                  ? "Backend started immediately. Live voice is giving a short handoff."
                : liveConnected
                  ? "Only your mic is streamed into Gemini Live."
                  : "Connecting Gemini Live..."}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProcessingScreen({ theme }: { theme: ActTheme }) {
  const steps = [
    "Analysing historical pivot...",
    "Projecting the full alternate timeline...",
    "Structuring the chronicle into acts...",
    "Generating visuals for the current act...",
    "Prefetching the next act in the background...",
  ];
  const [step, setStep] = useState(0);

  useEffect(() => {
    const interval = window.setInterval(() => setStep((value) => Math.min(value + 1, steps.length - 1)), 520);
    return () => window.clearInterval(interval);
  }, [steps.length]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#060504",
        animation: "fade-in .5s ease",
        gap: 34,
      }}
    >
      <div style={{ position: "relative", width: 88, height: 88 }}>
        {[76, 60, 44].map((size, index) => (
          <div
            key={size}
            style={{
              position: "absolute",
              top: (88 - size) / 2,
              left: (88 - size) / 2,
              width: size,
              height: size,
              borderRadius: "50%",
              border: `1px solid rgba(${theme.rgb},${0.18 + index * 0.1})`,
              borderTopColor: `rgba(${theme.rgb},${0.55 + index * 0.1})`,
              animation: `${index % 2 === 0 ? "spin" : "spin-r"} ${1.8 - index * 0.3}s linear infinite`,
            }}
          />
        ))}
        <div
          style={{
            position: "absolute",
            top: 30,
            left: 30,
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: `radial-gradient(circle at 33% 28%, #f2c258 0%, ${theme.accent} 42%, #180a04 100%)`,
            boxShadow: `0 0 16px rgba(${theme.rgb},.42)`,
          }}
        />
      </div>
      <div
        style={{
          fontFamily: "'Cinzel',serif",
          fontSize: 11,
          letterSpacing: ".38em",
          color: `rgba(${theme.rgb},.42)`,
          textTransform: "uppercase",
        }}
      >
        Rewriting History
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start" }}>
        {steps.map((item, index) => (
          <div
            key={item}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 300,
              fontSize: 12,
              letterSpacing: ".05em",
              color: `rgba(242,234,214,${index <= step ? 0.63 : 0.18})`,
              transition: "all .4s ease",
            }}
          >
            <div
              style={{
                width: 5,
                height: 5,
                borderRadius: "50%",
                background: index < step ? theme.accent : index === step ? "#f0c058" : `rgba(${theme.rgb},.18)`,
                boxShadow: index === step ? "0 0 8px rgba(240,192,88,.6)" : "none",
                animation: index === step ? "dot-pulse 1s ease-in-out infinite" : "none",
              }}
            />
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function ActReveal({
  act,
  stage,
  previewMoment,
}: {
  act: ActRevealState;
  stage: number;
  previewMoment: SceneMoment | null;
}) {
  const theme = actTheme(act.beatIndex);
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 80,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,.96)",
        opacity: stage === 0 ? 0 : 1,
        transition: "opacity .7s ease",
        pointerEvents: "none",
      }}
    >
      <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
        {previewMoment?.imageSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewMoment.imageSrc}
            alt=""
            style={{
              position: "absolute",
              inset: "-3%",
              width: "106%",
              height: "106%",
              objectFit: "cover",
              filter: "blur(12px) saturate(.72) brightness(.36)",
              transform: "scale(1.06)",
              animation: "ken 12s ease-in-out infinite alternate",
            }}
          />
        ) : (
          <div style={{ position: "absolute", inset: 0, background: theme.ambient }} />
        )}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "radial-gradient(circle at 50% 45%, rgba(255,255,255,.06) 0%, transparent 20%), linear-gradient(180deg, rgba(0,0,0,.84) 0%, rgba(0,0,0,.56) 38%, rgba(0,0,0,.92) 100%)",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `radial-gradient(circle at 50% 50%, rgba(${theme.rgb},.16) 0%, transparent 42%)`,
            opacity: stage >= 2 ? 1 : 0.2,
            transition: "opacity .8s ease",
          }}
        />
      </div>
      {stage >= 1 && (
        <div style={{ textAlign: "center", animation: "fade-up .7s ease", position: "relative", zIndex: 2, padding: "0 8vw" }}>
          <div
            style={{
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 200,
              fontSize: 10,
              letterSpacing: ".6em",
              color: `rgba(${theme.rgb},.44)`,
              textTransform: "uppercase",
              marginBottom: 18,
            }}
          >
            {`Act ${act.beatIndex}`}
          </div>
          <div
            style={{
              fontFamily: "'Cinzel',serif",
              fontWeight: 600,
              fontSize: "clamp(30px,4.8vw,66px)",
              letterSpacing: ".12em",
              color: "#f2ead6",
              marginBottom: 18,
              animation: "reveal-title .82s ease .12s both",
              textShadow: `0 0 36px rgba(${theme.rgb},.16)`,
            }}
          >
            {act.actTitle}
          </div>
          <div
            style={{
              width: 116,
              height: 1,
              margin: "0 auto 20px",
              background: `linear-gradient(90deg, transparent, rgba(${theme.rgb},.72), transparent)`,
              animation: "line-grow .7s ease .3s both",
            }}
          />
          <div
            style={{
              fontFamily: "'EB Garamond',serif",
              fontStyle: "italic",
              fontSize: 16,
              color: "rgba(242,234,214,.52)",
              letterSpacing: ".16em",
              animation: "fade-in .6s ease .5s both",
            }}
          >
            {act.actTimeLabel}
          </div>
          {stage >= 2 && (
            <div
              style={{
                marginTop: 24,
                fontFamily: "'EB Garamond',serif",
                fontStyle: "italic",
                fontSize: 18,
                lineHeight: 1.6,
                color: "rgba(242,234,214,.66)",
                animation: "fade-in .75s ease .2s both",
              }}
            >
              {previewMoment?.caption || "A new world gathers behind the curtain."}
            </div>
          )}
          {stage >= 2 && (
            <div
              style={{
                marginTop: 14,
                fontFamily: "'Raleway',sans-serif",
                fontWeight: 300,
                fontSize: 10,
                letterSpacing: ".34em",
                color: `rgba(${theme.rgb},.52)`,
                textTransform: "uppercase",
                animation: "fade-in .75s ease .35s both",
              }}
            >
              The chronicle opens
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SceneFilmstrip({
  theme,
  act,
  moments,
  momentIndex,
  progress,
  narrationText,
}: {
  theme: ActTheme;
  act: ActRevealState;
  moments: SceneMoment[];
  momentIndex: number;
  progress: number;
  narrationText: string;
}) {
  const currentMoment = moments[Math.min(momentIndex, Math.max(moments.length - 1, 0))];
  if (!currentMoment) return null;
  const displayBody = narrationText.trim() || currentMoment.body;
  const displayCaption = firstSentence(displayBody) || currentMoment.caption;
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden" }}>
      <div key={currentMoment.id} style={{ position: "absolute", inset: 0, overflow: "hidden", animation: "fade-in .55s ease" }}>
        {currentMoment.imageSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={currentMoment.imageSrc}
            alt=""
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              animation: `${momentIndex % 2 === 0 ? "ken" : "ken2"} 18s ease-in-out infinite alternate`,
            }}
          />
        ) : (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: currentMoment.backdrop,
              animation: `${momentIndex % 2 === 0 ? "ken" : "ken2"} 18s ease-in-out infinite alternate`,
            }}
          />
        )}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "linear-gradient(180deg,rgba(3,3,3,.62) 0%,rgba(3,3,3,.12) 28%,rgba(3,3,3,.28) 58%,rgba(3,3,3,.78) 100%)",
            pointerEvents: "none",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: "radial-gradient(ellipse at 52% 46%, transparent 22%, rgba(0,0,0,.78) 100%)",
            pointerEvents: "none",
          }}
        />
      </div>

      <div style={{ position: "absolute", top: "11.8vh", left: "50%", transform: "translateX(-50%)", display: "flex", gap: 9, zIndex: 20, alignItems: "center" }}>
        {moments.map((_, index) => (
          <div
            key={index}
            style={{
              height: 3,
              borderRadius: 2,
              width: index === momentIndex ? 22 : 6,
              background: index < momentIndex ? `rgba(${theme.rgb},.55)` : index === momentIndex ? theme.accent : `rgba(${theme.rgb},.18)`,
              boxShadow: index === momentIndex ? `0 0 8px ${theme.accent}77` : "none",
              transition: "all .55s cubic-bezier(.4,0,.2,1)",
            }}
          />
        ))}
      </div>

      <div style={{ position: "absolute", inset: 0, zIndex: 20, pointerEvents: "none", display: "flex", alignItems: "flex-end", justifyContent: "space-between", padding: "13vh 5vw 18vh", gap: 20 }}>
        <div
          style={{
            width: "min(560px, 100%)",
            maxHeight: "42vh",
            padding: "22px 24px 24px",
            borderRadius: 28,
            border: `1px solid rgba(${theme.rgb},.24)`,
            background:
              `linear-gradient(180deg, rgba(18,14,12,.34) 0%, rgba(14,11,10,.2) 100%), radial-gradient(circle at 18% 0%, rgba(${theme.rgb},.12) 0%, transparent 45%)`,
            backdropFilter: "blur(22px) saturate(118%)",
            boxShadow: `0 28px 80px rgba(0,0,0,.34), inset 0 1px 0 rgba(255,255,255,.08), inset 0 -1px 0 rgba(${theme.rgb},.12)`,
            animation: "caption-in .45s ease both",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 200,
              fontSize: 9,
              letterSpacing: ".48em",
              color: `rgba(${theme.rgb},.52)`,
              textTransform: "uppercase",
              marginBottom: 10,
            }}
          >
            {`Act ${act.beatIndex} · Scene ${momentIndex + 1} · ${act.actTimeLabel}`}
          </div>
          <div
            style={{
              fontFamily: "'EB Garamond',serif",
              fontStyle: "italic",
              fontSize: "clamp(14px,1.5vw,20px)",
              color: "rgba(242,234,214,.68)",
              letterSpacing: ".03em",
              lineHeight: 1.58,
              marginBottom: 16,
            }}
          >
            {displayCaption}
          </div>
          <div style={{ maxHeight: "24vh", overflowY: "auto", paddingRight: 6, scrollbarWidth: "thin" }}>
            <NarrationWords
              text={displayBody}
              progress={progress}
              accent={theme.accent}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function ActTimeline({ current, total, theme }: { current: number; total: number; theme: ActTheme }) {
  return (
    <div
      style={{
        position: "fixed",
        bottom: "3.6vh",
        left: "50%",
        transform: "translateX(-50%)",
        display: "flex",
        alignItems: "center",
        gap: 9,
        zIndex: 95,
      }}
    >
      {Array.from({ length: total }).map((_, index) => {
        const dotTheme = actTheme(index + 1);
        return (
          <div
            key={index}
            style={{
              height: 3,
              borderRadius: 2,
              width: index === current ? 24 : 6,
              background: index < current ? `rgba(${theme.rgb},.52)` : index === current ? dotTheme.accent : `rgba(${theme.rgb},.14)`,
              boxShadow: index === current ? `0 0 8px ${dotTheme.accent}66` : "none",
              transition: "all .5s cubic-bezier(.4,0,.2,1)",
            }}
          />
        );
      })}
    </div>
  );
}

function AskPanel({
  theme,
  value,
  onChange,
  onSubmit,
  disabled,
}: {
  theme: ActTheme;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12 }}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="Ask about the consequences of this act..."
        style={{
          width: "100%",
          minHeight: 88,
          resize: "none",
          background: "rgba(255,255,255,.02)",
          border: `1px solid rgba(${theme.rgb},.18)`,
          color: "#f2ead6",
          padding: "14px 16px",
          outline: "none",
          fontFamily: "'EB Garamond',serif",
          fontSize: 16,
          lineHeight: 1.55,
        }}
      />
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button
          onClick={onSubmit}
          disabled={!value.trim() || disabled}
          style={{
            background: !value.trim() || disabled ? "rgba(255,255,255,.08)" : theme.accent,
            color: !value.trim() || disabled ? "rgba(242,234,214,.42)" : "#060504",
            border: "none",
            padding: "12px 20px",
            fontFamily: "'Cinzel',serif",
            fontSize: 10,
            letterSpacing: ".25em",
            textTransform: "uppercase",
            cursor: !value.trim() || disabled ? "not-allowed" : "pointer",
          }}
        >
          Ask About This Act
        </button>
      </div>
    </div>
  );
}

function SummaryCard({
  theme,
  act,
  moments,
  heroVideoUri,
  prompt,
  questionDraft,
  setQuestionDraft,
  onAsk,
  onVoiceAsk,
  onContinue,
  onReplayAct,
  onReplayMoment,
  qaEntries,
  asking,
  listening,
  liveConnected,
  latestLine,
}: {
  theme: ActTheme;
  act: ActRevealState;
  moments: SceneMoment[];
  heroVideoUri: string | null;
  prompt: string;
  questionDraft: string;
  setQuestionDraft: (value: string) => void;
  onAsk: () => void;
  onVoiceAsk: () => void;
  onContinue: () => void;
  onReplayAct: () => void;
  onReplayMoment: (index: number) => void;
  qaEntries: QuestionEntry[];
  asking: boolean;
  listening: boolean;
  liveConnected: boolean;
  latestLine: string;
}) {
  const fallback = moments[0];
  const isFinal = act.beatIndex >= act.targetBeats;
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !heroVideoUri) return undefined;
    video.muted = true;
    const attemptPlay = () => {
      const playback = video.play();
      if (playback && typeof playback.catch === "function") {
        playback.catch(() => {});
      }
    };
    video.load();
    attemptPlay();
    video.addEventListener("canplay", attemptPlay);
    return () => video.removeEventListener("canplay", attemptPlay);
  }, [heroVideoUri]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 40,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,.86)",
        animation: "fade-in .65s ease",
        padding: "12vh 3vw 12vh",
      }}
    >
      <div style={{ width: "min(1220px,94vw)", maxHeight: "70vh", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 28, alignItems: "stretch" }}>
        <div style={{ minHeight: 0 }}>
          <div
            style={{
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 200,
              fontSize: 10,
              letterSpacing: ".45em",
              color: `rgba(${theme.rgb},.48)`,
              textTransform: "uppercase",
              marginBottom: 13,
              textAlign: "center",
            }}
          >
            {`ACT ${act.beatIndex} · ${act.actTitle}`}
          </div>

          <div
            style={{
              position: "relative",
              width: "100%",
              aspectRatio: "16/9",
              background: heroVideoUri ? "#000" : fallback?.backdrop ?? theme.videoGrad,
              border: `1px solid rgba(${theme.rgb},.2)`,
              boxShadow: `0 0 60px rgba(${theme.rgb},.12),0 40px 80px rgba(0,0,0,.65)`,
              overflow: "hidden",
              animation: "vid-in .65s ease",
            }}
          >
            {heroVideoUri ? (
              <video
                ref={videoRef}
                src={heroVideoUri}
                autoPlay
                muted
                loop
                controls
                playsInline
                preload="auto"
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
            ) : fallback?.imageSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={fallback.imageSrc} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", animation: "ken 16s ease-in-out infinite alternate" }} />
            ) : (
              <div style={{ position: "absolute", inset: 0, background: theme.videoGrad }} />
            )}
            <div
              style={{
                position: "absolute",
                inset: 0,
                pointerEvents: "none",
                backgroundImage: "repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.05) 2px,rgba(0,0,0,.05) 4px)",
              }}
            />
          </div>

          <div
            style={{
              fontFamily: "'EB Garamond',serif",
              fontStyle: "italic",
              fontSize: 15,
              color: "rgba(242,234,214,.34)",
              textAlign: "center",
              marginTop: 14,
              letterSpacing: ".05em",
            }}
          >
            {heroVideoUri ? "Play back the generated act video, then continue when you are ready." : "The act visuals are ready. The video summary will appear here as soon as rendering completes."}
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, justifyContent: "center", marginTop: 18 }}>
            <button
              onClick={onReplayAct}
              style={{
                border: `1px solid rgba(${theme.rgb},.24)`,
                background: "rgba(255,255,255,.03)",
                color: "#f2ead6",
                padding: "10px 14px",
                fontFamily: "'Cinzel',serif",
                fontSize: 10,
                letterSpacing: ".18em",
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              Re-Narrate Act
            </button>
            {moments.slice(0, MAX_SCENE_MOMENTS).map((moment, index) => (
              <button
                key={moment.id}
                onClick={() => onReplayMoment(index)}
                style={{
                  border: `1px solid rgba(${theme.rgb},.18)`,
                  background: "rgba(255,255,255,.02)",
                  color: "rgba(242,234,214,.86)",
                  padding: "10px 12px",
                  fontFamily: "'Raleway',sans-serif",
                  fontSize: 10,
                  letterSpacing: ".16em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                }}
              >
                {`Scene ${index + 1}`}
              </button>
            ))}
          </div>
        </div>

        <div
          style={{
            padding: "24px 22px",
            border: `1px solid rgba(${theme.rgb},.18)`,
            background: "rgba(8,6,5,.74)",
            backdropFilter: "blur(10px)",
            boxShadow: `0 24px 80px rgba(0,0,0,.38)`,
            maxHeight: "70vh",
            overflowY: "auto",
            scrollbarWidth: "thin",
          }}
        >
          <div
            style={{
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 200,
              fontSize: 10,
              letterSpacing: ".42em",
              color: `rgba(${theme.rgb},.46)`,
              textTransform: "uppercase",
              marginBottom: 10,
            }}
          >
            Continue The Chronicle
          </div>
          <p
            style={{
              fontFamily: "'EB Garamond',serif",
              fontStyle: "italic",
              fontSize: 17,
              lineHeight: 1.68,
              color: "rgba(242,234,214,.8)",
              marginBottom: 18,
            }}
          >
            {prompt}
          </p>

          {!isFinal && (
            <button
              onClick={onContinue}
              style={{
                width: "100%",
                background: theme.accent,
                border: "none",
                color: "#060504",
                padding: "14px 20px",
                fontFamily: "'Cinzel',serif",
                fontSize: 10,
                letterSpacing: ".32em",
                textTransform: "uppercase",
                cursor: "pointer",
                marginBottom: 20,
              }}
            >
              Move To Next Act
            </button>
          )}

          <div
            style={{
              width: "100%",
              height: 1,
              background: `rgba(${theme.rgb},.16)`,
              margin: "4px 0 18px",
            }}
          />

          <div
            style={{
              fontFamily: "'Raleway',sans-serif",
              fontWeight: 200,
              fontSize: 10,
              letterSpacing: ".42em",
              color: `rgba(${theme.rgb},.46)`,
              textTransform: "uppercase",
              marginBottom: 10,
            }}
          >
            Ask About This Act
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "136px minmax(0, 1fr)",
              gap: 18,
              alignItems: "center",
              padding: "14px 14px 18px",
              border: `1px solid rgba(${theme.rgb},.12)`,
              background: "rgba(255,255,255,.015)",
              marginBottom: 18,
            }}
          >
            <div style={{ display: "flex", justifyContent: "center" }}>
              <Orb
                size="medium"
                listening={listening}
                onClick={asking ? undefined : onVoiceAsk}
                label={listening ? "Listening" : "Ask Aloud"}
                accent={theme.accent}
              />
            </div>
            <div>
              <div
                style={{
                  fontFamily: "'EB Garamond',serif",
                  fontStyle: "italic",
                  fontSize: 18,
                  lineHeight: 1.65,
                  color: "rgba(242,234,214,.84)",
                  marginBottom: 8,
                  maxHeight: 112,
                  overflowY: "auto",
                  scrollbarWidth: "thin",
                }}
              >
                {latestLine || "Use the orb to ask Gemini Live about the current act before moving on."}
              </div>
              <div
                style={{
                  fontFamily: "'Raleway',sans-serif",
                  fontWeight: 300,
                  fontSize: 10,
                  letterSpacing: ".24em",
                  color: `rgba(${theme.rgb},.44)`,
                  textTransform: "uppercase",
                }}
              >
                {liveConnected ? "Live voice ready for follow-up" : "Reconnecting live voice"}
              </div>
            </div>
          </div>
          <AskPanel theme={theme} value={questionDraft} onChange={setQuestionDraft} onSubmit={onAsk} disabled={asking} />

          {qaEntries.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 18 }}>
              {qaEntries.slice(-3).map((entry, index) => (
                <div key={`${entry.question}-${index}`} style={{ borderTop: `1px solid rgba(${theme.rgb},.12)`, paddingTop: 14 }}>
                  <div
                    style={{
                      fontFamily: "'Cinzel',serif",
                      fontSize: 11,
                      letterSpacing: ".14em",
                      color: "#f2ead6",
                      marginBottom: 8,
                    }}
                  >
                    {entry.question}
                  </div>
                  <div
                    style={{
                      fontFamily: "'EB Garamond',serif",
                      fontStyle: "italic",
                      fontSize: 16,
                      lineHeight: 1.65,
                      color: entry.pending ? "rgba(242,234,214,.46)" : "rgba(242,234,214,.82)",
                    }}
                  >
                    {entry.pending ? "Thinking through the consequences..." : entry.answer}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function CompleteScreen({ onRestart }: { onRestart: () => void }) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#060504",
        animation: "fade-in 1.5s ease",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>
        <div
          style={{
            fontFamily: "'Raleway',sans-serif",
            fontWeight: 200,
            fontSize: 10,
            letterSpacing: ".5em",
            color: "rgba(196,120,38,.36)",
            textTransform: "uppercase",
            animation: "fade-up 1s ease .6s both",
          }}
        >
          The Story Is Told
        </div>
        <div
          style={{
            fontFamily: "'Cinzel',serif",
            fontWeight: 600,
            fontSize: "clamp(44px,7vw,80px)",
            letterSpacing: ".18em",
            color: "#f2ead6",
            animation: "fade-up 1s ease .9s both, flicker 8s ease-in-out 2s infinite",
            textShadow: "0 0 60px rgba(196,120,26,.08)",
          }}
        >
          WHATIFY
        </div>
        <div
          style={{
            width: 64,
            height: 1,
            background: "linear-gradient(90deg,transparent,rgba(196,120,38,.36),transparent)",
            animation: "fade-in 1s ease 1.2s both",
          }}
        />
        <div
          style={{
            fontFamily: "'EB Garamond',serif",
            fontStyle: "italic",
            fontSize: "clamp(14px,1.8vw,19px)",
            lineHeight: 1.82,
            color: "rgba(242,234,214,.35)",
            maxWidth: 400,
            textAlign: "center",
            animation: "fade-up 1s ease 1.4s both",
          }}
        >
          “The past is never dead.
          <br />
          It&apos;s not even past.”
        </div>
        <div
          style={{
            fontFamily: "'Raleway',sans-serif",
            fontWeight: 300,
            fontSize: 11,
            color: "rgba(242,234,214,.18)",
            letterSpacing: ".15em",
            animation: "fade-up 1s ease 1.7s both",
          }}
        >
          — William Faulkner
        </div>
        <div style={{ marginTop: 8, animation: "fade-up 1s ease 2.1s both" }}>
          <button
            onClick={onRestart}
            style={{
              background: "none",
              border: "1px solid rgba(196,120,38,.24)",
              color: "rgba(196,120,38,.52)",
              padding: "13px 36px",
              fontFamily: "'Cinzel',serif",
              fontSize: 10,
              letterSpacing: ".35em",
              cursor: "pointer",
              textTransform: "uppercase",
              transition: "all .3s ease",
            }}
          >
            Ask Another
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CinematicPage() {
  const [phase, setPhase] = useState<UiPhase>("intro");
  const [sessionBooting, setSessionBooting] = useState(false);
  const [session, setSession] = useState<SessionStart | null>(null);
  const [sessionState, setSessionState] = useState<SessionState | null>(null);
  const [scene, setScene] = useState<SceneState>(emptySceneState);
  const [actRevealState, setActRevealState] = useState<ActRevealState | null>(null);
  const [revealStage, setRevealStage] = useState(0);
  const [captions, setCaptions] = useState<string[]>([]);
  const [storyboardAssetsByBeat, setStoryboardAssetsByBeat] = useState<Record<string, Record<string, string>>>({});
  const [storyboardExpectedByBeat, setStoryboardExpectedByBeat] = useState<Record<string, number>>({});
  const [heroVideoByBeat, setHeroVideoByBeat] = useState<Record<string, string>>({});
  const [interleavedRunsByBeat, setInterleavedRunsByBeat] = useState<Record<string, InterleavedRun>>({});
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveOutputState, setLiveOutputState] = useState<TranscriptState>(emptyTranscriptState);
  const [liveInputState, setLiveInputState] = useState<TranscriptState>(emptyTranscriptState);
  const [liveMicOn, setLiveMicOn] = useState(false);
  const [capturedPrompt, setCapturedPrompt] = useState("");
  const [onboardingHandoff, setOnboardingHandoff] = useState(false);
  const [aiSpeaking, setAiSpeaking] = useState(false);
  const [narrationStarted, setNarrationStarted] = useState(false);
  const [sceneTurnComplete, setSceneTurnComplete] = useState(false);
  const [momentIndex, setMomentIndex] = useState(0);
  const [momentProgress, setMomentProgress] = useState(0);
  const [summaryPrompt, setSummaryPrompt] = useState(DEFAULT_SUMMARY_PROMPT);
  const [questionDraft, setQuestionDraft] = useState("");
  const [qaEntries, setQaEntries] = useState<QuestionEntry[]>([]);

  const actionsSocket = useRef<WebSocket | null>(null);
  const captionsSocket = useRef<WebSocket | null>(null);
  const liveSocket = useRef<WebSocket | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const micSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcRef = useRef<ScriptProcessorNode | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const playGainRef = useRef<GainNode | null>(null);
  const playSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const playCursorRef = useRef(0);
  const playPendingChunksRef = useRef(0);
  const revealTimerRef = useRef<number | null>(null);
  const narrationRetryTimerRef = useRef<number | null>(null);
  const onboardingIntroRetryTimerRef = useRef<number | null>(null);
  const onboardingSilenceTimerRef = useRef<number | null>(null);
  const onboardingProcessingTimerRef = useRef<number | null>(null);
  const pendingRevealRef = useRef<ActRevealState | null>(null);
  const questionPendingRef = useRef(false);
  const pendingNarrationRef = useRef<SceneState | null>(null);
  const beginInFlightRef = useRef(false);
  const beginFromPromptRef = useRef<((prompt: string) => Promise<void>) | null>(null);
  const continueStoryRef = useRef<(() => Promise<void>) | null>(null);
  const replayMomentRef = useRef<((startIndex?: number) => Promise<void>) | null>(null);
  const disconnectLiveRef = useRef<(() => Promise<void>) | null>(null);
  const launchActRevealRef = useRef<(nextReveal: ActRevealState) => void>(() => {});
  const snapshotLastRef = useRef(0);
  const phaseRef = useRef<UiPhase>(phase);
  const liveMicOnRef = useRef(liveMicOn);
  const liveStageRef = useRef<string | null>(null);
  const liveUserIdRef = useRef<string | null>(null);
  const narrationStartedRef = useRef(false);
  const actingResponseReceivedRef = useRef(false);
  const pendingActingOutputRef = useRef<TranscriptState>(emptyTranscriptState());
  const pendingActingOutputFinalRef = useRef(false);
  const pendingActingTurnCompleteRef = useRef(false);
  const onboardingGreetingSentRef = useRef(false);
  const onboardingIntroRetryCountRef = useRef(0);
  const onboardingIntroPendingRef = useRef(false);
  const onboardingSpeechDetectedRef = useRef(false);
  const onboardingHandoffRef = useRef(false);
  const onboardingRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const onboardingRecognitionActiveRef = useRef(false);
  const onboardingRecognitionFinalRef = useRef("");
  const onboardingRecognitionTextRef = useRef("");
  const onboardingRecognitionSubmittedRef = useRef(false);
  const preloadedImageSrcsRef = useRef<Set<string>>(new Set());

  phaseRef.current = phase;
  liveMicOnRef.current = liveMicOn;
  narrationStartedRef.current = narrationStarted;
  onboardingHandoffRef.current = onboardingHandoff;

  const currentTheme = actTheme(actRevealState?.beatIndex ?? sessionState?.beat_index ?? 1);
  const liveOutputLines = useMemo(() => transcriptLines(liveOutputState), [liveOutputState]);
  const liveInputLines = useMemo(() => transcriptLines(liveInputState), [liveInputState]);
  const lastAiLine = liveOutputLines[liveOutputLines.length - 1] ?? "";
  const lastUserLine = liveInputLines[liveInputLines.length - 1] ?? "";
  const currentNarrationText = useMemo(
    () => liveOutputLines.join(" ").replace(/\s+/g, " ").trim(),
    [liveOutputLines],
  );
  const currentBeatId = actRevealState?.beatId || scene.beatId || sessionState?.beat_id || "";
  const currentInterleavedRun = interleavedRunsByBeat[currentBeatId] ?? null;
  const heroVideoUri = heroVideoByBeat[currentBeatId] ?? null;
  const currentStoryboardAssets = useMemo(
    () => storyboardAssetsByBeat[currentBeatId] ?? {},
    [currentBeatId, storyboardAssetsByBeat],
  );

  const moments = useMemo(
    () => buildMoments(currentInterleavedRun, currentStoryboardAssets, scene, currentTheme),
    [currentInterleavedRun, currentStoryboardAssets, currentTheme, scene],
  );

  const activeMoment = moments[Math.min(momentIndex, Math.max(moments.length - 1, 0))];
  const summaryLatestLine = useMemo(() => followupExcerpt(lastAiLine), [lastAiLine]);

  const ensurePlayCtx = useCallback(async (): Promise<AudioContext> => {
    let context = playCtxRef.current;
    if (context?.state === "closed") {
      playCtxRef.current = null;
      playGainRef.current = null;
      context = null;
    }
    if (!context) {
      context = new AudioContext();
      const gain = context.createGain();
      gain.gain.value = 1;
      gain.connect(context.destination);
      playCtxRef.current = context;
      playGainRef.current = gain;
    }
    if (context.state === "suspended") {
      try {
        await context.resume();
      } catch {
        // Resume can fail when the browser blocks autoplay; keep the context for the next gesture.
      }
    }
    playCursorRef.current = Math.max(playCursorRef.current, context.currentTime);
    return context;
  }, []);

  const resetPlayback = useCallback(async () => {
    playSourcesRef.current.forEach((source) => {
      source.onended = null;
      try {
        source.stop(0);
      } catch {
        // Ignore stop calls on sources that have already finished.
      }
    });
    playSourcesRef.current.clear();
    playPendingChunksRef.current = 0;
    playCursorRef.current = playCtxRef.current?.currentTime ?? 0;
    setAiSpeaking(false);
  }, []);

  const ensureMomentImageReady = useCallback(async (src: string | null): Promise<boolean> => {
    if (!src) return true;
    if (preloadedImageSrcsRef.current.has(src)) return true;

    return await new Promise<boolean>((resolve) => {
      const image = new Image();
      let settled = false;
      const settle = (ready: boolean) => {
        if (settled) return;
        settled = true;
        if (ready) preloadedImageSrcsRef.current.add(src);
        resolve(ready);
      };

      image.onload = () => settle(true);
      image.onerror = () => settle(false);
      image.src = src;
      if (image.complete) settle(true);
      window.setTimeout(() => settle(false), 7000);
    });
  }, []);

  const playPcmChunk = useCallback(async (b64: string, mimeType: string) => {
    const sampleRate = parseSampleRate(mimeType);
    const bytes = decodeBase64(b64);
    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    const float32 = new Float32Array(int16.length);
    for (let index = 0; index < int16.length; index += 1) float32[index] = int16[index] / 32768;

    const context = await ensurePlayCtx();
    const gain = playGainRef.current;
    const buffer = context.createBuffer(1, float32.length, sampleRate);
    buffer.copyToChannel(float32, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    if (gain) source.connect(gain);
    else source.connect(context.destination);
    const startAt = Math.max(context.currentTime, playCursorRef.current);
    source.start(startAt);
    playSourcesRef.current.add(source);
    playCursorRef.current = startAt + buffer.duration;
    playPendingChunksRef.current += 1;
    setAiSpeaking(true);
    source.onended = () => {
      playSourcesRef.current.delete(source);
      playPendingChunksRef.current = Math.max(0, playPendingChunksRef.current - 1);
      if (playPendingChunksRef.current === 0) setAiSpeaking(false);
    };
  }, [ensurePlayCtx]);

  const refreshState = useCallback(async (sessionId: string) => {
    const response = await fetch(`${API_BASE}/api/v1/session/${sessionId}/state`);
    if (response.ok) setSessionState((await response.json()) as SessionState);
  }, []);

  const hydrateBeatVisuals = useCallback(async (sessionId: string, beatId: string) => {
    if (!beatId.trim()) return;
    try {
      const response = await fetch(`${API_BASE}/api/v1/session/${sessionId}/visual-state?beat_id=${encodeURIComponent(beatId)}`);
      if (!response.ok) return;
      const payload = (await response.json()) as VisualStateResponse;
      const interleavedRun = payload.interleaved_run;
      if (interleavedRun) {
        setInterleavedRunsByBeat((previous) => ({
          ...previous,
          [beatId]: {
            ...interleavedRun,
            final: true,
            blocks: [...interleavedRun.blocks].sort((left, right) => left.part_order - right.part_order),
          },
        }));
      }
      if (payload.storyboard_frames.length > 0) {
        setStoryboardExpectedByBeat((previous) => ({
          ...previous,
          [beatId]: Math.max(previous[beatId] ?? 0, payload.storyboard_frames.length),
        }));
        setStoryboardAssetsByBeat((previous) => {
          const next = { ...previous };
          const scoped = { ...(next[beatId] ?? {}) };
          payload.storyboard_frames.forEach((frame) => {
            if (frame.uri) scoped[frame.shot_id] = frame.uri;
          });
          next[beatId] = scoped;
          return next;
        });
      }
      if (payload.hero_video_uri) {
        const heroUri = toBrowserUri(payload.hero_video_uri ?? undefined);
        if (heroUri) {
          setHeroVideoByBeat((previous) => ({ ...previous, [beatId]: heroUri }));
        }
      }
    } catch {
      // Visual hydration is best-effort; websocket actions still stream live updates.
    }
  }, []);

  const ackAction = useCallback(async (sessionId: string, actionId: string) => {
    await fetch(`${API_BASE}/api/v1/session/${sessionId}/ack`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_id: actionId }),
    });
  }, []);

  const appendCaption = useCallback((text: string) => {
    const cleaned = text.trim();
    if (!cleaned) return;
    setCaptions((previous) => {
      if (previous[previous.length - 1] === cleaned) return previous;
      return [...previous, cleaned].slice(-32);
    });
  }, []);

  const clearNarrationTimers = useCallback(() => {
    clearTimeoutRef(narrationRetryTimerRef);
  }, []);

  const clearRevealTimers = useCallback(() => {
    clearTimeoutRef(revealTimerRef);
  }, []);

  const clearOnboardingTimers = useCallback(() => {
    clearTimeoutRef(onboardingIntroRetryTimerRef);
    clearTimeoutRef(onboardingSilenceTimerRef);
    clearTimeoutRef(onboardingProcessingTimerRef);
  }, []);

  const resetNarrationPhase = useCallback((options?: { preserveCommittedOutput?: boolean }) => {
    clearNarrationTimers();
    narrationStartedRef.current = false;
    actingResponseReceivedRef.current = false;
    setNarrationStarted(false);
    setSceneTurnComplete(false);
    pendingActingOutputFinalRef.current = false;
    pendingActingTurnCompleteRef.current = false;
    pendingActingOutputRef.current = emptyTranscriptState();
    setLiveOutputState((previous) =>
      options?.preserveCommittedOutput ? { committed: previous.committed, pending: "" } : emptyTranscriptState(),
    );
  }, [clearNarrationTimers]);

  const resetStoryAssets = useCallback(() => {
    setStoryboardAssetsByBeat({});
    setStoryboardExpectedByBeat({});
    setHeroVideoByBeat({});
    setInterleavedRunsByBeat({});
    preloadedImageSrcsRef.current = new Set();
  }, []);

  const resetSessionViewState = useCallback(() => {
    setScene(emptySceneState());
    setActRevealState(null);
    setRevealStage(0);
    setCaptions([]);
    resetStoryAssets();
    setLiveOutputState(emptyTranscriptState());
    setLiveInputState(emptyTranscriptState());
    setCapturedPrompt("");
    setNarrationStarted(false);
    setSceneTurnComplete(false);
    setMomentIndex(0);
    setMomentProgress(0);
    setSummaryPrompt(DEFAULT_SUMMARY_PROMPT);
    setQuestionDraft("");
    setQaEntries([]);
    questionPendingRef.current = false;
    pendingNarrationRef.current = null;
    pendingRevealRef.current = null;
    clearOnboardingTimers();
    setOnboardingHandoff(false);
    onboardingGreetingSentRef.current = false;
    onboardingIntroRetryCountRef.current = 0;
    onboardingIntroPendingRef.current = false;
    onboardingSpeechDetectedRef.current = false;
    onboardingRecognitionFinalRef.current = "";
    onboardingRecognitionTextRef.current = "";
    onboardingRecognitionSubmittedRef.current = false;
  }, [clearOnboardingTimers, resetStoryAssets]);

  const enterActSummary = useCallback((nextPrompt?: string) => {
    if (nextPrompt) setSummaryPrompt(nextPrompt);
    resetNarrationPhase();
    setPhase("actSummary");
  }, [resetNarrationPhase]);

  const stopOnboardingCapture = useCallback((options?: { suppressSubmit?: boolean }) => {
    const recognition = onboardingRecognitionRef.current;
    onboardingRecognitionRef.current = null;
    onboardingRecognitionActiveRef.current = false;
    if (options?.suppressSubmit) onboardingRecognitionSubmittedRef.current = true;
    if (recognition) {
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      if (options?.suppressSubmit) recognition.abort();
      else recognition.stop();
    }
    setLiveMicOn(false);
  }, []);

  const startOnboardingRecognition = useCallback((): boolean => {
    if (phaseRef.current !== "onboarding" || onboardingRecognitionActiveRef.current) return true;
    const Recognition = speechRecognitionCtor();
    if (!Recognition) return false;

    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      onboardingRecognitionActiveRef.current = true;
      debugLog("onboarding:recognition:start");
    };
    recognition.onresult = (event) => {
      let finalText = onboardingRecognitionFinalRef.current;
      let interimText = "";

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0]?.transcript?.trim();
        if (!transcript) continue;
        if (result.isFinal) {
          finalText = mergeTranscriptText(finalText, transcript);
        } else {
          interimText = mergeTranscriptText(interimText, transcript);
        }
      }

      onboardingRecognitionFinalRef.current = finalText;
      const previewText = mergeTranscriptText(finalText, interimText);
      onboardingRecognitionTextRef.current = previewText;
      setLiveInputState({
        committed: finalText ? [finalText] : [],
        pending: finalText === previewText ? "" : previewText,
      });
      setCapturedPrompt(finalText || previewText);
      debugLog("onboarding:recognition:result", { finalLength: finalText.length, previewLength: previewText.length });
    };
    recognition.onerror = () => {
      onboardingRecognitionActiveRef.current = false;
      debugLog("onboarding:recognition:error");
    };
    recognition.onend = () => {
      if (onboardingRecognitionRef.current === recognition) {
        onboardingRecognitionActiveRef.current = false;
      }
      debugLog("onboarding:recognition:end");
    };

    onboardingRecognitionRef.current = recognition;
    try {
      recognition.start();
      return true;
    } catch {
      onboardingRecognitionRef.current = null;
      onboardingRecognitionActiveRef.current = false;
      debugLog("onboarding:recognition:unavailable");
      return false;
    }
  }, []);

  const playOnboardingWelcome = useCallback(async (socket: WebSocket | null, options?: { isRetry?: boolean }) => {
    const liveSocketCurrent = socket;
    if (!liveSocketCurrent || liveSocketCurrent.readyState !== WebSocket.OPEN) return;
    if (!options?.isRetry && onboardingGreetingSentRef.current) return;
    onboardingGreetingSentRef.current = true;
    onboardingIntroPendingRef.current = true;
    setOnboardingHandoff(false);
    setCapturedPrompt("");
    setLiveOutputState(emptyTranscriptState());
    setLiveInputState(emptyTranscriptState());
    liveSocketCurrent.send(JSON.stringify({
      type: "text",
      text: `Speak exactly this line in English, warmly and cinematically, and add nothing else: "${ONBOARDING_WELCOME_SCRIPT}"`,
    }));
    clearTimeoutRef(onboardingIntroRetryTimerRef);
    onboardingIntroRetryTimerRef.current = window.setTimeout(() => {
      onboardingIntroRetryTimerRef.current = null;
      if (
        phaseRef.current !== "onboarding"
        || onboardingHandoffRef.current
        || liveMicOnRef.current
        || aiSpeaking
        || !onboardingIntroPendingRef.current
      ) {
        return;
      }
      if (onboardingIntroRetryCountRef.current >= 1) {
        onboardingIntroPendingRef.current = false;
        setLiveOutputState({ committed: [ONBOARDING_WELCOME_SCRIPT], pending: "" });
        return;
      }
      onboardingIntroRetryCountRef.current += 1;
      void playOnboardingWelcome(liveSocket.current, { isRetry: true });
    }, 2600);
    debugLog(options?.isRetry ? "onboarding:intro:retry" : "onboarding:intro:play");
  }, [aiSpeaking]);

  const stopMic = useCallback(async () => {
    if (onboardingRecognitionActiveRef.current) {
      stopOnboardingCapture({ suppressSubmit: true });
    }
    if (!liveMicOnRef.current && !micCtxRef.current && !micStreamRef.current && !micSourceRef.current && !micProcRef.current) {
      return;
    }
    liveMicOnRef.current = false;
    const socket = liveSocket.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "activity_end" }));
    }
    const processor = micProcRef.current;
    micProcRef.current = null;
    if (processor) {
      processor.disconnect();
      processor.onaudioprocess = null;
    }
    const source = micSourceRef.current;
    micSourceRef.current = null;
    source?.disconnect();
    const stream = micStreamRef.current;
    micStreamRef.current = null;
    stream?.getTracks().forEach((track) => track.stop());
    const context = micCtxRef.current;
    micCtxRef.current = null;
    await closeAudioContext(context);
    setLiveMicOn(false);
    clearTimeoutRef(onboardingSilenceTimerRef);
    debugLog("mic:stop");
  }, [stopOnboardingCapture]);

  const startMic = useCallback(async () => {
    let socket = liveSocket.current;
    if (phaseRef.current === "onboarding") {
      if (aiSpeaking || onboardingHandoffRef.current || liveMicOnRef.current) return;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      clearOnboardingTimers();
      stopOnboardingCapture({ suppressSubmit: true });
      onboardingRecognitionSubmittedRef.current = false;
      onboardingSpeechDetectedRef.current = false;
      onboardingRecognitionFinalRef.current = "";
      onboardingRecognitionTextRef.current = "";
      setLiveInputState(emptyTranscriptState());
      setCapturedPrompt("");
      startOnboardingRecognition();
    }
    if (!socket || socket.readyState !== WebSocket.OPEN || liveMicOnRef.current) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const audioContext = new AudioContext();
    await audioContext.resume();
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    const mute = audioContext.createGain();
    mute.gain.value = 0;
    processor.onaudioprocess = (event) => {
      const currentSocket = liveSocket.current;
      if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) return;
      const samples = event.inputBuffer.getChannelData(0);
      if (phaseRef.current === "onboarding") {
        let energy = 0;
        for (let index = 0; index < samples.length; index += 1) energy += Math.abs(samples[index]);
        const average = energy / samples.length;
        const speaking = average > 0.018;
        if (speaking) {
          onboardingSpeechDetectedRef.current = true;
          clearTimeoutRef(onboardingSilenceTimerRef);
        } else if (
          onboardingSpeechDetectedRef.current
          && onboardingSilenceTimerRef.current === null
          && !onboardingRecognitionSubmittedRef.current
        ) {
          onboardingSilenceTimerRef.current = window.setTimeout(() => {
            onboardingSilenceTimerRef.current = null;
            const finalPrompt = onboardingRecognitionTextRef.current.trim();
            if (!finalPrompt || onboardingRecognitionSubmittedRef.current || phaseRef.current !== "onboarding") return;
            onboardingRecognitionSubmittedRef.current = true;
            void stopMic();
            void beginFromPromptRef.current?.(finalPrompt);
          }, 950);
        }
      }
      const pcm = downsampleTo16k(samples, audioContext.sampleRate);
      currentSocket.send(pcm.buffer);
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(audioContext.destination);
    micStreamRef.current = stream;
    micCtxRef.current = audioContext;
    micSourceRef.current = source;
    micProcRef.current = processor;
    socket.send(JSON.stringify({ type: "activity_start" }));
    setLiveMicOn(true);
    debugLog("mic:start");
  }, [aiSpeaking, clearOnboardingTimers, startOnboardingRecognition, stopMic, stopOnboardingCapture]);

  const handleLiveMessage = useCallback(async (data: string) => {
    let message: LiveMessage;
    try {
      message = JSON.parse(data) as LiveMessage;
    } catch {
      return;
    }

    if (message.type === "adk_event") {
      if (hasNestedFlag(message.event, ["interrupted"])) {
        clearNarrationTimers();
        await resetPlayback();
        resetNarrationPhase({ preserveCommittedOutput: true });
      }
      if (hasNestedFlag(message.event, ["turnComplete", "turn_complete", "generationComplete", "generation_complete"])) {
        if (phaseRef.current === "acting") {
          if (narrationStartedRef.current || playPendingChunksRef.current > 0) {
            setSceneTurnComplete(true);
          } else {
            pendingActingTurnCompleteRef.current = true;
          }
        }
        if (playPendingChunksRef.current === 0) setAiSpeaking(false);
      }
      return;
    }

    if (message.type === "live_status") {
      debugLog("live:status", {
        status: message.status ?? "unknown",
        message: message.message ?? "",
        attempt: message.attempt ?? null,
        retryInMs: message.retry_in_ms ?? null,
      });
      if (message.status === "failed") {
        onboardingIntroPendingRef.current = false;
        setAiSpeaking(false);
      }
      return;
    }

    if (message.type === "output_transcript" && message.text) {
      if (phaseRef.current === "actSummary" && !questionPendingRef.current) {
        debugLog("live:output:ignored", { phase: phaseRef.current, text: message.text.slice(0, 120) });
        return;
      }
      const outputText = sanitizeLiveNarration(message.text, phaseRef.current);
      if (!outputText) {
        debugLog("live:filtered", { phase: phaseRef.current, text: message.text.slice(0, 120) });
        return;
      }
      if (phaseRef.current === "onboarding" && !onboardingHandoffRef.current) {
        onboardingIntroPendingRef.current = false;
        clearTimeoutRef(onboardingIntroRetryTimerRef);
      }
      if (phaseRef.current === "acting") {
        actingResponseReceivedRef.current = true;
        if (message.final) pendingActingOutputFinalRef.current = true;
      }
      if (phaseRef.current === "acting" && !narrationStartedRef.current) {
        pendingActingOutputRef.current = applyTranscriptDelta(
          pendingActingOutputRef.current,
          outputText,
          Boolean(message.final),
        );
        debugLog("live:output:buffered", { final: Boolean(message.final), text: outputText.slice(0, 120) });
        return;
      }
      setLiveOutputState((previous) => applyTranscriptDelta(previous, outputText, Boolean(message.final)));
      if (phaseRef.current === "onboarding" && onboardingHandoffRef.current && message.final) {
        clearTimeoutRef(onboardingProcessingTimerRef);
        onboardingProcessingTimerRef.current = window.setTimeout(() => {
          onboardingProcessingTimerRef.current = null;
          setOnboardingHandoff(false);
          setPhase("processing");
        }, 420);
      }
      if (phaseRef.current === "acting" && message.final) setSceneTurnComplete(true);
      if (phaseRef.current === "actSummary" && questionPendingRef.current) {
        setQaEntries((previous) => resolvePendingAnswer(previous, outputText, !Boolean(message.final)));
        if (message.final) questionPendingRef.current = false;
      }
      debugLog("live:output", { final: Boolean(message.final), text: outputText.slice(0, 120) });
    }

    if (message.type === "input_transcript" && message.text) {
      if (phaseRef.current === "onboarding") {
        const spokenText = message.text.trim();
        if (!spokenText) return;
        const hasBrowserTranscript = onboardingRecognitionTextRef.current.trim().length > 0;
        if (message.final && !hasBrowserTranscript) {
          const finalText = mergeTranscriptText(onboardingRecognitionFinalRef.current, spokenText);
          onboardingRecognitionFinalRef.current = finalText;
          onboardingRecognitionTextRef.current = finalText;
          setLiveInputState({
            committed: finalText ? [finalText] : [],
            pending: "",
          });
          setCapturedPrompt(finalText);
        } else if (!onboardingRecognitionActiveRef.current && !hasBrowserTranscript) {
          const previewText = mergeTranscriptText(onboardingRecognitionFinalRef.current, spokenText);
          onboardingRecognitionTextRef.current = previewText;
          setLiveInputState({
            committed: [],
            pending: previewText,
          });
          setCapturedPrompt(previewText);
        }
        debugLog("onboarding:live-input", { final: Boolean(message.final), text: spokenText.slice(0, 120) });
        if (!message.final || onboardingRecognitionSubmittedRef.current || onboardingRecognitionActiveRef.current || hasBrowserTranscript) return;
        onboardingRecognitionSubmittedRef.current = true;
        await stopMic();
        const finalPrompt = onboardingRecognitionTextRef.current.trim();
        if (finalPrompt) {
          void beginFromPromptRef.current?.(finalPrompt);
        }
        return;
      }
      setLiveInputState((previous) => applyTranscriptDelta(previous, message.text ?? "", Boolean(message.final)));
      debugLog("live:input", { final: Boolean(message.final), text: message.text.slice(0, 120) });
      if (message.final && phaseRef.current === "actSummary") {
        const spokenQuestion = message.text.trim();
        await stopMic();
        const command = parseVoiceCommand(spokenQuestion, Math.max(moments.length, 1));
        if (command?.kind === "continue") {
          await disconnectLiveRef.current?.();
          await continueStoryRef.current?.();
          return;
        }
        if (command?.kind === "replay") {
          await disconnectLiveRef.current?.();
          await replayMomentRef.current?.(command.momentIndex);
          return;
        }
        if (spokenQuestion && !questionPendingRef.current) {
          questionPendingRef.current = true;
          setQaEntries((previous) => [...previous, { question: spokenQuestion, answer: "", pending: true }].slice(-6));
        }
      }
    }

    if (message.type === "audio_chunk" && message.data && message.mime_type) {
      if (phaseRef.current === "onboarding" && !onboardingHandoffRef.current) {
        onboardingIntroPendingRef.current = false;
        clearTimeoutRef(onboardingIntroRetryTimerRef);
      }
      clearNarrationTimers();
      if (phaseRef.current === "acting") {
        actingResponseReceivedRef.current = true;
      }
      if (phaseRef.current === "acting" && !narrationStartedRef.current) {
        narrationStartedRef.current = true;
        setNarrationStarted(true);
        if (pendingActingOutputRef.current.committed.length || pendingActingOutputRef.current.pending) {
          setLiveOutputState(pendingActingOutputRef.current);
          pendingActingOutputRef.current = emptyTranscriptState();
        }
        if (pendingActingOutputFinalRef.current || pendingActingTurnCompleteRef.current) {
          pendingActingOutputFinalRef.current = false;
          pendingActingTurnCompleteRef.current = false;
          setSceneTurnComplete(true);
        }
      }
      await playPcmChunk(message.data, message.mime_type);
    }
  }, [clearNarrationTimers, moments.length, playPcmChunk, resetNarrationPhase, resetPlayback, stopMic]);

  const disconnectLive = useCallback(async () => {
    await stopMic();
    clearNarrationTimers();
    liveSocket.current?.close();
    liveSocket.current = null;
    liveStageRef.current = null;
    setLiveConnected(false);
    await resetPlayback();
  }, [clearNarrationTimers, resetPlayback, stopMic]);

  disconnectLiveRef.current = disconnectLive;

  const connectLive = useCallback(async (
    sessionId: string,
    stage: string,
    options?: { forceReconnect?: boolean },
  ): Promise<WebSocket | null> => {
    const existing = liveSocket.current;
    const sameStage = liveStageRef.current === stage;
    if (!options?.forceReconnect && existing && existing.readyState === WebSocket.OPEN && sameStage) {
      liveStageRef.current = stage;
      return existing;
    }

    if (existing) {
      await disconnectLive();
    }

    return await new Promise<WebSocket | null>((resolve) => {
      let settled = false;
      const settle = (value: WebSocket | null) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const userId = liveUserIdRef.current ?? `cinematic-ui-${sessionId}`;
      const socket = new WebSocket(
        `${WS_BASE}/api/v1/session/${sessionId}/live?user_id=${encodeURIComponent(userId)}`,
      );

      socket.onopen = () => {
        liveSocket.current = socket;
        liveStageRef.current = stage;
        setLiveConnected(true);
        debugLog("live:open", { sessionId, stage });
        settle(socket);
      };

      socket.onmessage = async (event) => {
        await handleLiveMessage(event.data as string);
      };

      socket.onclose = (event) => {
        void stopMic();
        if (liveSocket.current === socket) {
          liveSocket.current = null;
          liveStageRef.current = null;
          setLiveConnected(false);
          setAiSpeaking(false);
        }
        debugLog("live:close", { sessionId, stage, code: event.code, reason: event.reason });
        settle(null);
      };

      socket.onerror = () => {
        if (liveSocket.current === socket) {
          liveSocket.current = null;
          liveStageRef.current = null;
        }
        setLiveConnected(false);
        debugLog("live:error", { sessionId, stage });
        settle(null);
      };
    });
  }, [disconnectLive, handleLiveMessage, stopMic]);

  const ensureActLive = useCallback(async (beatId: string) => {
    const sessionId = session?.session_id;
    if (!sessionId || !beatId.trim()) return null;
    return await connectLive(sessionId, `story-${beatId}`, { forceReconnect: liveStageRef.current !== `story-${beatId}` });
  }, [connectLive, session?.session_id]);

  const buildNarrationSnapshot = useCallback((
    payload: SceneState,
    moment: SceneMoment,
    momentIdx: number,
  ) => ({
    phase: "acting",
    mode: sessionState?.mode ?? "STORY",
    beat_index: actRevealState?.beatIndex ?? sessionState?.beat_index ?? null,
    act_title: payload.title,
    act_time_label: payload.actTimeLabel,
    moment_caption: moment.caption,
    moment_body: moment.body,
    latest_caption: captions[captions.length - 1] ?? "",
    latest_model_line: "",
    latest_user_line: "",
    has_image: Boolean(moment.imageSrc),
    has_video: Boolean(heroVideoByBeat[payload.beatId]),
    story_mode: {
      acting: true,
      summary: false,
      awaiting_continue: false,
    },
    scene_index: momentIdx + 1,
  }), [actRevealState?.beatIndex, captions, heroVideoByBeat, sessionState?.beat_index, sessionState?.mode]);

  const cueNarration = useCallback(async (
    payload: SceneState,
    startIndex = 0,
    attempt = 0,
    options?: { forceReconnect?: boolean },
  ) => {
    if (!payload.beatId.trim()) return;
    const actMoments = buildMoments(
      interleavedRunsByBeat[payload.beatId] ?? null,
      storyboardAssetsByBeat[payload.beatId] ?? {},
      payload,
      actTheme(actRevealState?.beatIndex ?? sessionState?.beat_index ?? 1),
    );
    if (actMoments.length === 0) return;

    const boundedIndex = Math.max(0, Math.min(startIndex, actMoments.length - 1));
    const currentMoment = actMoments[boundedIndex];
    await ensureMomentImageReady(currentMoment.imageSrc);

    const socket = options?.forceReconnect && session?.session_id
      ? await connectLive(session.session_id, `story-${payload.beatId}`, { forceReconnect: true })
      : await ensureActLive(payload.beatId);
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const prompt = sceneNarrationPrompt(payload, currentMoment, boundedIndex, actMoments.length);

    const applyNarrationFallback = (fallbackText: string) => {
      const bufferedOutput = pendingActingOutputRef.current;
      const hasBufferedOutput = bufferedOutput.committed.length > 0 || bufferedOutput.pending.length > 0;
      narrationStartedRef.current = true;
      setNarrationStarted(true);
      setLiveOutputState(
        hasBufferedOutput
          ? bufferedOutput
          : { committed: fallbackText ? [fallbackText] : [], pending: "" },
      );
      pendingActingOutputRef.current = emptyTranscriptState();
      pendingActingOutputFinalRef.current = false;
      pendingActingTurnCompleteRef.current = false;
      setSceneTurnComplete(true);
    };

    clearNarrationTimers();
    await resetPlayback();
    setMomentIndex(boundedIndex);
    setMomentProgress(0);
    resetNarrationPhase();
    socket.send(JSON.stringify({
      type: "scene_snapshot",
      snapshot: buildNarrationSnapshot(payload, currentMoment, boundedIndex),
    }));
    socket.send(JSON.stringify({ type: "text", text: prompt }));
    const armAudioGraceWindow = () => {
      clearNarrationTimers();
      narrationRetryTimerRef.current = window.setTimeout(() => {
        if (narrationStartedRef.current) return;
        if (attempt >= MAX_NARRATION_RECOVERY_ATTEMPTS) {
          debugLog("narration:text-fallback", {
            beatId: payload.beatId,
            momentIndex: boundedIndex,
            attempt,
            reason: "audio-timeout",
          });
          applyNarrationFallback(currentMoment.body);
          return;
        }
        debugLog("narration:audio-retry", {
          beatId: payload.beatId,
          momentIndex: boundedIndex,
          attempt: attempt + 1,
        });
        void cueNarration(payload, boundedIndex, attempt + 1, { forceReconnect: true });
      }, NARRATION_AUDIO_GRACE_TIMEOUT_MS);
    };

    narrationRetryTimerRef.current = window.setTimeout(() => {
      if (narrationStartedRef.current) return;
      if (actingResponseReceivedRef.current) {
        debugLog("narration:await-audio", {
          beatId: payload.beatId,
          momentIndex: boundedIndex,
          attempt,
        });
        armAudioGraceWindow();
        return;
      }
      if (attempt >= MAX_NARRATION_RECOVERY_ATTEMPTS) {
        debugLog("narration:script-fallback", {
          beatId: payload.beatId,
          momentIndex: boundedIndex,
          attempt,
        });
        applyNarrationFallback(currentMoment.body);
        return;
      }
      debugLog("narration:retry", {
        beatId: payload.beatId,
        startIndex: boundedIndex,
        attempt: attempt + 1,
      });
      void cueNarration(payload, boundedIndex, attempt + 1, { forceReconnect: true });
    }, NARRATION_RESPONSE_TIMEOUT_MS);
    debugLog("narration:cue", { beatId: payload.beatId, momentIndex: boundedIndex, attempt });
  }, [actRevealState?.beatIndex, buildNarrationSnapshot, clearNarrationTimers, connectLive, ensureActLive, ensureMomentImageReady, interleavedRunsByBeat, resetNarrationPhase, resetPlayback, session?.session_id, sessionState?.beat_index, storyboardAssetsByBeat]);

  const launchActReveal = useCallback((nextReveal: ActRevealState) => {
    clearRevealTimers();
    resetNarrationPhase();
    setActRevealState(nextReveal);
    setRevealStage(0);
    setCaptions([]);
    setPhase("actReveal");
    window.setTimeout(() => setRevealStage(1), 260);
    window.setTimeout(() => setRevealStage(2), 1620);
    revealTimerRef.current = window.setTimeout(() => {
      revealTimerRef.current = null;
      setPhase("acting");
      if (pendingNarrationRef.current) void cueNarration(pendingNarrationRef.current);
    }, 4200);
  }, [clearRevealTimers, cueNarration, resetNarrationPhase]);

  launchActRevealRef.current = launchActReveal;

  const beginFromPrompt = useCallback(async (prompt: string) => {
    const sessionId = session?.session_id;
    const cleaned = prompt.trim();
    if (!sessionId || !cleaned || beginInFlightRef.current) return;
    beginInFlightRef.current = true;
    await stopMic();
    onboardingIntroPendingRef.current = false;
    setCapturedPrompt(cleaned);
    setLiveInputState({ committed: [cleaned], pending: "" });
    setQuestionDraft("");
    setQaEntries([]);
    questionPendingRef.current = false;
    resetStoryAssets();
    setCaptions([]);
    resetNarrationPhase();
    setOnboardingHandoff(true);
    setLiveOutputState(emptyTranscriptState());
    clearTimeoutRef(onboardingProcessingTimerRef);
    onboardingProcessingTimerRef.current = window.setTimeout(() => {
      onboardingProcessingTimerRef.current = null;
      setOnboardingHandoff(false);
      setPhase("processing");
    }, 3200);

    const liveSocketCurrent = liveSocket.current;
    if (phaseRef.current === "onboarding" && liveSocketCurrent?.readyState === WebSocket.OPEN) {
      liveSocketCurrent.send(JSON.stringify({
        type: "text",
        text: `In two short cinematic sentences, acknowledge this alternate-history divergence and say the chronicle is opening now: ${cleaned}`,
      }));
    } else {
      setOnboardingHandoff(false);
      setPhase("processing");
    }
    try {
      const response = await fetch(`${API_BASE}/api/v1/session/${sessionId}/begin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ divergence_point: cleaned, tone: "cinematic", pacing: "normal" }),
      });
      if (!response.ok) {
        clearOnboardingTimers();
        setOnboardingHandoff(false);
        setPhase("onboarding");
        return;
      }
      await refreshState(sessionId);
    } finally {
      beginInFlightRef.current = false;
    }
  }, [clearOnboardingTimers, refreshState, resetNarrationPhase, resetStoryAssets, session?.session_id, stopMic]);

  beginFromPromptRef.current = beginFromPrompt;

  const startExperience = useCallback(async () => {
    if (sessionBooting) return;
    setSessionBooting(true);
    try {
      await ensurePlayCtx();
    } catch {
      // Ignore warmup failures and create the context on demand later.
    }

    try {
      const response = await fetch(`${API_BASE}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ divergence_point: null, tone: "cinematic", pacing: "normal", auto_run: false }),
      });
      if (!response.ok) {
        setPhase("intro");
        return;
      }
      const payload = (await response.json()) as SessionStart;
      setSession(payload);
      liveUserIdRef.current = `cinematic-ui-${payload.session_id}`;
      setSessionState(null);
      resetSessionViewState();
      await refreshState(payload.session_id);
      setPhase("onboarding");
      const socket = await connectLive(payload.session_id, "onboarding", { forceReconnect: true });
      playOnboardingWelcome(socket);
    } catch {
      setPhase("intro");
    } finally {
      setSessionBooting(false);
    }
  }, [connectLive, ensurePlayCtx, playOnboardingWelcome, refreshState, resetSessionViewState, sessionBooting]);

  const continueStory = useCallback(async () => {
    const sessionId = session?.session_id;
    if (!sessionId) return;
    setPhase("processing");
    resetNarrationPhase();
    const response = await fetch(`${API_BASE}/api/v1/session/${sessionId}/continue`, { method: "POST" });
    if (!response.ok) {
      setPhase("actSummary");
      return;
    }
    await refreshState(sessionId);
  }, [refreshState, resetNarrationPhase, session?.session_id]);

  continueStoryRef.current = continueStory;

  const replayMoment = useCallback(async (startIndex = 0) => {
    const currentScene = pendingNarrationRef.current ?? scene;
    if (!currentScene.beatId.trim()) return;
    const boundedIndex = Math.max(0, Math.min(startIndex, Math.max(moments.length - 1, 0)));
    pendingNarrationRef.current = currentScene;
    setMomentIndex(boundedIndex);
    setMomentProgress(0);
    setNarrationStarted(false);
    setSceneTurnComplete(false);
    setPhase("acting");
    await cueNarration(currentScene, boundedIndex);
  }, [cueNarration, moments.length, scene]);

  replayMomentRef.current = replayMoment;

  const askAboutAct = useCallback(async () => {
    const question = questionDraft.trim();
    const beatId = actRevealState?.beatId ?? scene.beatId;
    if (!question || !beatId) return;
    questionPendingRef.current = true;
    setQaEntries((previous) => [...previous, { question, answer: "", pending: true }].slice(-6));
    setQuestionDraft("");
    const socket = await ensureActLive(beatId);
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({
        type: "text",
        text: `Answer this question about the current act only: ${question}`,
      }));
      return;
    }

    const sessionId = session?.session_id;
    if (!sessionId) return;
    await fetch(`${API_BASE}/api/v1/session/${sessionId}/interrupt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "WHY", question }),
    });
    await refreshState(sessionId);
  }, [actRevealState?.beatId, ensureActLive, questionDraft, refreshState, scene.beatId, session?.session_id]);

  const askAboutActByVoice = useCallback(async () => {
    const beatId = actRevealState?.beatId ?? scene.beatId;
    if (!beatId) return;
    const socket = await ensureActLive(beatId);
    if (!socket) return;
    await startMic();
  }, [actRevealState?.beatId, ensureActLive, scene.beatId, startMic]);

  const resetToIntro = useCallback(async () => {
    await disconnectLive();
    actionsSocket.current?.close();
    captionsSocket.current?.close();
    liveUserIdRef.current = null;
    setSession(null);
    setSessionState(null);
    setPhase("intro");
    clearRevealTimers();
    resetSessionViewState();
  }, [clearRevealTimers, disconnectLive, resetSessionViewState]);

  useEffect(() => {
    moments.forEach((moment) => {
      if (moment.imageSrc) {
        void ensureMomentImageReady(moment.imageSrc);
      }
    });
  }, [ensureMomentImageReady, moments]);

  useEffect(() => {
    if (phase !== "acting" || moments.length === 0 || !narrationStarted) return undefined;
    setMomentProgress(0);
    const duration = momentDurationMs(activeMoment, currentNarrationText);
    const start = Date.now();
    const interval = window.setInterval(() => {
      const progress = Math.min((Date.now() - start) / duration, 1);
      setMomentProgress(progress);
      if (progress >= 1) {
        window.clearInterval(interval);
        setMomentProgress(1);
      }
    }, 55);
    return () => window.clearInterval(interval);
  }, [activeMoment, currentNarrationText, moments.length, narrationStarted, phase]);

  useEffect(() => {
    setMomentIndex(0);
    setMomentProgress(0);
  }, [actRevealState?.beatId]);

  useEffect(() => {
    if (phase !== "acting") return undefined;
    if (!narrationStarted || !sceneTurnComplete) return undefined;
    if (aiSpeaking || momentProgress < 0.96) return undefined;

    const timer = window.setTimeout(() => {
      if (momentIndex < moments.length - 1 && pendingNarrationRef.current) {
        void cueNarration(pendingNarrationRef.current, momentIndex + 1);
        return;
      }
      enterActSummary();
    }, heroVideoUri ? 700 : 950);
    return () => window.clearTimeout(timer);
  }, [aiSpeaking, cueNarration, enterActSummary, heroVideoUri, momentIndex, momentProgress, moments.length, narrationStarted, phase, sceneTurnComplete]);

  useEffect(() => {
    const pendingReveal = pendingRevealRef.current;
    if (!pendingReveal) return;
    const pendingScene = pendingNarrationRef.current;
    if (!pendingScene || pendingScene.beatId !== pendingReveal.beatId) return;

    const pendingRun = interleavedRunsByBeat[pendingReveal.beatId] ?? null;
    const pendingStoryboard = storyboardAssetsByBeat[pendingReveal.beatId] ?? {};
    const storyboardExpectedCount = storyboardExpectedByBeat[pendingReveal.beatId] ?? 0;

    const pendingMoments = buildMoments(
      pendingRun,
      pendingStoryboard,
      pendingScene,
      actTheme(pendingReveal.beatIndex),
    );
    const expectedImageCount = expectedActImageCount(
      pendingRun,
      pendingStoryboard,
      storyboardExpectedCount,
      pendingMoments,
    );
    if (!isActReadyForPlayback(pendingMoments, expectedImageCount)) return;

    let cancelled = false;
    void (async () => {
      await ensureMomentImageReady(pendingMoments[0]?.imageSrc ?? null);
      if (cancelled || pendingRevealRef.current?.beatId !== pendingReveal.beatId) return;
      pendingRevealRef.current = null;
      launchActRevealRef.current(pendingReveal);
    })();

    return () => {
      cancelled = true;
    };
  }, [actRevealState?.beatId, ensureMomentImageReady, interleavedRunsByBeat, scene.beatId, storyboardAssetsByBeat, storyboardExpectedByBeat]);

  useEffect(() => {
    const sessionId = session?.session_id;
    if (!sessionId || !currentBeatId) return;
    void hydrateBeatVisuals(sessionId, currentBeatId);
  }, [currentBeatId, hydrateBeatVisuals, session?.session_id]);

  useEffect(() => {
    if (phase !== "actSummary" || questionPendingRef.current) return;
    setLiveOutputState(emptyTranscriptState());
  }, [phase]);

  const buildSnapshot = useCallback(() => ({
    phase,
    mode: sessionState?.mode ?? null,
    beat_index: sessionState?.beat_index ?? null,
    act_title: actRevealState?.actTitle ?? scene.title,
    act_time_label: actRevealState?.actTimeLabel ?? scene.actTimeLabel,
    moment_caption: activeMoment?.caption ?? "",
    moment_body: activeMoment?.body ?? "",
    latest_caption: captions[captions.length - 1] ?? "",
    latest_model_line: liveOutputLines[liveOutputLines.length - 1] ?? "",
    latest_user_line: liveInputLines[liveInputLines.length - 1] ?? "",
    has_image: Boolean(activeMoment?.imageSrc),
    has_video: Boolean(heroVideoUri),
    story_mode: {
      acting: phase === "acting",
      summary: phase === "actSummary",
      awaiting_continue: phase === "actSummary",
    },
  }), [activeMoment?.body, activeMoment?.caption, activeMoment?.imageSrc, actRevealState?.actTimeLabel, actRevealState?.actTitle, captions, heroVideoUri, liveInputLines, liveOutputLines, phase, scene.actTimeLabel, scene.title, sessionState?.beat_index, sessionState?.mode]);

  const sendSnapshot = useCallback((force = false) => {
    const socket = liveSocket.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    if (!force && now - snapshotLastRef.current < 1500) return;
    snapshotLastRef.current = now;
    socket.send(JSON.stringify({ type: "scene_snapshot", snapshot: buildSnapshot() }));
  }, [buildSnapshot]);

  useEffect(() => {
    sendSnapshot(false);
  }, [sendSnapshot]);

  useEffect(() => {
    if (!liveConnected) return undefined;
    const timer = window.setInterval(() => sendSnapshot(true), 1500);
    return () => window.clearInterval(timer);
  }, [liveConnected, sendSnapshot]);

  const handleAction = useCallback(async (sessionId: string, action: Action) => {
    debugLog("action:recv", { type: action.type, actionId: action.action_id });

    switch (action.type) {
      case "SET_SCENE": {
        const nextScene = sceneFromPayload(action.payload);
        setScene(nextScene);
        pendingNarrationRef.current = nextScene;
        break;
      }
      case "SHOW_ACT_REVEAL": {
        const nextReveal = actRevealFromPayload(action.payload);
        questionPendingRef.current = false;
        setQaEntries([]);
        setSummaryPrompt(summaryPromptForAct(nextReveal));
        clearOnboardingTimers();
        setOnboardingHandoff(false);
        pendingRevealRef.current = nextReveal;
        clearRevealTimers();
        setActRevealState(nextReveal);
        setPhase("processing");
        setSceneTurnComplete(false);
        break;
      }
      case "SHOW_STORYBOARD": {
        const beatId = (action.payload.beat_id as string) ?? "";
        const frames = (action.payload.frames as Array<{ shot_id: string; uri?: string | null }>) ?? [];
        if (!beatId) break;
        setStoryboardExpectedByBeat((previous) => ({
          ...previous,
          [beatId]: Math.max(previous[beatId] ?? 0, frames.length),
        }));
        setStoryboardAssetsByBeat((previous) => {
          const next = { ...previous };
          const scoped = { ...(next[beatId] ?? {}) };
          frames.forEach((frame) => {
            if (frame.uri) scoped[frame.shot_id] = frame.uri;
          });
          next[beatId] = scoped;
          return next;
        });
        break;
      }
      case "PLAY_VIDEO": {
        const beatId = (action.payload.beat_id as string) ?? "";
        const uri = toBrowserUri(action.payload.uri as string);
        if (uri && beatId) {
          setHeroVideoByBeat((previous) => ({ ...previous, [beatId]: uri }));
        }
        break;
      }
      case "SHOW_INTERLEAVED_BLOCKS": {
        const beatId = (action.payload.beat_id as string) ?? "";
        if (!beatId) break;

        const runId = (action.payload.run_id as string) ?? "run_unknown";
        const runBlocks = (action.payload.blocks as InterleavedBlock[]) ?? [];
        const trigger = (action.payload.trigger as string) ?? "BEAT_START";
        const modelId = (action.payload.model_id as string) ?? "";
        const requestId = (action.payload.request_id as string) ?? "";
        const final = Boolean(action.payload.final);

        setInterleavedRunsByBeat((previous) => {
          const existing = previous[beatId];
          if (existing && existing.run_id !== runId) {
            return {
              ...previous,
              [beatId]: {
                run_id: runId,
                beat_id: beatId,
                trigger,
                model_id: modelId,
                request_id: requestId,
                final,
                blocks: [...runBlocks].sort((left, right) => left.part_order - right.part_order),
              },
            };
          }

          return {
            ...previous,
            [beatId]: {
              run_id: runId,
              beat_id: beatId,
              trigger,
              model_id: modelId,
              request_id: requestId,
              final: final || Boolean(existing?.final),
              blocks: mergeInterleavedBlocks(existing?.blocks, runBlocks),
            },
          };
        });
        break;
      }
      case "SHOW_INTERMISSION": {
        enterActSummary((action.payload.prompt as string) ?? "Continue to the next act?");
        break;
      }
      case "CAPTION_APPEND": {
        const text = ((action.payload.text as string) ?? "").trim();
        if (!text) break;
        appendCaption(text);
        if (questionPendingRef.current) {
          questionPendingRef.current = false;
          setQaEntries((previous) => resolvePendingAnswer(previous, text, false));
        }
        break;
      }
      case "SET_MODE": {
        const mode = action.payload.mode as SessionState["mode"] | undefined;
        if (mode === "COMPLETE") setPhase("complete");
        else if (mode === "INTERMISSION") enterActSummary();
        else if (mode === "ONBOARDING") setPhase("onboarding");
        else if (mode === "STORY" && phaseRef.current === "processing" && !pendingRevealRef.current) setPhase("processing");
        if (mode && mode !== "ONBOARDING") {
          clearOnboardingTimers();
          setOnboardingHandoff(false);
        }
        if (mode !== "STORY") {
          setNarrationStarted(false);
          setSceneTurnComplete(false);
        }
        break;
      }
      default:
        break;
    }

    await ackAction(sessionId, action.action_id);
    await refreshState(sessionId);
  }, [ackAction, appendCaption, clearOnboardingTimers, clearRevealTimers, enterActSummary, refreshState]);

  useEffect(() => {
    const sessionId = session?.session_id;
    if (!sessionId) return undefined;

    const actionSocket = new WebSocket(`${WS_BASE}/api/v1/session/${sessionId}/actions`);
    const captionSocket = new WebSocket(`${WS_BASE}/api/v1/session/${sessionId}/captions`);

    actionSocket.onmessage = async (event) => {
      const action = JSON.parse(event.data as string) as Action;
      await handleAction(sessionId, action);
    };

    captionSocket.onmessage = (event) => {
      const payload = JSON.parse(event.data as string) as { text: string };
      if (payload.text?.trim()) appendCaption(payload.text);
    };

    actionsSocket.current = actionSocket;
    captionsSocket.current = captionSocket;

    return () => {
      actionSocket.close();
      captionSocket.close();
    };
  }, [appendCaption, handleAction, session?.session_id]);

  useEffect(() => () => {
    clearRevealTimers();
    clearNarrationTimers();
    clearOnboardingTimers();
    stopOnboardingCapture({ suppressSubmit: true });
    liveMicOnRef.current = false;
    const micProcessor = micProcRef.current;
    micProcRef.current = null;
    if (micProcessor) {
      micProcessor.disconnect();
      micProcessor.onaudioprocess = null;
    }
    const micSource = micSourceRef.current;
    micSourceRef.current = null;
    micSource?.disconnect();
    const micStream = micStreamRef.current;
    micStreamRef.current = null;
    micStream?.getTracks().forEach((track) => track.stop());
    const micContext = micCtxRef.current;
    micCtxRef.current = null;
    void closeAudioContext(micContext);
    liveSocket.current?.close();
    actionsSocket.current?.close();
    captionsSocket.current?.close();
    const playContext = playCtxRef.current;
    playCtxRef.current = null;
    playGainRef.current = null;
    void closeAudioContext(playContext);
  }, [clearNarrationTimers, clearOnboardingTimers, clearRevealTimers, stopOnboardingCapture]);

  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh", background: "#060504", overflow: "hidden", color: "#f2ead6" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;900&family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Raleway:wght@200;300;400;600&display=swap');`}</style>
      <FilmGrain />
      <Letterbox show={phase === "acting" || phase === "actReveal" || phase === "actSummary"} />

      {(phase === "actReveal" || phase === "actSummary") && (
        <div style={{ position: "absolute", inset: 0, opacity: 1, transition: "opacity 1.1s ease" }}>
          {activeMoment?.imageSrc ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={activeMoment.imageSrc} alt="" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", animation: "ken 20s ease-in-out infinite alternate" }} />
          ) : (
            <div style={{ position: "absolute", inset: 0, background: activeMoment?.backdrop ?? currentTheme.ambient }} />
          )}
          <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg,rgba(0,0,0,.55) 0%,rgba(0,0,0,.18) 30%,rgba(0,0,0,.68) 100%)" }} />
          <div style={{ position: "absolute", inset: 0, background: "radial-gradient(ellipse at 50% 50%,transparent 28%,rgba(0,0,0,.82) 100%)" }} />
        </div>
      )}

      {phase === "intro" && <IntroScreen onStart={() => void startExperience()} busy={sessionBooting} />}
      {phase === "onboarding" && (
        <OnboardingScreen
          theme={currentTheme}
          aiLine={lastAiLine}
          onMicStart={() => void startMic()}
          listening={liveMicOn}
          liveConnected={liveConnected}
          introPlaying={aiSpeaking}
          handoffPlaying={onboardingHandoff}
        />
      )}
      {phase === "processing" && <ProcessingScreen theme={currentTheme} />}
      {phase === "actReveal" && actRevealState && <ActReveal act={actRevealState} stage={revealStage} previewMoment={moments[0] ?? null} />}
      {phase === "acting" && actRevealState && (
        <>
          <SceneFilmstrip
            theme={currentTheme}
            act={actRevealState}
            moments={moments}
            momentIndex={momentIndex}
            progress={momentProgress}
            narrationText={currentNarrationText}
          />
          <div style={{ position: "absolute", top: "11vh", left: "4.5vw", zIndex: 30, animation: "fade-up .9s ease .2s both" }}>
            <div
              style={{
                fontFamily: "'Raleway',sans-serif",
                fontWeight: 200,
                fontSize: 9,
                letterSpacing: ".5em",
                color: `rgba(${currentTheme.rgb},.42)`,
                textTransform: "uppercase",
                marginBottom: 5,
              }}
            >
              {`ACT ${actRevealState.beatIndex}`}
            </div>
            <div style={{ fontFamily: "'Cinzel',serif", fontSize: 13, letterSpacing: ".07em", color: "rgba(242,234,214,.58)" }}>
              {actRevealState.actTitle}
            </div>
          </div>
          <div style={{ position: "absolute", top: "10.5vh", right: "4.5vw", zIndex: 30, display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
            <Orb size="small" listening={aiSpeaking} accent={currentTheme.accent} />
            <div
              style={{
                fontFamily: "'Raleway',sans-serif",
                fontWeight: 300,
                fontSize: 8,
                letterSpacing: ".3em",
                color: `rgba(${currentTheme.rgb},.34)`,
                textTransform: "uppercase",
              }}
            >
              Live Narration
            </div>
          </div>
          <ActTimeline current={actRevealState.beatIndex - 1} total={actRevealState.targetBeats} theme={currentTheme} />
        </>
      )}
      {phase === "actSummary" && actRevealState && (
        <>
          <SummaryCard
            theme={currentTheme}
            act={actRevealState}
            moments={moments}
            heroVideoUri={heroVideoUri}
            prompt={summaryPrompt}
            questionDraft={questionDraft}
            setQuestionDraft={setQuestionDraft}
            onAsk={() => void askAboutAct()}
            onVoiceAsk={() => void askAboutActByVoice()}
            onContinue={() => void continueStory()}
            onReplayAct={() => void replayMoment(0)}
            onReplayMoment={(index) => void replayMoment(index)}
            qaEntries={qaEntries}
            asking={questionPendingRef.current}
            listening={liveMicOn}
            liveConnected={liveConnected}
            latestLine={summaryLatestLine}
          />
          <ActTimeline current={actRevealState.beatIndex - 1} total={actRevealState.targetBeats} theme={currentTheme} />
        </>
      )}
      {phase === "complete" && <CompleteScreen onRestart={() => void resetToIntro()} />}
    </div>
  );
}
