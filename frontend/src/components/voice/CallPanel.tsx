import { createLocalAudioTrack, Room, RoomEvent, Track, type LocalAudioTrack, type Participant, type TranscriptionSegment } from "livekit-client";
import { useEffect, useRef, useState } from "react";
import { api } from "../../services/api";

type CallState = "idle" | "connecting" | "live" | "ended";
type Line = { id: string; who: "you" | "clinic"; text: string };

function micRank(label: string): number {
  const name = label.toLowerCase();
  if (/barracuda|headset|headphone|earphone|hands-free|airpod|earbuds|buds/.test(name)) return 3;
  if (/array|built-in|internal|webcam|camera/.test(name)) return 0;
  if (/microphone/.test(name)) return 2;
  return 1;
}

function prefersRawCapture(label: string): boolean {
  return micRank(label) >= 2;
}

export function CallPanel() {
  const [livekitUp, setLivekitUp] = useState(false);
  const [error, setError] = useState("");
  const [callState, setCallState] = useState<CallState>("idle");
  const [micLive, setMicLive] = useState(false);
  const [level, setLevel] = useState(0);
  const [micName, setMicName] = useState("Microphone");
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const roomRef = useRef<Room | null>(null);
  const audioRef = useRef<HTMLDivElement | null>(null);
  const chatRef = useRef<HTMLDivElement | null>(null);
  const meterRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const localTrackRef = useRef<LocalAudioTrack | null>(null);
  const userPickedMic = useRef(false);

  useEffect(() => {
    api.health().then((health) => setLivekitUp(health.voice.livekit === "up")).catch(() => setLivekitUp(false));
    return () => {
      stopLocalAudio();
      void roomRef.current?.disconnect();
    };
  }, []);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight });
  }, [lines]);

  function stopLocalAudio() {
    if (meterRef.current !== null) cancelAnimationFrame(meterRef.current);
    meterRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
    localTrackRef.current?.stop();
    localTrackRef.current = null;
    setLevel(0);
    setMicLive(false);
  }

  function putLine(line: Line) {
    setLines((current) => {
      const next = [...current];
      const index = next.findIndex((item) => item.id === line.id);
      if (index >= 0) next[index] = line;
      else next.push(line);
      return next.slice(-24);
    });
  }

  function measure(context: AudioContext, mediaTrack: MediaStreamTrack) {
    if (meterRef.current !== null) cancelAnimationFrame(meterRef.current);
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(new MediaStream([mediaTrack])).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) {
        const sample = (value - 128) / 128;
        sum += sample * sample;
      }
      setLevel(Math.min(100, Math.round(Math.sqrt(sum / samples.length) * 1800)));
      meterRef.current = requestAnimationFrame(tick);
    };
    tick();
  }

  async function listMics(): Promise<MediaDeviceInfo[]> {
    const listed = (await navigator.mediaDevices.enumerateDevices()).filter((item) => item.kind === "audioinput");
    setDevices(listed);
    return listed;
  }

  function bestMicId(listed: MediaDeviceInfo[], chosenId: string): string {
    if (userPickedMic.current && chosenId && listed.some((item) => item.deviceId === chosenId)) return chosenId;
    const ranked = [...listed].sort((left, right) => micRank(right.label) - micRank(left.label) || left.label.localeCompare(right.label));
    return ranked[0]?.deviceId || chosenId;
  }

  async function prepareMicList(): Promise<MediaDeviceInfo[]> {
    let listed = await listMics();
    if (listed.length === 0 || listed.every((item) => !item.label)) {
      const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
      probe.getTracks().forEach((item) => item.stop());
      listed = await listMics();
    }
    if (!userPickedMic.current) {
      const best = bestMicId(listed, "");
      if (best) setDeviceId(best);
    }
    return listed;
  }

  async function openMic(chosenId: string) {
    const context = audioContextRef.current ?? new AudioContext();
    audioContextRef.current = context;
    await context.resume();
    localTrackRef.current?.stop();
    localTrackRef.current = null;
    const listed = await prepareMicList();
    const activeChoice = bestMicId(listed, chosenId);
    const label = listed.find((item) => item.deviceId === activeChoice)?.label ?? "";
    // Headsets: keep echoCancellation off so the agent voice is not gated,
    // but keep noise suppression on so the mic is less twitchy.
    const headset = prefersRawCapture(label);
    let track: LocalAudioTrack;
    try {
      track = await createLocalAudioTrack({
        deviceId: activeChoice ? { exact: activeChoice } : undefined,
        echoCancellation: headset ? false : true,
        noiseSuppression: true,
        autoGainControl: false,
        channelCount: 1,
      });
    } catch {
      track = await createLocalAudioTrack({
        deviceId: activeChoice ? { ideal: activeChoice } : undefined,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: false,
        channelCount: 1,
      });
    }
    localTrackRef.current = track;
    const mediaTrack = track.mediaStreamTrack;
    const showLive = () => setMicLive(mediaTrack.readyState === "live" && !mediaTrack.muted);
    mediaTrack.onmute = showLive;
    mediaTrack.onunmute = showLive;
    showLive();
    setMicName(mediaTrack.label || label || "Microphone");
    const activeId = mediaTrack.getSettings().deviceId ?? activeChoice;
    if (activeId) setDeviceId(activeId);
    measure(context, mediaTrack);
    return track;
  }

  function addTranscript(segments: TranscriptionSegment[], participant?: Participant) {
    const who: Line["who"] = participant?.isLocal ? "you" : "clinic";
    for (const segment of segments) {
      const text = segment.text.trim();
      if (text) putLine({ id: segment.id, who, text });
    }
  }

  async function startCall() {
    setError("");
    setLines([]);
    setCallState("connecting");
    const context = new AudioContext();
    audioContextRef.current = context;
    void context.resume();
    try {
      const mic = await openMic(deviceId);
      const ticket = await api.openSession();
      if (!ticket.available || !ticket.url || !ticket.token) {
        throw new Error(ticket.reason ?? "LiveKit is not ready.");
      }
      const room = new Room();
      roomRef.current = room;
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio || !audioRef.current) return;
        const element = track.attach();
        element.autoplay = true;
        audioRef.current.appendChild(element);
      });
      room.on(RoomEvent.TranscriptionReceived, addTranscript);
      room.on(RoomEvent.Disconnected, () => {
        stopLocalAudio();
        setCallState("ended");
      });
      await room.connect(ticket.url, ticket.token);
      await room.localParticipant.publishTrack(mic);
      setCallState("live");
    } catch (reason) {
      stopLocalAudio();
      setCallState("idle");
      setError(reason instanceof Error ? reason.message : "The call could not start.");
    }
  }

  async function chooseMic(nextId: string) {
    userPickedMic.current = true;
    setDeviceId(nextId);
    if (callState !== "live" || !audioContextRef.current) return;
    const mic = await openMic(nextId);
    const room = roomRef.current;
    if (!room) return;
    const current = room.localParticipant.getTrackPublication(Track.Source.Microphone);
    if (current?.track) await room.localParticipant.unpublishTrack(current.track);
    await room.localParticipant.publishTrack(mic);
  }

  async function hangUp() {
    stopLocalAudio();
    await roomRef.current?.disconnect();
    roomRef.current = null;
    if (audioRef.current) audioRef.current.replaceChildren();
    setCallState("ended");
  }

  const hearing = level > 2;
  const micLabel = callState !== "live"
    ? "Mic off"
    : !micLive
      ? "Headphone mic is muted"
      : hearing
        ? "Hearing you"
        : "Mic open, no sound from this device";
  return (
    <section className="w-full max-w-sm">
      <select
        className="mb-2 w-full truncate rounded-xl border border-line bg-panel px-2 py-1.5 text-xs"
        value={deviceId}
        onMouseDown={() => void prepareMicList().catch(() => setError("Allow the microphone, then pick your headphones."))}
        onChange={(event) => void chooseMic(event.target.value)}
      >
        {devices.length === 0 ? <option value="">Default microphone</option> : null}
        {devices.map((device) => (
          <option key={device.deviceId} value={device.deviceId}>
            {device.label || "Microphone"}
          </option>
        ))}
      </select>
      <div className="flex items-center gap-3 rounded-2xl border border-line bg-panel px-3 py-2">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${hearing ? "bg-pine" : "bg-line"}`} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm">{micLabel}</p>
          <p className="truncate text-[11px] text-ink/50">{callState === "live" ? micName : "Press Call and allow the microphone"}</p>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-paper">
            <div className="h-full bg-pine" style={{ width: `${callState === "live" ? level : 0}%` }} />
          </div>
        </div>
        {callState === "live" ? (
          <button type="button" onClick={hangUp} className="rounded-full bg-clay px-3 py-1.5 text-xs text-white">
            End
          </button>
        ) : (
          <button
            type="button"
            onClick={startCall}
            disabled={callState === "connecting" || !livekitUp}
            className="rounded-full bg-pine px-3 py-1.5 text-xs text-white disabled:opacity-50"
          >
            {callState === "connecting" ? "…" : "Call"}
          </button>
        )}
      </div>
      {error ? <p className="mt-2 text-xs text-clay">{error}</p> : null}
      <div ref={audioRef} className="hidden" />
      <div ref={chatRef} className="mt-2 h-52 space-y-1.5 overflow-y-auto rounded-2xl border border-line bg-panel p-2">
        {lines.length === 0 ? (
          <p className="px-1 py-6 text-center text-xs text-ink/50">Your words appear on the right.</p>
        ) : (
          lines.map((line) => (
            <p
              key={line.id}
              className={`max-w-[90%] rounded-xl px-2.5 py-1.5 text-xs leading-snug ${
                line.who === "you" ? "ml-auto bg-pine text-white" : "bg-paper text-ink"
              }`}
            >
              {line.text}
            </p>
          ))
        )}
      </div>
    </section>
  );
}
