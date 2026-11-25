import { useEffect, useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../../services/api";
import type { EvalRun } from "../../types/clinic";

export function AnalyticsPage() {
  const [run, setRun] = useState<EvalRun | null>(null);

  useEffect(() => {
    api.evaluations().then((payload) => setRun(payload.runs[0] ?? null)).catch(() => setRun(null));
  }, []);

  const data = (run?.results ?? []).map((item) => ({
    name: item.scenario_id.replaceAll("_", " "),
    passed: item.passed ? 1 : 0,
  }));

  return (
    <div>
      <h2 className="text-4xl">Scenario results</h2>
      <p className="mt-2 max-w-xl text-ink/70">
        Each bar is one assignment scenario from the latest saved run. A full bar passed. Run the evaluation first if this is empty.
      </p>
      {data.length === 0 ? (
        <p className="mt-8 text-sm">No saved run yet.</p>
      ) : (
        <div className="mt-6 h-96 rounded-2xl border border-line bg-panel p-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ left: 24, right: 12 }}>
              <XAxis type="number" domain={[0, 1]} hide />
              <YAxis type="category" dataKey="name" width={180} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="passed" fill="#1e6b4a" radius={6} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
