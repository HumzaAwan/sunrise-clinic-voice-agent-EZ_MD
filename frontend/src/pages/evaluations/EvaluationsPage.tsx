import { useState } from "react";
import { api } from "../../services/api";
import type { EvalResult } from "../../types/clinic";

export function EvaluationsPage() {
  const [results, setResults] = useState<EvalResult[] | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    setError("");
    try {
      const payload = await api.runEvaluations();
      setResults(payload.results);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Run failed");
    } finally {
      setRunning(false);
    }
  }

  const passed = results?.filter((item) => item.passed).length ?? 0;

  return (
    <div>
      <h2 className="text-4xl">Workflow evaluation</h2>
      <p className="mt-2 max-w-2xl text-ink/70">
        Runs the assignment scenarios through the same LangGraph clinic workflow the voice worker will call.
        This is not a text chatbot for patients.
      </p>
      <button
        type="button"
        onClick={run}
        disabled={running}
        className="mt-6 rounded-full bg-pine px-5 py-3 text-sm text-white disabled:opacity-60"
      >
        {running ? "Running…" : "Run assignment scenarios"}
      </button>
      {error ? <p className="mt-4 text-clay">{error}</p> : null}
      {results ? (
        <p className="mt-4 text-sm">{passed} passed · {results.length - passed} failed</p>
      ) : null}
      <ul className="mt-4 space-y-2">
        {results?.map((item) => (
          <li key={item.id} className="rounded-xl border border-line bg-panel px-4 py-3 text-sm">
            <span className={item.passed ? "text-pine" : "text-clay"}>{item.passed ? "Pass" : "Fail"}</span>
            {" · "}{item.id}
            {item.detail ? <span className="block text-ink/70">{item.detail}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
