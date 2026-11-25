import { useEffect, useState } from "react";
import { api } from "../../services/api";

export function SettingsPage() {
  const [now, setNow] = useState("");
  const [open, setOpen] = useState<boolean | null>(null);
  const [retention, setRetention] = useState("");
  const [override, setOverride] = useState("2026-06-13T22:00:00-04:00");
  const [error, setError] = useState("");

  useEffect(() => {
    api.settings().then((settings) => {
      setNow(settings.now);
      setOpen(settings.office_open);
      setRetention(`Audio ${settings.recording_retention_days} days · transcripts ${settings.transcript_retention_days} days · database ${settings.database}`);
    }).catch((reason: Error) => setError(reason.message));
  }, []);

  async function apply(value: string | null) {
    const result = await api.setClock(value);
    setNow(result.now);
    setOpen(result.office_open);
  }

  return (
    <div className="max-w-xl">
      <h2 className="text-4xl">Clinic clock</h2>
      <p className="mt-2 text-ink/70">{retention}</p>
      <p className="mt-4 text-sm">Now: {now || "…"} · {open === null ? "" : open ? "open" : "closed"}</p>
      {error ? <p className="mt-2 text-clay">{error}</p> : null}
      <label className="mt-6 block text-sm">
        Override (ISO, America/New_York offset)
        <input
          className="mt-2 w-full rounded-xl border border-line bg-panel px-3 py-2"
          value={override}
          onChange={(event) => setOverride(event.target.value)}
        />
      </label>
      <div className="mt-4 flex gap-3">
        <button type="button" className="rounded-full bg-pine px-4 py-2 text-sm text-white" onClick={() => apply(override)}>
          Use override
        </button>
        <button type="button" className="rounded-full border border-line px-4 py-2 text-sm" onClick={() => apply(null)}>
          Use the real clock
        </button>
      </div>
    </div>
  );
}
