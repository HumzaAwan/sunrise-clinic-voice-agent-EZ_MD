import { Route, Routes } from "react-router-dom";
import { Shell } from "./components/shared/Shell";
import { DeskPage } from "./pages/desk/DeskPage";
import { OverviewPage } from "./pages/overview/OverviewPage";
import { RequestsPage } from "./pages/requests/RequestsPage";
import { SessionsPage } from "./pages/sessions/SessionsPage";
import { SettingsPage } from "./pages/settings/SettingsPage";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<DeskPage />} />
        <Route path="overview" element={<OverviewPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="requests" element={<RequestsPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
