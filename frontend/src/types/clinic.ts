export type PatientCard = {
  code: string;
  name: string;
  lab_status: string;
  balance: string;
  next_appointment: string | null;
};

export type Overview = {
  hours: string;
  office_open: boolean;
  now: string;
  active_sessions: number;
  patients: PatientCard[];
  open_slots: { doctor: string; when: string }[];
};

export type VoiceHealth = {
  status: string;
  office_open: boolean;
  voice: { livekit: string; ollama: string; ollama_model: string; kokoro: string };
  session_store: string;
};

export type EvalResult = {
  id: string;
  passed: boolean;
  detail: string;
  last_say?: string;
};

export type EvalRun = {
  id: string;
  passed: number;
  failed: number;
  created_at: string;
  results: { scenario_id: string; passed: boolean; detail: string }[];
};

export type RequestsPayload = {
  refills: { id: string; medication: string; pharmacy: string; status: string; summary: string }[];
  callbacks: { id: string; name: string; number: string; reason: string; kind: string; summary: string }[];
  doctor_notes: { id: string; category: string; summary: string; created_at?: string }[];
  confirmations: { id: string; to: string; subject: string }[];
};
