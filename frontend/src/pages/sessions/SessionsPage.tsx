import { useEffect, useState } from "react";
import { api } from "../../services/api";

type Line = { who: string; text: string; at: string };
type SessionRow = {
  session_id: string;
  phase: string;
  verified: boolean;
  started_at: string | null;
  lines: Line[];
};

function when(value: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export function SessionsPage() {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [openId, setOpenId] = useState("");

  useEffect(() => {
    api.sessions().then((payload) => setRows(payload.sessions)).catch(() => setRows([]));
  }, []);

  return (
    <div>
      <h2 className="text-4xl">Sessions</h2>
      <p className="mt-2 max-w-xl text-ink/70">
        Each call is saved with its time and the redacted chat. A verification turn is stored as a placeholder, not the date of birth or last four.
      </p>
      {rows.length === 0 ? (
        <p className="mt-8 rounded-2xl border border-dashed border-line bg-panel px-5 py-8">No saved calls yet.</p>
      ) : (
        <ul className="mt-6 space-y-3">
          {rows.map((row) => (
            <li key={row.session_id} className="rounded-2xl border border-line bg-panel px-4 py-3">
              <button type="button" className="w-full text-left" onClick={() => setOpenId(openId === row.session_id ? "" : row.session_id)}>
                <p className="text-sm">{when(row.started_at)}</p>
                <p className="mt-1 text-xs text-ink/60">
                  {row.phase} · {row.verified ? "verified" : "not verified"} · {row.lines.length} lines
                </p>
              </button>
              {openId === row.session_id ? (
                <div className="mt-3 space-y-2">
                  {row.lines.map((line, index) => (
                    <p key={`${line.at}-${index}`} className={`max-w-[90%] rounded-xl px-2.5 py-1.5 text-xs leading-snug ${line.who === "caller" ? "ml-auto bg-pine text-white" : "bg-paper text-ink"}`}>
                      <span className="block text-[10px] opacity-70">{when(line.at)}</span>
                      {line.text}
                    </p>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
