import { NavLink, Outlet, useLocation } from "react-router-dom";
import { CallPanel } from "../voice/CallPanel";

const links = [
  ["/", "Desk"],
  ["/overview", "Chart"],
  ["/sessions", "Sessions"],
  ["/requests", "Requests"],
  ["/settings", "Settings"],
];

export function Shell() {
  const onDesk = useLocation().pathname === "/";
  return (
    <div className="min-h-screen md:grid md:grid-cols-[220px_1fr]">
      <aside className="border-b border-line bg-panel px-5 py-6 md:border-b-0 md:border-r">
        <p className="text-xs uppercase tracking-[0.18em] text-pine">EZ MD</p>
        <h1 className="mt-1 text-2xl text-ink">Sunrise</h1>
        <p className="text-sm text-ink/70">Family Clinic</p>
        <nav className="mt-8 flex flex-wrap gap-2 md:flex-col">
          {links.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `rounded-full px-3 py-1.5 text-sm ${isActive ? "bg-pine text-white" : "text-ink hover:bg-paper"}`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="px-5 py-6 md:px-10">
        {/* Keep the LiveKit room mounted while browsing other tabs; only hide the UI. */}
        <div className={onDesk ? "mb-6" : "hidden"}>
          <CallPanel />
        </div>
        <Outlet />
      </main>
    </div>
  );
}
