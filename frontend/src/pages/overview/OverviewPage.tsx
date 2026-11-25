import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type { Overview } from "../../types/clinic";

export function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.overview().then(setData).catch((reason: Error) => setError(reason.message));
  }, []);

  if (error) return <p className="text-clay">{error}</p>;
  if (!data) return <p>Loading the chart…</p>;

  return (
    <div className="space-y-8">
      <header>
        <h2 className="text-4xl">Synthetic chart</h2>
        <p className="mt-2 text-ink/70">
          {data.office_open ? "Office is open" : "Office is closed"} · {data.hours}
        </p>
      </header>
      <div className="grid gap-4 md:grid-cols-3">
        {data.patients.map((patient) => (
          <article key={patient.code} className="rounded-2xl border border-line bg-panel p-5">
            <p className="text-xs text-pine">{patient.code}</p>
            <h3 className="text-2xl">{patient.name}</h3>
            <p className="mt-3 text-sm">Labs: {patient.lab_status}</p>
            <p className="text-sm">Balance: ${patient.balance}</p>
            <p className="mt-2 text-sm text-ink/70">{patient.next_appointment ?? "No upcoming appointment"}</p>
          </article>
        ))}
      </div>
      <section>
        <h3 className="text-2xl">Open slots</h3>
        <ul className="mt-3 grid gap-2 md:grid-cols-2">
          {data.open_slots.map((slot) => (
            <li key={`${slot.doctor}-${slot.when}`} className="rounded-xl border border-line bg-panel px-4 py-3 text-sm">
              {slot.doctor} · {slot.when}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
