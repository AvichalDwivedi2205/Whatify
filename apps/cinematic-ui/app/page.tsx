"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/* ─── Types ───────────────────────────────────────────────── */
type Choice = { choice_id: string; label: string; consequence_hint: string };
type Action = { action_id: string; type: string; payload: Record<string, unknown>; ts: string; retry_count: number };
type SessionStart = { session_id: string; branch_id: string; beat_id: string; stream_token: string };
type SessionState = {
  session_id: string; branch_id: string; beat_id: string; beat_index: number;
  mode: "ONBOARDING" | "STORY" | "CHOICE" | "EXPLAIN" | "INTERMISSION" | "COMPLETE" | "NAV";
  pacing: string; video_budget_remaining: number; pending_actions: number; target_beats: number;
  phase: string; awaiting_continue: boolean;
};
type InterleavedBlock = { part_order: number; kind: "text" | "image"; text?: string; mime_type?: string; uri?: string; inline_data_b64?: string };
type InterleavedRun = { run_id: string; beat_id: string; trigger: string; model_id: string; request_id: string; final: boolean; blocks: InterleavedBlock[] };
type LiveMessage = { type: string; text?: string; final?: boolean; data?: string; mime_type?: string };
type ActRevealState = { actTitle: string; actTimeLabel: string; beatIndex: number; targetBeats: number };
type UiPhase = "intro" | "modeSelect" | "onboarding" | "processing" | "actReveal" | "acting" | "intermission" | "complete";
type InputMode = "voice" | "text" | null;
type TranscriptState = { committed: string[]; pending: string };
type MicCaptureReason = "onboarding" | "interrupt" | null;
type NarrationCue = {
  beat_id?: string;
  title?: string;
  setup?: string;
  escalation?: string;
  act_time_label?: string;
  narration_script?: string;
};

/* ─── Constants ───────────────────────────────────────────── */
const API_BASE = process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ?? "http://localhost:8080";
const WS_BASE  = API_BASE.replace(/^http/i, "ws");

const WAVE_HEIGHTS = [5,12,22,8,18,28,6,20,14,26,10,24,4,16,30,8,22,12,18,6];
const WAVE_DELAYS  = [0,.15,.3,.05,.22,.38,.1,.28,.18,.4,.08,.33,.02,.2,.45,.12,.35,.15,.25,.07];

/* ─── Audio helpers ───────────────────────────────────────── */
function parseSampleRate(mimeType: string): number {
  const m = /rate=(\d+)/i.exec(mimeType);
  if (!m) return 24000;
  const v = parseInt(m[1], 10);
  return Number.isFinite(v) && v > 0 ? v : 24000;
}
function decodeBase64(data: string): Uint8Array {
  const b = atob(data); const o = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) o[i] = b.charCodeAt(i);
  return o;
}
function floatToInt16(input: Float32Array): Int16Array {
  const o = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    o[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return o;
}
function downsampleTo16k(buffer: Float32Array, inputRate: number): Int16Array {
  if (inputRate === 16000) return floatToInt16(buffer);
  const ratio = inputRate / 16000;
  const len   = Math.round(buffer.length / ratio);
  const out   = new Int16Array(len);
  for (let r = 0, b = 0; r < len; r++) {
    const next = Math.round((r + 1) * ratio);
    let acc = 0, cnt = 0;
    for (let i = b; i < next && i < buffer.length; i++) { acc += buffer[i]; cnt++; }
    const s = cnt > 0 ? Math.max(-1, Math.min(1, acc / cnt)) : 0;
    out[r] = s < 0 ? s * 0x8000 : s * 0x7fff;
    b = next;
  }
  return out;
}
function toBrowserUri(uri?: string): string | null {
  if (!uri) return null;
  return uri.startsWith("gs://") ? `https://storage.googleapis.com/${uri.slice(5)}` : uri;
}
function blockImageSource(block: InterleavedBlock | undefined): string | null {
  if (!block || block.kind !== "image") return null;
  const uri = toBrowserUri(block.uri);
  if (uri) return uri;
  if (block.inline_data_b64 && block.mime_type) return `data:${block.mime_type};base64,${block.inline_data_b64}`;
  return null;
}
function applyTranscriptDelta(state: TranscriptState, text: string, isFinal: boolean, maxLines = 60): TranscriptState {
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
    console.info(`[whatify-ui] ${event}`, details);
    return;
  }
  console.info(`[whatify-ui] ${event}`);
}

