import { useEffect, useState } from "react";
import { api } from "../../services/api";
import type { RequestsPayload } from "../../types/clinic";

export function RequestsPage() {
  const [data, setData] = useState<RequestsPayload | null>(null);

  useEffect(() => {
    api.requests().then(setData).catch(() => setData(null));
  }, []);

  return (
    <div className="space-y-8">
      <h2 className="text-4xl">Saved requests</h2>
      <p className="max-w-xl text-ink/70">
        Refills, doctor notes, callbacks, and simulated email confirmations land here after a workflow commits them.
      </p>
      <RequestGroup title="Refills" rows={data?.refills.map((row) => `${row.medication} · ${row.pharmacy} · ${row.status}`) ?? []} />
      <RequestGroup
        title="Doctor notes"
        rows={data?.doctor_notes?.map((row) => `${row.summary}${row.created_at ? ` · ${new Date(row.created_at).toLocaleString()}` : ""}`) ?? []}
      />
      <RequestGroup title="Callbacks" rows={data?.callbacks.map((row) => `${row.kind} · ${row.name} · ${row.number} · ${row.reason}`) ?? []} />
      <RequestGroup title="Email confirmations" rows={data?.confirmations.map((row) => `${row.to} · ${row.subject}`) ?? []} />
    </div>
  );
}

function RequestGroup({ title, rows }: { title: string; rows: string[] }) {
  return (
    <section>
      <h3 className="text-2xl">{title}</h3>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-ink/60">None yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {rows.map((row) => (
            <li key={row} className="rounded-xl border border-line bg-panel px-4 py-3 text-sm">{row}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