/* ─── Film Grain ──────────────────────────────────────────── */
function FilmGrain() {
  return (
    <div style={{
      position:"fixed",inset:0,zIndex:999,pointerEvents:"none",opacity:0.055,
      backgroundImage:`url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
      backgroundSize:"180px 180px",animation:"grain-shift .35s steps(1) infinite",
    }}/>
  );
}

/* ─── Letterbox ───────────────────────────────────────────── */
function Letterbox({ show }: { show: boolean }) {
  const bar = (top: boolean) => ({
    position:"fixed" as const, [top?"top":"bottom"]:0, left:0, right:0, height:"9vh",
    background:"#000", zIndex:90,
    transform: show ? "scaleY(1)" : "scaleY(0)",
    transformOrigin: top ? "top" : "bottom",
    transition:"transform .9s cubic-bezier(.4,0,.2,1)",
  });
  return <><div style={bar(true)}/><div style={bar(false)}/></>;
}

/* ─── Cinematic Orb ───────────────────────────────────────── */
function Orb({ size="large", listening=false, onClick, label, style: extra }:
  { size?:"large"|"medium"|"small"; listening?:boolean; onClick?:()=>void; label?:string; style?: React.CSSProperties }) {
  const scale = size==="large" ? 1 : size==="medium" ? 0.65 : 0.35;
  const B = 148 * scale;
  const glow = listening ? 1.8 : 1;
  return (
    <div onClick={onClick} style={{ display:"flex",flexDirection:"column",alignItems:"center",gap:14*scale,cursor:onClick?"pointer":"default",userSelect:"none",...extra }}>
      <div style={{ position:"relative",width:B,height:B,display:"flex",alignItems:"center",justifyContent:"center" }}>
        {listening && [0,.55,1.1].map((delay,i) => (
          <div key={i} style={{ position:"absolute",width:B,height:B,borderRadius:"50%",border:`1px solid rgba(196,120,38,${0.35-i*0.08})`,animation:`ring-out 2.2s ease-out ${delay}s infinite`,pointerEvents:"none" }}/>
        ))}
        <div style={{ position:"absolute",width:B,height:B,borderRadius:"50%",
          background:`radial-gradient(circle at 38% 33%, rgba(200,128,38,${0.16*glow}) 0%, transparent 68%)`,
          boxShadow:`0 0 ${55*scale}px rgba(196,120,26,${0.14*glow}), 0 0 ${110*scale}px rgba(196,120,26,${0.07*glow})`,
          animation:listening?"orb-listen 1.6s ease-in-out infinite":"orb-breathe 4.5s ease-in-out infinite",
        }}/>
        <div style={{ position:"absolute",width:B*0.72,height:B*0.72,borderRadius:"50%",
          background:`radial-gradient(circle at 36% 30%, rgba(215,148,48,${0.28*glow}) 0%, rgba(140,72,16,${0.18*glow}) 50%, transparent 70%)`,
          boxShadow:`0 0 ${28*scale}px rgba(200,110,24,${0.22*glow}), inset 0 0 ${18*scale}px rgba(0,0,0,.45)`,
        }}/>
        <div style={{ position:"absolute",width:B*0.42,height:B*0.42,borderRadius:"50%",
          background:"radial-gradient(circle at 33% 28%, #f2c258 0%, #c87e1c 38%, #6a2e0c 78%, #180a04 100%)",
          boxShadow:`0 0 ${18*scale}px rgba(240,178,72,${0.55*glow}), 0 0 ${36*scale}px rgba(196,120,26,${0.32*glow}), inset -2px -2px ${6*scale}px rgba(0,0,0,.5)`,
          animation:listening?"orb-listen 1.6s ease-in-out infinite":"orb-breathe 4.5s ease-in-out infinite",
        }}/>
      </div>
      {label && <div style={{ fontFamily:"'Cinzel',serif",fontSize:10*scale+1,letterSpacing:"0.38em",color:`rgba(196,128,38,${listening?0.9:0.65})`,textTransform:"uppercase",transition:"color .4s" }}>{label}</div>}
    </div>
  );
}

/* ─── Act Timeline ────────────────────────────────────────── */
function ActTimeline({ current, total }: { current: number; total: number }) {
  return (
    <div style={{ position:"fixed",bottom:"3.2vh",left:"50%",transform:"translateX(-50%)",display:"flex",alignItems:"center",gap:10,zIndex:95 }}>
      {Array.from({length:total}).map((_,i) => (
        <div key={i} style={{
          height:3, borderRadius:2,
          width: i===current ? 28 : 7,
          background: i<current ? "rgba(196,120,38,.65)" : i===current ? "#c4781a" : "rgba(196,120,38,.18)",
          boxShadow: i===current ? "0 0 8px rgba(196,120,26,.6)" : "none",
          transition:"all .5s cubic-bezier(.4,0,.2,1)",
        }}/>
      ))}
    </div>
  );
}

/* ─── Intro Screen ────────────────────────────────────────── */
function IntroScreen({ onStart, busy }: { onStart: () => void; busy: boolean }) {
  return (
    <div style={{ position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"#070604",animation:"fade-in 1.2s ease",gap:0 }}>
      <div style={{ position:"absolute",width:500,height:500,borderRadius:"50%",background:"radial-gradient(circle, rgba(196,120,26,.06) 0%, transparent 70%)",top:"50%",left:"50%",transform:"translate(-50%,-50%)",pointerEvents:"none" }}/>
      <div style={{ display:"flex",flexDirection:"column",alignItems:"center",gap:36 }}>
        <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:300,fontSize:10,letterSpacing:"0.5em",color:"rgba(196,120,38,.45)",textTransform:"uppercase",animation:"fade-up 1s ease .4s both" }}>
          An Alternate History Engine
        </div>
        <div style={{ fontFamily:"'Cinzel',serif",fontWeight:600,fontSize:"clamp(54px,9vw,100px)",letterSpacing:"0.18em",color:"#f2ead6",lineHeight:1,textShadow:"0 0 80px rgba(196,120,26,.08)",animation:"fade-up 1s ease .65s both, flicker 8s ease-in-out 2s infinite" }}>
          WHATIFY
        </div>
        <div style={{ width:72,height:1,background:"linear-gradient(90deg, transparent, rgba(196,120,38,.45), transparent)",animation:"fade-in 1s ease 1s both" }}/>
        <div style={{ animation:"fade-up 1s ease 1.1s both" }}>
          <Orb size="large" listening={busy} onClick={busy ? undefined : onStart} label={busy ? "Opening" : "Begin"}/>
        </div>
        <div style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:15,color:"rgba(242,234,214,.28)",letterSpacing:"0.08em",animation:"fade-in 1s ease 1.8s both" }}>
          {busy ? "Preparing the live chamber" : "Speak your what if"}
        </div>
      </div>
    </div>
  );
}

/* ─── Mode Select Screen ──────────────────────────────────── */
function ModeSelectScreen({ onSelect, aiSpeaking, lastAiLine }: {
  onSelect: (mode: "voice" | "text") => void;
  aiSpeaking: boolean;
  lastAiLine: string;
}) {
  return (
    <div style={{ position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"#070604",animation:"fade-in .6s ease",gap:0 }}>
      {/* Ambient orb always listening/speaking */}
      <div style={{ marginBottom:32 }}>
        <Orb size="medium" listening={aiSpeaking} label={aiSpeaking ? "Speaking" : "Ready"}/>
      </div>

      {/* AI message */}
      <div style={{ minHeight:52,maxWidth:520,textAlign:"center",padding:"0 2rem",marginBottom:44 }}>
        <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:"clamp(16px,2vw,22px)",color:"rgba(242,234,214,.75)",lineHeight:1.7,letterSpacing:".02em",transition:"opacity .4s",opacity:lastAiLine?1:0 }}>
          {lastAiLine || "…"}
        </p>
      </div>

      {/* Mode cards */}
      <div style={{ display:"flex",gap:24,alignItems:"stretch" }}>
        {([
          { mode:"voice" as const, icon:"🎙", title:"Voice", desc:"Speak your question.\nThe AI listens and guides you." },
          { mode:"text"  as const, icon:"✍", title:"Text",  desc:"Type your question.\nThe AI narrates the journey." },
        ]).map(({ mode, icon, title, desc }) => (
          <button key={mode} onClick={() => onSelect(mode)} style={{
            background:"rgba(196,120,38,.06)",border:"1px solid rgba(196,120,38,.22)",borderRadius:16,
            padding:"28px 36px",cursor:"pointer",color:"#f2ead6",textAlign:"center",width:200,
            display:"flex",flexDirection:"column",alignItems:"center",gap:14,
            transition:"all .3s ease",animation:"fade-up .7s ease both",
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background="rgba(196,120,38,.14)"; (e.currentTarget as HTMLButtonElement).style.borderColor="rgba(196,120,38,.55)"; (e.currentTarget as HTMLButtonElement).style.transform="translateY(-4px)"; }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background="rgba(196,120,38,.06)"; (e.currentTarget as HTMLButtonElement).style.borderColor="rgba(196,120,38,.22)"; (e.currentTarget as HTMLButtonElement).style.transform="translateY(0)"; }}>
            <span style={{ fontSize:32 }}>{icon}</span>
            <span style={{ fontFamily:"'Cinzel',serif",fontSize:13,letterSpacing:"0.22em",textTransform:"uppercase" }}>{title}</span>
            <span style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:13,color:"rgba(242,234,214,.5)",lineHeight:1.5,whiteSpace:"pre-line" }}>{desc}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─── Listening / Onboarding Screen ──────────────────────── */
function OnboardingScreen({ transcript, inputMode, textInput, onTextChange, onTextSubmit, aiSpeaking, liveMicOn, onVoiceRetry, lastAiLine }:
  { transcript: string; inputMode: InputMode; textInput: string; onTextChange: (v:string)=>void; onTextSubmit: ()=>void; aiSpeaking: boolean; liveMicOn: boolean; onVoiceRetry: ()=>void; lastAiLine: string }) {
  return (
    <div style={{ position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"#070604",animation:"fade-in .5s ease",gap:0 }}>
      <div style={{ display:"flex",flexDirection:"column",alignItems:"center",gap:36,width:"100%",maxWidth:600,padding:"0 2rem" }}>
        <Orb
          size="large"
          listening={liveMicOn || aiSpeaking}
          onClick={inputMode==="voice" && !liveMicOn ? onVoiceRetry : undefined}
          label={inputMode==="voice" ? (liveMicOn ? "Listening" : aiSpeaking ? "Speaking" : "Ready") : "Ready"}
        />

        {/* Live AI line */}
        {lastAiLine && (
          <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:"clamp(14px,1.8vw,20px)",color:"rgba(242,234,214,.6)",textAlign:"center",lineHeight:1.6,animation:"fade-in .4s ease" }}>
            {lastAiLine}
          </p>
        )}

        {inputMode==="voice" && liveMicOn && (
          <>
            {/* Waveform */}
            <div style={{ display:"flex",alignItems:"center",gap:4,height:36 }}>
              {WAVE_HEIGHTS.map((h,i) => (
                <div key={i} style={{ width:3,height:h,background:`rgba(196,120,38,${0.4+(h/30)*0.5})`,borderRadius:2,transformOrigin:"center",animation:`wave-bar ${0.5+WAVE_DELAYS[i]*1.2}s ease-in-out ${WAVE_DELAYS[i]}s infinite` }}/>
              ))}
            </div>
            {transcript && (
              <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:"clamp(16px,2.2vw,22px)",color:"rgba(242,234,214,.75)",textAlign:"center",lineHeight:1.6,opacity:transcript?1:0,transition:"opacity .3s" }}>
                &ldquo;{transcript}&rdquo;
              </p>
            )}
          </>
        )}

        {inputMode==="voice" && !liveMicOn && (
          <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:14,color:"rgba(242,234,214,.35)",letterSpacing:"0.04em",textAlign:"center" }}>
            {transcript ? "Finalizing your divergence point..." : "Tap the orb to listen again."}
          </p>
        )}

        {inputMode==="text" && (
          <div style={{ width:"100%",display:"flex",flexDirection:"column",gap:12 }}>
            <textarea
              value={textInput}
              onChange={e => onTextChange(e.target.value)}
              placeholder="What if the Library of Alexandria never burned?"
              style={{ width:"100%",background:"rgba(196,120,38,.05)",border:"1px solid rgba(196,120,38,.25)",borderRadius:12,color:"#f2ead6",padding:"16px 20px",fontFamily:"'EB Garamond',serif",fontSize:18,lineHeight:1.6,resize:"none",minHeight:120,outline:"none" }}
            />
            <button
              onClick={onTextSubmit}
              disabled={!textInput.trim()}
              style={{ alignSelf:"flex-end",background:"rgba(196,120,38,.15)",border:"1px solid rgba(196,120,38,.4)",borderRadius:10,color:"rgba(242,234,214,.9)",padding:"12px 32px",fontFamily:"'Cinzel',serif",fontSize:11,letterSpacing:"0.3em",textTransform:"uppercase",cursor:"pointer",opacity:textInput.trim()?1:0.45 }}>
              Begin Story
            </button>
          </div>
        )}

        <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:13,color:"rgba(242,234,214,.22)",letterSpacing:"0.05em" }}>
          {inputMode==="voice" ? (liveMicOn ? "Speak your alternate history question..." : "Voice capture is paused") : "Describe your historical divergence point"}
        </p>
      </div>
    </div>
  );
}

/* ─── Processing Screen ───────────────────────────────────── */
function ProcessingScreen() {
  const steps = ["Analysing historical pivot…","Mapping causal chains…","Generating alternate timeline…","Composing the Acts…","Preparing cinematic visuals…"];
  const [step, setStep] = useState(0);
  const stepsLen = steps.length;
  useEffect(() => { const iv = setInterval(() => setStep(s => Math.min(s+1,stepsLen-1)),520); return ()=>clearInterval(iv); },[stepsLen]);
  return (
    <div style={{ position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"#070604",animation:"fade-in .5s ease",gap:40 }}>
      <div style={{ position:"relative",width:88,height:88 }}>
        {[76,60,44].map((size,i) => (
          <div key={i} style={{ position:"absolute",top:(88-size)/2,left:(88-size)/2,width:size,height:size,borderRadius:"50%",border:`1px solid rgba(196,120,38,${0.18+i*0.1})`,borderTopColor:`rgba(196,120,38,${0.6+i*0.15})`,animation:`${i%2===0?"spin-cw":"spin-ccw"} ${1.8-i*0.3}s linear infinite` }}/>
        ))}
        <div style={{ position:"absolute",top:30,left:30,width:28,height:28,borderRadius:"50%",background:"radial-gradient(circle at 33% 28%, #f2c258 0%, #c87e1c 42%, #180a04 100%)",boxShadow:"0 0 16px rgba(240,180,70,.45)" }}/>
      </div>
      <div style={{ fontFamily:"'Cinzel',serif",fontSize:11,letterSpacing:"0.38em",color:"rgba(196,120,38,.5)",textTransform:"uppercase" }}>Rewriting History</div>
      <div style={{ display:"flex",flexDirection:"column",gap:10,alignItems:"flex-start" }}>
        {steps.map((s,i) => (
          <div key={i} style={{ display:"flex",alignItems:"center",gap:12,fontFamily:"'Raleway',sans-serif",fontWeight:300,fontSize:12,letterSpacing:".05em",color:`rgba(242,234,214,${i<=step?0.65:0.18})`,transition:"all .4s ease" }}>
            <div style={{ width:5,height:5,borderRadius:"50%",background:i<step?"#c4781a":i===step?"#f0c058":"rgba(196,120,38,.2)",boxShadow:i===step?"0 0 8px rgba(240,192,88,.6)":"none",transition:"all .4s",animation:i===step?"pulse-dot 1s ease-in-out infinite":"none" }}/>
            {s}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Act Reveal Overlay ──────────────────────────────────── */
function ActRevealOverlay({ actReveal }: { actReveal: ActRevealState }) {
  return (
    <div style={{ position:"absolute",inset:0,zIndex:80,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"rgba(0,0,0,.92)",animation:"fade-in .7s ease" }}>
      <div style={{ textAlign:"center",animation:"fade-up .7s ease" }}>
        <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:200,fontSize:10,letterSpacing:"0.6em",color:"rgba(196,120,38,.5)",textTransform:"uppercase",marginBottom:18 }}>
          ACT {actReveal.beatIndex}
        </div>
        <div style={{ fontFamily:"'Cinzel',serif",fontWeight:400,fontSize:"clamp(26px,3.8vw,50px)",letterSpacing:"0.09em",color:"#f2ead6",marginBottom:22,animation:"title-mask .8s ease .1s both" }}>
          {actReveal.actTitle}
        </div>
        <div style={{ width:80,height:1,margin:"0 auto 18px",background:"rgba(196,120,38,.45)",animation:"line-sweep .7s ease .3s both" }}/>
        <div style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:14,color:"rgba(242,234,214,.4)",letterSpacing:"0.12em",animation:"fade-in .6s ease .5s both" }}>
          {actReveal.actTimeLabel}
        </div>
      </div>
    </div>
  );
}

/* ─── Narration Text (word-reveal) ────────────────────────── */
function NarrationText({ lines }: { lines: string[] }) {
  const text = lines.slice(-3).join(" ");
  return (
    <p style={{ fontFamily:"'EB Garamond',serif",fontWeight:400,fontSize:"clamp(17px,2.1vw,26px)",lineHeight:1.78,color:"rgba(242,234,212,.9)",textShadow:"0 2px 24px rgba(0,0,0,.85)",maxWidth:"65ch",animation:"fade-up .5s ease" }}>
      {text}
    </p>
  );
}

/* ─── Acting Screen ───────────────────────────────────────── */
function ActingScreen({ actReveal, totalActs, liveOutputLines, stageImageSrc, heroVideoUri, storyboardAssets, onInterrupt, aiSpeaking, userSpeaking, scene }:
  { actReveal: ActRevealState|null; totalActs: number; liveOutputLines: string[]; stageImageSrc: string|null; heroVideoUri: string|null; storyboardAssets: Record<string,string>; onInterrupt:()=>void; aiSpeaking: boolean; userSpeaking: boolean; scene: SceneState }) {
  const sbEntries = Object.entries(storyboardAssets).slice(-8);
  return (
    <div style={{ position:"absolute",inset:0,zIndex:20 }}>
      {/* Background media */}
      {heroVideoUri ? (
        <video src={heroVideoUri} autoPlay loop muted playsInline style={{ position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover" }}/>
      ) : stageImageSrc ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={stageImageSrc} alt="" style={{ position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover",animation:"ken-burns 22s ease-in-out infinite alternate" }}/>
      ) : (
        <div style={{ position:"absolute",inset:0,background:"radial-gradient(ellipse at 30% 45%, rgba(60,28,8,.5) 0%, transparent 55%), radial-gradient(ellipse at 12% 85%, rgba(255,110,18,.2) 0%, transparent 45%), linear-gradient(175deg, #090503 0%, #1a0e05 50%, #0f0803 100%)" }}/>
      )}

      {/* Gradient overlays */}
      <div style={{ position:"absolute",inset:0,background:"radial-gradient(ellipse at 50% 50%, transparent 35%, rgba(0,0,0,.75) 100%)",pointerEvents:"none" }}/>
      <div style={{ position:"absolute",inset:0,background:"linear-gradient(180deg, rgba(0,0,0,.5) 0%, transparent 30%, transparent 55%, rgba(0,0,0,.85) 100%)",pointerEvents:"none" }}/>

      {/* Progress bar (top under letterbox) */}
      <div style={{ position:"absolute",top:"9.5vh",left:0,right:0,height:1,background:"rgba(255,255,255,.06)",zIndex:30 }}>
        <div style={{ height:"100%",background:"rgba(196,120,38,.5)",boxShadow:"0 0 6px rgba(196,120,38,.5)",width:aiSpeaking?"60%":"30%",transition:"width 1s linear" }}/>
      </div>

      {/* Act label — top left */}
      {actReveal && (
        <div style={{ position:"absolute",top:"12.5vh",left:"4.5vw",zIndex:30,animation:"fade-up .9s ease .2s both" }}>
          <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:200,fontSize:9,letterSpacing:"0.55em",color:"rgba(196,120,38,.5)",textTransform:"uppercase",marginBottom:6 }}>
            ACT {actReveal.beatIndex}
          </div>
          <div style={{ fontFamily:"'Cinzel',serif",fontSize:13,letterSpacing:"0.08em",color:"rgba(242,234,214,.65)" }}>
            {actReveal.actTitle}
          </div>
          <div style={{ marginTop:4,fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:11,color:"rgba(242,234,214,.28)" }}>
            {actReveal.actTimeLabel || scene.actTimeLabel}
          </div>
        </div>
      )}

      {/* Interrupt orb — top right */}
      <div style={{ position:"absolute",top:"11.5vh",right:"4.5vw",zIndex:30,animation:"fade-in .8s ease .5s both",display:"flex",flexDirection:"column",alignItems:"center",gap:6 }}>
        <Orb size="small" listening={userSpeaking} onClick={onInterrupt} label="Ask"/>
        <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:300,fontSize:9,letterSpacing:"0.3em",color:"rgba(196,120,38,.35)",textTransform:"uppercase" }}>interrupt</div>
      </div>

      {/* Narration — lower third */}
      <div style={{ position:"absolute",bottom:"16vh",left:"7vw",right:"7vw",zIndex:30,animation:"fade-up .9s ease .4s both" }}>
        {liveOutputLines.length > 0 && <NarrationText lines={liveOutputLines}/>}
        {liveOutputLines.length === 0 && scene.setup && (
          <p style={{ fontFamily:"'EB Garamond',serif",fontSize:"clamp(17px,2.1vw,26px)",lineHeight:1.78,color:"rgba(242,234,212,.7)",textShadow:"0 2px 24px rgba(0,0,0,.85)",maxWidth:"65ch",fontStyle:"italic" }}>
            {scene.setup}
          </p>
        )}
      </div>

      {/* Storyboard strip — accumulating images */}
      {sbEntries.length > 0 && (
        <div style={{ position:"absolute",bottom:"10.5vh",right:"1.5vw",zIndex:30,display:"flex",flexDirection:"column",gap:6 }}>
          {sbEntries.map(([shotId, uri]) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={shotId} src={uri} alt="" style={{ width:72,height:48,objectFit:"cover",borderRadius:6,border:"1px solid rgba(196,120,38,.25)",opacity:0.7,animation:"slide-in-right .4s ease" }}/>
          ))}
        </div>
      )}

      {/* Timeline */}
      {actReveal && <ActTimeline current={actReveal.beatIndex-1} total={totalActs}/>}
    </div>
  );
}

/* ─── Intermission Overlay ────────────────────────────────── */
function IntermissionOverlay({ prompt, onContinue, actReveal, totalActs, aiSpeaking }:
  { prompt: string; onContinue: ()=>void; actReveal: ActRevealState|null; totalActs: number; aiSpeaking: boolean }) {
  return (
    <div style={{ position:"absolute",inset:0,zIndex:70,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center" }}>
      <div style={{ position:"absolute",inset:0,background:"rgba(0,0,0,.6)",animation:"fade-in .3s ease" }}/>
      <div style={{ position:"relative",zIndex:75,textAlign:"center",animation:"interrupt-slide .4s ease" }}>
        <Orb size="medium" listening={aiSpeaking}/>
        <div style={{ marginTop:32,width:"min(520px,86vw)" }}>
          <div style={{ width:40,height:1,background:"rgba(196,120,38,.3)",margin:"0 auto 16px" }}/>
          <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:300,fontSize:9,letterSpacing:"0.45em",color:"rgba(196,120,38,.5)",textTransform:"uppercase",marginBottom:14 }}>
            Intermission
          </div>
          <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:"clamp(15px,2vw,21px)",lineHeight:1.7,color:"rgba(242,234,214,.82)",marginBottom:28 }}>
            {prompt}
          </p>
          <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:13,color:"rgba(242,234,214,.35)",marginBottom:28 }}>
            Ask any questions, or continue when ready.
          </p>
          <button onClick={onContinue} style={{ background:"rgba(196,120,38,.15)",border:"1px solid rgba(196,120,38,.35)",borderRadius:10,color:"rgba(242,234,214,.9)",padding:"12px 32px",fontFamily:"'Cinzel',serif",fontSize:10,letterSpacing:"0.3em",textTransform:"uppercase",cursor:"pointer" }}>
            Continue
          </button>
        </div>
      </div>
      {actReveal && <ActTimeline current={actReveal.beatIndex-1} total={totalActs}/>}
    </div>
  );
}

/* ─── Complete Screen ─────────────────────────────────────── */
function CompleteScreen({ onRestart }: { onRestart: () => void }) {
  return (
    <div style={{ position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",background:"#070604",animation:"fade-in 1.5s ease",gap:0 }}>
      <div style={{ display:"flex",flexDirection:"column",alignItems:"center",gap:30 }}>
        <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:200,fontSize:10,letterSpacing:"0.5em",color:"rgba(196,120,38,.4)",textTransform:"uppercase",animation:"credits-rise 1s ease .6s both" }}>The Story Is Told</div>
        <div style={{ fontFamily:"'Cinzel',serif",fontWeight:600,fontSize:"clamp(44px,7vw,82px)",letterSpacing:"0.18em",color:"#f2ead6",animation:"credits-rise 1s ease .9s both",textShadow:"0 0 60px rgba(196,120,26,.1)" }}>WHATIFY</div>
        <div style={{ width:64,height:1,background:"linear-gradient(90deg, transparent, rgba(196,120,38,.4), transparent)",animation:"fade-in 1s ease 1.2s both" }}/>
        <div style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:"clamp(14px,1.8vw,19px)",lineHeight:1.8,color:"rgba(242,234,214,.42)",maxWidth:400,textAlign:"center",animation:"credits-rise 1s ease 1.4s both" }}>
          &ldquo;The past is never dead.<br/>It&apos;s not even past.&rdquo;
        </div>
        <div style={{ fontFamily:"'Raleway',sans-serif",fontWeight:300,fontSize:11,color:"rgba(242,234,214,.2)",letterSpacing:"0.15em",animation:"credits-rise 1s ease 1.7s both" }}>— William Faulkner</div>
        <div style={{ marginTop:12,animation:"credits-rise 1s ease 2.1s both" }}>
          <button onClick={onRestart}
            style={{ background:"none",border:"1px solid rgba(196,120,38,.28)",color:"rgba(196,120,38,.6)",padding:"13px 36px",fontFamily:"'Cinzel',serif",fontSize:10,letterSpacing:"0.35em",cursor:"pointer",textTransform:"uppercase",transition:"all .3s ease" }}
            onMouseEnter={e => { const b = e.currentTarget; b.style.borderColor="rgba(196,120,38,.7)"; b.style.color="#c4781a"; b.style.boxShadow="0 0 20px rgba(196,120,26,.15)"; }}
            onMouseLeave={e => { const b = e.currentTarget; b.style.borderColor="rgba(196,120,38,.28)"; b.style.color="rgba(196,120,38,.6)"; b.style.boxShadow="none"; }}>
            Ask Another
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Scene State type ────────────────────────────────────── */
type SceneState = { title: string; setup: string; escalation: string; actTimeLabel: string; narrationScript: string };

/* ══════════════════════════════════════════════════════════
   MAIN PAGE
══════════════════════════════════════════════════════════ */
export default function CinematicPage() {
  const [phase,           setPhase]           = useState<UiPhase>("intro");
  const [inputMode,       setInputMode]       = useState<InputMode>(null);
  const [sessionBooting,  setSessionBooting]  = useState(false);
  const [session,         setSession]         = useState<SessionStart | null>(null);
  const [sessionState,    setSessionState]    = useState<SessionState | null>(null);
  const [scene,           setScene]           = useState<SceneState>({ title:"", setup:"", escalation:"", actTimeLabel:"", narrationScript:"" });
  const [actReveal,       setActReveal]       = useState<ActRevealState | null>(null);
  const [intermissionPrompt, setIntermissionPrompt] = useState("Continue to the next act?");
  const [choices,         setChoices]         = useState<Choice[]>([]);
  const [captions,        setCaptions]        = useState<string[]>([]);
  const [storyboardAssets,setStoryboardAssets]= useState<Record<string, string>>({});
  const [heroVideoUri,    setHeroVideoUri]    = useState<string | null>(null);
  const [interleavedRuns, setInterleavedRuns] = useState<Record<string, InterleavedRun>>({});
  const [interleavedOrder,setInterleavedOrder]= useState<string[]>([]);
  const [liveConnected,   setLiveConnected]   = useState(false);
  const [liveOutputState, setLiveOutputState] = useState<TranscriptState>({ committed: [], pending: "" });
  const [liveInputState,  setLiveInputState]  = useState<TranscriptState>({ committed: [], pending: "" });
  const [liveMicOn,       setLiveMicOn]       = useState(false);
  const [micCaptureReason, setMicCaptureReason] = useState<MicCaptureReason>(null);
  const [capturedPrompt,  setCapturedPrompt]  = useState("");
  const [textInput,       setTextInput]       = useState("");
  const [visualHold,      setVisualHold]      = useState(false);
  const [aiSpeaking,      setAiSpeaking]      = useState(false);

  const actionsSocket   = useRef<WebSocket | null>(null);
  const captionsSocket  = useRef<WebSocket | null>(null);
  const liveSocket      = useRef<WebSocket | null>(null);
  const micStreamRef    = useRef<MediaStream | null>(null);
  const micCtxRef       = useRef<AudioContext | null>(null);
  const micSourceRef    = useRef<MediaStreamAudioSourceNode | null>(null);
  const micProcRef      = useRef<ScriptProcessorNode | null>(null);
  const playCtxRef      = useRef<AudioContext | null>(null);
  const playGainRef     = useRef<GainNode | null>(null);
  const playCursorRef   = useRef(0);
  const revealTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);
  const duckResetRef    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapshotLastRef = useRef(0);
  const lastNarratedRef = useRef<string | null>(null);
  const pendingNarrationRef = useRef<NarrationCue | null>(null);
  const pendingTurnReasonRef = useRef<MicCaptureReason>(null);
  const beginInFlightRef = useRef(false);
  const phaseRef        = useRef(phase);
  const liveMicOnRef    = useRef(liveMicOn);
  const micReasonRef    = useRef(micCaptureReason);
  const inputModeRef    = useRef<InputMode>(inputMode);
  phaseRef.current      = phase;
  liveMicOnRef.current  = liveMicOn;
  micReasonRef.current  = micCaptureReason;
  inputModeRef.current  = inputMode;

  /* ── Derived ── */
  const latestRun = useMemo(() => {
    const id = interleavedOrder[interleavedOrder.length - 1];
    return id ? interleavedRuns[id] ?? null : null;
  }, [interleavedOrder, interleavedRuns]);

  const latestImageBlock = useMemo(() =>
    latestRun ? [...latestRun.blocks].reverse().find(b => b.kind==="image") ?? null : null,
  [latestRun]);

  const stageImageSrc = useMemo(() => blockImageSource(latestImageBlock ?? undefined), [latestImageBlock]);

  const liveOutputLines = useMemo(() => transcriptLines(liveOutputState), [liveOutputState]);
  const liveInputLines = useMemo(() => transcriptLines(liveInputState), [liveInputState]);
  const lastAiLine = liveOutputLines[liveOutputLines.length - 1] ?? "";
  const lastUserLine = liveInputLines[liveInputLines.length - 1] ?? "";

  /* ── Playback context ── */
  const ensurePlayCtx = async (sampleRate: number): Promise<AudioContext> => {
    const ex = playCtxRef.current;
    if (ex && ex.sampleRate === sampleRate) {
      if (ex.state === "suspended") await ex.resume();
      return ex;
    }
    if (ex) await ex.close();
    const ctx  = new AudioContext({ sampleRate });
    const gain = ctx.createGain();
    gain.gain.value = 1;
    gain.connect(ctx.destination);
    playCtxRef.current  = ctx;
    playGainRef.current = gain;
    playCursorRef.current = ctx.currentTime;
    return ctx;
  };

  const playPcmChunk = useCallback(async (b64: string, mimeType: string) => {
    const sr    = parseSampleRate(mimeType);
    const bytes = decodeBase64(b64);
    const i16   = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    const f32   = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;

    const ctx  = await ensurePlayCtx(sr);
    const gain = playGainRef.current;
    const buf  = ctx.createBuffer(1, f32.length, sr);
    buf.copyToChannel(f32, 0);
    const src  = ctx.createBufferSource();
    src.buffer = buf;
    if (gain) src.connect(gain); else src.connect(ctx.destination);
    const startAt = Math.max(ctx.currentTime, playCursorRef.current);
    src.start(startAt);
    playCursorRef.current = startAt + buf.duration;
    setAiSpeaking(true);
    src.onended = () => setAiSpeaking(false);
  }, []);

  /* ── Duck on user speech ── */
  useEffect(() => {
    if (!playGainRef.current) return;
    playGainRef.current.gain.setTargetAtTime(visualHold ? 0.3 : 1, playCtxRef.current?.currentTime ?? 0, 0.03);
  }, [visualHold]);

  const resetDuckTimer = () => {
    if (duckResetRef.current) clearTimeout(duckResetRef.current);
    duckResetRef.current = setTimeout(() => setVisualHold(false), 1200);
  };

  /* ── Session API ── */
  const refreshState = useCallback(async (sid: string) => {
    const r = await fetch(`${API_BASE}/api/v1/session/${sid}/state`);
    if (r.ok) setSessionState(await r.json() as SessionState);
  }, []);

  const ackAction = useCallback(async (sid: string, aid: string) => {
    await fetch(`${API_BASE}/api/v1/session/${sid}/ack`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ action_id:aid }) });
  }, []);

  /* ── Cue narration ── */
  const cueNarration = useCallback((payload: {
    beat_id?: string; title?: string; setup?: string; escalation?: string; act_time_label?: string; narration_script?: string;
  }) => {
    const beatId = (payload.beat_id ?? "").trim();
    if (!beatId || lastNarratedRef.current === beatId) return;
    const ws = liveSocket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    const parts = [
      "Begin narrating the current act now in a cinematic voice.",
      payload.title       ? `Act title: ${payload.title}.` : "",
      payload.act_time_label ? `Time: ${payload.act_time_label}.` : "",
      payload.setup       ? `Setup: ${payload.setup}` : "",
      payload.escalation  ? `Escalation: ${payload.escalation}` : "",
      payload.narration_script ? `Direction: ${payload.narration_script}` : "",
      "Keep it immersive and concise. Invite the listener into the story.",
    ].filter(Boolean).join(" ");

    debugLog("narration:cue", { beatId, phase: phaseRef.current, hasLive: true });
    ws.send(JSON.stringify({ type: "text", text: parts }));
    lastNarratedRef.current = beatId;
  }, []);

  const flushPendingNarration = useCallback(() => {
    const payload = pendingNarrationRef.current;
    if (!payload) return;
    setLiveOutputState({ committed: [], pending: "" });
    cueNarration(payload);
    pendingNarrationRef.current = null;
  }, [cueNarration]);

  /* ── Begin from prompt ── */
  const beginFromPrompt = useCallback(async (prompt: string) => {
    const sid = session?.session_id;
    const cleaned = prompt.trim();
    if (!sid || !cleaned || beginInFlightRef.current) return;

    beginInFlightRef.current = true;
    pendingTurnReasonRef.current = null;
    setCapturedPrompt(cleaned);
    setCaptions([]);
    setChoices([]);
    setLiveOutputState({ committed: [], pending: "" });
    setLiveInputState({ committed: [], pending: "" });
    setPhase("processing");
    try {
      const res = await fetch(`${API_BASE}/api/v1/session/${sid}/begin`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({ divergence_point:cleaned, tone:"cinematic", pacing:"normal" }),
      });
      if (!res.ok) {
        setPhase("onboarding");
        return;
      }
      await refreshState(sid);
    } finally {
      beginInFlightRef.current = false;
    }
  }, [refreshState, session?.session_id]);

  /* ── Mic controls ── */
  const stopMic = useCallback(async ({ preserveTurnReason = false }: { preserveTurnReason?: boolean } = {}) => {
    if (!liveMicOnRef.current) return;
    micProcRef.current?.disconnect();
    if (micProcRef.current) micProcRef.current.onaudioprocess = null;
    micSourceRef.current?.disconnect();
    if (micCtxRef.current) await micCtxRef.current.close();
    micStreamRef.current?.getTracks().forEach(t => t.stop());
    micStreamRef.current = micCtxRef.current = micSourceRef.current = micProcRef.current = null;
    if (!preserveTurnReason) pendingTurnReasonRef.current = null;
    setMicCaptureReason(null);
    setLiveMicOn(false);
    debugLog("mic:stop");
  }, []);

  const startMic = useCallback(async (reason: MicCaptureReason = "interrupt") => {
    const ws = liveSocket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || liveMicOnRef.current) return;
    const stream = await navigator.mediaDevices.getUserMedia({ audio:{ channelCount:1, echoCancellation:true, noiseSuppression:true } });
    const actx   = new AudioContext();
    await actx.resume();
    const source = actx.createMediaStreamSource(stream);
    const proc   = actx.createScriptProcessor(4096, 1, 1);
    const mute   = actx.createGain();
    mute.gain.value = 0;
    pendingTurnReasonRef.current = reason;
    proc.onaudioprocess = ev => {
      const s = liveSocket.current;
      if (!s || s.readyState !== WebSocket.OPEN) return;
      const samples = ev.inputBuffer.getChannelData(0);
      const pcm = downsampleTo16k(samples, actx.sampleRate);
      s.send(pcm.buffer);
    };
    source.connect(proc);
    proc.connect(mute);
    mute.connect(actx.destination);
    micStreamRef.current = stream;
    micCtxRef.current    = actx;
    micSourceRef.current = source;
    micProcRef.current   = proc;
    setMicCaptureReason(reason);
    setLiveMicOn(true);
    debugLog("mic:start", { reason });
  }, []);

  /* ── Live message handler ── */
  const handleLiveMessage = useCallback(async (data: string) => {
    let msg: LiveMessage;
    try { msg = JSON.parse(data) as LiveMessage; } catch { return; }
    const onboardingVoice = phaseRef.current === "onboarding" && inputModeRef.current === "voice";

    if (msg.type === "output_transcript" && msg.text) {
      if (onboardingVoice) return;
      setLiveOutputState(prev => applyTranscriptDelta(prev, msg.text!, Boolean(msg.final)));
      if (msg.final) resetDuckTimer();
      debugLog("live:output", { final: Boolean(msg.final), text: msg.text.slice(0, 120) });
    }
    if (msg.type === "input_transcript" && msg.text) {
      setLiveInputState(prev => applyTranscriptDelta(prev, msg.text!, Boolean(msg.final)));
      setCapturedPrompt(msg.text!);
      setVisualHold(true);
      resetDuckTimer();
      const turnReason = pendingTurnReasonRef.current ?? micReasonRef.current;
      debugLog("live:input", { final: Boolean(msg.final), reason: turnReason, text: msg.text.slice(0, 120) });
      if (msg.final) {
        if (liveMicOnRef.current) void stopMic({ preserveTurnReason: true });
        if (turnReason === "onboarding") {
          void beginFromPrompt(msg.text);
        } else {
          pendingTurnReasonRef.current = null;
        }
      }
    }
    if (msg.type === "audio_chunk" && msg.data && msg.mime_type) {
      if (onboardingVoice) return;
      await playPcmChunk(msg.data, msg.mime_type);
    }
  }, [beginFromPrompt, playPcmChunk, stopMic]);

  /* ── Connect live ── */
  const connectLive = useCallback(async (sessionId: string, autoMic = false) => {
    if (liveSocket.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(`${WS_BASE}/api/v1/session/${sessionId}/live?user_id=cinematic-ui`);
    ws.onopen = async () => {
      setLiveConnected(true);
      debugLog("live:open", { sessionId });
      // Welcome prompt in modeSelect phase
      if (phaseRef.current === "modeSelect") {
        ws.send(JSON.stringify({ type:"text", text:"Welcome the user to WhatIf with a single cinematic sentence under 25 words. Keep it warm, mysterious and cinematic." }));
      }
      if (autoMic) await startMic("onboarding");
    };
    ws.onmessage = async ev => { await handleLiveMessage(ev.data as string); };
    ws.onclose   = () => {
      void stopMic();
      setLiveConnected(false);
      setLiveMicOn(false);
      setMicCaptureReason(null);
      debugLog("live:close", { sessionId });
    };
    ws.onerror   = () => {
      setLiveConnected(false);
      debugLog("live:error", { sessionId });
    };
    liveSocket.current = ws;
  }, [handleLiveMessage, startMic, stopMic]);

  const disconnectLive = useCallback(async () => {
    await stopMic();
    liveSocket.current?.close();
    liveSocket.current = null;
    setLiveConnected(false);
  }, [stopMic]);

  /* ── Start experience ── */
  const startExperience = async () => {
    if (sessionBooting) return;
    setSessionBooting(true);
    // Pre-create AudioContext during user gesture (critical for audio output!)
    try {
      const ctx  = new AudioContext();
      await ctx.resume();
      const gain = ctx.createGain();
      gain.gain.value = 1;
      gain.connect(ctx.destination);
      playCtxRef.current    = ctx;
      playGainRef.current   = gain;
      playCursorRef.current = ctx.currentTime;
    } catch { /* ignore — will be created on first chunk */ }

    try {
      const res = await fetch(`${API_BASE}/api/v1/session/start`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({ divergence_point:null, tone:"cinematic", pacing:"normal", auto_run:false }),
      });
      if (!res.ok) {
        setPhase("intro");
        return;
      }

      const data = await res.json() as SessionStart;
      debugLog("session:start", { sessionId: data.session_id });
      setSession(data);
      setSessionState(null);
      setScene({ title:"", setup:"", escalation:"", actTimeLabel:"", narrationScript:"" });
      setChoices([]); setCaptions([]); setStoryboardAssets({}); setHeroVideoUri(null);
      setInterleavedRuns({}); setInterleavedOrder([]);
      setLiveOutputState({ committed: [], pending: "" });
      setLiveInputState({ committed: [], pending: "" });
      setCapturedPrompt(""); setTextInput(""); setActReveal(null);
      setMicCaptureReason(null);
      pendingTurnReasonRef.current = null;
      pendingNarrationRef.current = null;
      lastNarratedRef.current = null;

      await refreshState(data.session_id);
      setPhase("modeSelect");
      await connectLive(data.session_id, false); // no auto-mic yet
    } catch {
      setPhase("intro");
    } finally {
      setSessionBooting(false);
    }
  };

  /* ── Mode selected ── */
  const handleModeSelect = async (mode: "voice" | "text") => {
    setInputMode(mode);
    setPhase("onboarding");
    if (mode === "voice") {
      setLiveOutputState({ committed: [], pending: "" });
    }
    debugLog("mode:selected", { mode });
    const ws = liveSocket.current;
    if (mode === "text" && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type:"text", text:"The user chose text input. Ask them to type their 'What if' question now. One sentence only." }));
    }
    if (mode === "voice") await startMic("onboarding");
  };

  /* ── Continue act ── */
  const continueAct = async () => {
    const sid = session?.session_id;
    if (!sid) return;
    setPhase("processing");
    const res = await fetch(`${API_BASE}/api/v1/session/${sid}/continue`, { method:"POST", headers:{"Content-Type":"application/json"} });
    if (!res.ok) { setPhase("intermission"); return; }
    await refreshState(sid);
  };

  /* ── Interrupt ── */
  const handleInterrupt = () => {
    const ws = liveSocket.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type:"text", text:"The user wants to ask a question. Pause narration and invite them to speak." }));
    }
    debugLog("interrupt:prompt");
    if (!liveMicOn) void startMic("interrupt");
  };

  /* ── Reset ── */
  const resetToIntro = async () => {
    await disconnectLive();
    actionsSocket.current?.close();
    captionsSocket.current?.close();
    setSession(null); setSessionState(null); setPhase("intro"); setInputMode(null);
    setSessionBooting(false);
    setLiveOutputState({ committed: [], pending: "" });
    setLiveInputState({ committed: [], pending: "" });
    setCapturedPrompt(""); setTextInput("");
    setMicCaptureReason(null);
    pendingTurnReasonRef.current = null;
    pendingNarrationRef.current = null;
    lastNarratedRef.current = null;
  };

  useEffect(() => {
    if (phase !== "onboarding" || inputMode !== "voice" || liveMicOn || !capturedPrompt.trim() || beginInFlightRef.current) {
      return;
    }
    const timer = setTimeout(() => { void beginFromPrompt(capturedPrompt); }, 600);
    return () => clearTimeout(timer);
  }, [beginFromPrompt, capturedPrompt, inputMode, liveMicOn, phase]);

  /* ── Scene snapshot ── */
  const buildSnapshot = useCallback(() => ({
    phase, mode:sessionState?.mode??null,
    beat_index:sessionState?.beat_index??null,
    scene_title:scene.title, scene_setup:scene.setup,
    latest_caption:captions[captions.length-1]??"",
    latest_model_line:liveOutputLines[liveOutputLines.length-1]??"",
    latest_user_line:liveInputLines[liveInputLines.length-1]??"",
    has_image:!!stageImageSrc, has_video:!!heroVideoUri,
    overlays:{ intermission:phase==="intermission", visual_hold:visualHold },
  }), [phase,sessionState,scene,captions,liveOutputLines,liveInputLines,stageImageSrc,heroVideoUri,visualHold]);

  const sendSnapshot = useCallback((force=false) => {
    const ws = liveSocket.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const now = Date.now();
    if (!force && now - snapshotLastRef.current < 1500) return;
    snapshotLastRef.current = now;
    ws.send(JSON.stringify({ type:"scene_snapshot", snapshot:buildSnapshot() }));
  }, [buildSnapshot]);

  useEffect(() => { sendSnapshot(false); }, [sendSnapshot]);
  useEffect(() => {
    if (!liveConnected) return;
    const t = setInterval(() => sendSnapshot(true), 1500);
    return () => clearInterval(t);
  }, [liveConnected, sendSnapshot]);

  /* ── Actions & captions WebSocket ── */
  useEffect(() => {
    const sid = session?.session_id;
    if (!sid) return;

    const actWs = new WebSocket(`${WS_BASE}/api/v1/session/${sid}/actions`);
    const capWs = new WebSocket(`${WS_BASE}/api/v1/session/${sid}/captions`);

    actWs.onmessage = async ev => {
      const action = JSON.parse(ev.data as string) as Action;
      debugLog("action:recv", { type: action.type, actionId: action.action_id });

      if (action.type === "SET_SCENE") {
        const title = (action.payload.title as string)??"";
        const setup = (action.payload.setup as string)??"";
        const escalation = (action.payload.escalation as string)??"";
        const actTimeLabel = (action.payload.act_time_label as string)??"";
        const narrationScript = (action.payload.narration_script as string)??"";
        setScene({ title,setup,escalation,actTimeLabel,narrationScript });
        if (micReasonRef.current === "onboarding" && liveMicOnRef.current) {
          await stopMic({ preserveTurnReason: true });
        }
        pendingNarrationRef.current = {
          beat_id:action.payload.beat_id as string,
          title,
          setup,
          escalation,
          act_time_label:actTimeLabel,
          narration_script:narrationScript,
        };
        if (phaseRef.current === "acting" && !revealTimerRef.current) flushPendingNarration();
      }

      if (action.type === "SHOW_ACT_REVEAL") {
        const actTitle     = (action.payload.act_title as string)??"Act";
        const actTimeLabel = (action.payload.act_time_label as string)??"";
        const beatIndex    = Number(action.payload.beat_index ?? sessionState?.beat_index ?? 1);
        const targetBeats  = Number(action.payload.target_beats ?? sessionState?.target_beats ?? 6);
        setActReveal({ actTitle,actTimeLabel,beatIndex,targetBeats });
        setPhase("actReveal");
        if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
        revealTimerRef.current = setTimeout(() => {
          revealTimerRef.current = null;
          setPhase("acting");
          flushPendingNarration();
        }, 2200);
      }

      if (action.type === "SHOW_INTERMISSION") {
        setIntermissionPrompt((action.payload.prompt as string)??"Continue to the next act?");
        setPhase("intermission");
      }

      if (action.type === "SHOW_CHOICES") {
        setChoices((action.payload.choices as Choice[])?? []);
      }

      if (action.type === "SHOW_STORYBOARD") {
        const frames = (action.payload.frames as Array<{shot_id:string;uri?:string|null}>)?? [];
        setStoryboardAssets(prev => {
          const out = {...prev};
          frames.forEach(f => { if (f.uri) out[f.shot_id] = f.uri; });
          return out;
        });
      }

      if (action.type === "PLAY_VIDEO") {
        const uri = toBrowserUri(action.payload.uri as string);
        if (uri) setHeroVideoUri(uri);
      }

      if (action.type === "SHOW_INTERLEAVED_BLOCKS") {
        const runId = (action.payload.run_id as string)??"run_unknown";
        const runBlocks = (action.payload.blocks as InterleavedBlock[])?? [];
        const trigger   = (action.payload.trigger as string)??"BEAT_START";
        const beatId    = (action.payload.beat_id as string)??"";
        const modelId   = (action.payload.model_id as string)??"";
        const requestId = (action.payload.request_id as string)??"";
        const final     = Boolean(action.payload.final);
        setInterleavedRuns(prev => {
          const ex = prev[runId];
          const merged = [...(ex?.blocks??[]),...runBlocks].sort((a,b)=>a.part_order-b.part_order);
          return {...prev,[runId]:{ run_id:runId,beat_id:beatId,trigger,model_id:modelId,request_id:requestId,final:final||Boolean(ex?.final),blocks:merged }};
        });
        setInterleavedOrder(prev => prev.includes(runId)?prev:[...prev,runId]);
      }

      if (action.type === "CAPTION_APPEND") {
        const text = (action.payload.text as string) ?? "";
        if (text.trim()) setCaptions(prev => [...prev,text].slice(-32));
      }

      if (action.type === "SET_MODE") {
        const mode = action.payload.mode as SessionState["mode"]|undefined;
        if (mode==="ONBOARDING") {
          if (phaseRef.current === "modeSelect" && inputModeRef.current === null) {
            debugLog("action:set_mode_ignored", { mode, phase: phaseRef.current });
          } else {
            setPhase("onboarding");
          }
        }
        else if (mode==="INTERMISSION")  setPhase("intermission");
        else if (mode==="COMPLETE")      setPhase("complete");
        else if ((mode==="STORY"||mode==="CHOICE") && phaseRef.current!=="actReveal") setPhase("acting");
      }

      await ackAction(sid, action.action_id);
      await refreshState(sid);
    };

    capWs.onmessage = ev => {
      const p = JSON.parse(ev.data as string) as { text:string };
      if (p.text) {
        debugLog("caption:recv", { text: p.text.slice(0, 120) });
        setCaptions(prev => [...prev,p.text].slice(-32));
      }
    };

    actionsSocket.current  = actWs;
    captionsSocket.current = capWs;
    return () => { actWs.close(); capWs.close(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ackAction, flushPendingNarration, refreshState, session?.session_id, stopMic]);

  /* ── Cleanup ── */
  useEffect(() => () => {
    if (revealTimerRef.current) clearTimeout(revealTimerRef.current);
    if (duckResetRef.current)   clearTimeout(duckResetRef.current);
    micProcRef.current?.disconnect();
    micSourceRef.current?.disconnect();
    void micCtxRef.current?.close();
    micStreamRef.current?.getTracks().forEach(t => t.stop());
    liveSocket.current?.close();
    actionsSocket.current?.close();
    captionsSocket.current?.close();
    void playCtxRef.current?.close();
  }, []);

  /* ── Render ── */
  return (
    <div style={{ position:"relative",width:"100vw",height:"100vh",background:"#070604",overflow:"hidden",fontFamily:"'EB Garamond',serif",color:"#f2ead6" }}>
      {/* Google Fonts */}
      {/* eslint-disable-next-line @next/next/no-page-custom-font */}
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;900&family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Raleway:wght@200;300;400;500;600&display=swap');`}</style>

      <FilmGrain/>
      <Letterbox show={phase==="acting"||phase==="actReveal"||phase==="intermission"}/>

      {/* Act background when in act phases */}
      {(phase==="acting"||phase==="actReveal"||phase==="intermission") && (stageImageSrc||heroVideoUri) && (
        <div style={{ position:"absolute",inset:0,opacity:1,transition:"opacity 1.1s ease" }}>
          {heroVideoUri
            ? <video src={heroVideoUri} autoPlay loop muted playsInline style={{ position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover" }}/>
            : stageImageSrc
              // eslint-disable-next-line @next/next/no-img-element
              ? <img src={stageImageSrc} alt="" style={{ position:"absolute",inset:0,width:"100%",height:"100%",objectFit:"cover",animation:"ken-burns 22s ease-in-out infinite alternate" }}/>
              : null
          }
          <div style={{ position:"absolute",inset:0,background:"radial-gradient(ellipse at 50% 50%, transparent 30%, rgba(0,0,0,.72) 100%)",pointerEvents:"none" }}/>
        </div>
      )}

      {/* Screens */}
      {phase==="intro"        && <IntroScreen onStart={() => void startExperience()} busy={sessionBooting}/>}
      {phase==="modeSelect"   && <ModeSelectScreen onSelect={m => void handleModeSelect(m)} aiSpeaking={aiSpeaking} lastAiLine={lastAiLine}/>}
      {phase==="onboarding"   && (
        <OnboardingScreen
          transcript={lastUserLine}
          inputMode={inputMode}
          textInput={textInput}
          onTextChange={setTextInput}
          onTextSubmit={() => void beginFromPrompt(textInput)}
          aiSpeaking={aiSpeaking}
          liveMicOn={liveMicOn}
          onVoiceRetry={() => void startMic("onboarding")}
          lastAiLine={lastAiLine}
        />
      )}
      {phase==="processing"   && <ProcessingScreen/>}
      {phase==="actReveal"    && actReveal && <ActRevealOverlay actReveal={actReveal}/>}
      {phase==="acting"       && (
        <ActingScreen
          actReveal={actReveal}
          totalActs={actReveal?.targetBeats ?? 6}
          liveOutputLines={(liveOutputLines.length > 0 ? liveOutputLines : captions.slice(-1)).slice(-4)}
          stageImageSrc={stageImageSrc}
          heroVideoUri={heroVideoUri}
          storyboardAssets={storyboardAssets}
          onInterrupt={handleInterrupt}
          aiSpeaking={aiSpeaking}
          userSpeaking={liveMicOn && visualHold}
          scene={scene}
        />
      )}
      {phase==="intermission" && (
        <IntermissionOverlay
          prompt={intermissionPrompt}
          onContinue={() => void continueAct()}
          actReveal={actReveal}
          totalActs={actReveal?.targetBeats ?? 6}
          aiSpeaking={aiSpeaking}
        />
      )}
      {phase==="complete"     && <CompleteScreen onRestart={() => void resetToIntro()}/>}

      {/* Choices overlay (when acting phase has choices) */}
      {phase==="acting" && choices.length>0 && (
        <div style={{ position:"absolute",bottom:"18vh",left:"50%",transform:"translateX(-50%)",zIndex:50,display:"flex",gap:16,animation:"fade-up .5s ease" }}>
          {choices.map(c => (
            <button key={c.choice_id} onClick={async () => {
              if (!session) return;
              await fetch(`${API_BASE}/api/v1/session/${session.session_id}/choice`, { method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ choice_id:c.choice_id }) });
              setChoices([]);
              await refreshState(session.session_id);
            }} style={{ background:"rgba(196,120,38,.1)",border:"1px solid rgba(196,120,38,.35)",borderRadius:12,color:"#f2ead6",padding:"14px 24px",cursor:"pointer",textAlign:"center",maxWidth:220,transition:"all .3s" }}
            onMouseEnter={e => { const b=e.currentTarget; b.style.background="rgba(196,120,38,.2)"; b.style.borderColor="rgba(196,120,38,.6)"; }}
            onMouseLeave={e => { const b=e.currentTarget; b.style.background="rgba(196,120,38,.1)"; b.style.borderColor="rgba(196,120,38,.35)"; }}>
              <p style={{ fontFamily:"'Cinzel',serif",fontSize:12,letterSpacing:"0.1em",margin:"0 0 6px" }}>{c.label}</p>
              <span style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:13,opacity:0.6 }}>{c.consequence_hint}</span>
            </button>
          ))}
        </div>
      )}

      {/* Captions strip at very bottom */}
      {(phase==="acting"||phase==="intermission") && captions.length>0 && (
        <div style={{ position:"fixed",bottom:"9.5vh",left:"50%",transform:"translateX(-50%)",maxWidth:"min(900px,86vw)",zIndex:40,textAlign:"center",pointerEvents:"none" }}>
          <p style={{ fontFamily:"'EB Garamond',serif",fontStyle:"italic",fontSize:15,color:"rgba(242,234,214,.45)",lineHeight:1.5 }}>
            {captions[captions.length-1]}
          </p>
        </div>
      )}
    </div>
  );
}
