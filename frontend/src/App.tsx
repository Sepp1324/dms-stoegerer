import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom";
import { getSessionId, isLoggedIn, onAuthChange } from "./api";
import ErrorBoundary from "./components/ErrorBoundary";
import Login from "./components/Login";
import DocumentsPage from "./components/DocumentsPage";
import SharePage from "./components/SharePage";

// Echtes URL-Routing (#7, Stage 1). Bisher wurde nur /share/<token> manuell aus
// dem Pfad gelesen und der Rest lief rein über React-State. Jetzt liefert der
// Router die Routen; die Dokumenten-App (inkl. Dokument-Deep-Link
// /dokument/:id) hängt unter dem Catch-all. View- und Filter-Routen folgen in
// den nächsten Stufen. Der nginx-/Vite-SPA-Fallback liefert Deep-Links
// weiterhin index.html aus.

function ShareRoute({
  loggedIn,
  onLogin,
  onAuthLost,
}: {
  loggedIn: boolean;
  onLogin: () => void;
  onAuthLost: () => void;
}) {
  const { token } = useParams<{ token: string }>();
  // Kein anonymer Zugriff: erst anmelden. Der Login bleibt an dieser URL; nach
  // erfolgreicher Anmeldung rendert dieselbe Route die Vorschau.
  if (!loggedIn) {
    return (
      <Login
        onSuccess={onLogin}
        hint="Bitte melde dich an, um das geteilte Dokument zu sehen."
      />
    );
  }
  return <SharePage token={token ?? ""} onAuthLost={onAuthLost} />;
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  // Sitzungs-ID als State (P1): Ein Benutzerwechsel A -> B (auch aus einem anderen
  // Tab) lässt loggedIn true, würde DocumentsPage also NICHT neu mounten und könnte
  // A-Daten unter B zeigen. Wir keyen DocumentsPage mit der Sitzungs-ID -> bei
  // Wechsel remountet React die Seite frisch.
  const [sessionId, setSessionId] = useState(getSessionId());

  // Tab-übergreifende UI-Synchronisation (P1): Ein Login/Logout/Benutzerwechsel –
  // auch in einem ANDEREN Tab – zieht loggedIn UND die Sitzungs-ID nach.
  useEffect(
    () =>
      onAuthChange(() => {
        setLoggedIn(isLoggedIn());
        setSessionId(getSessionId());
      }),
    [],
  );

  return (
    <ErrorBoundary>
      <BrowserRouter>
        {/* Die v6-``future``-Flags (v7_startTransition/v7_relativeSplatPath) sind in
            react-router v7 der Standard und wurden aus der Prop entfernt. */}
        <Routes>
        <Route
          path="/share/:token"
          element={
            <ShareRoute
              loggedIn={loggedIn}
              onLogin={() => setLoggedIn(true)}
              onAuthLost={() => setLoggedIn(false)}
            />
          }
        />
        {/* Die gesamte Dokumenten-App inkl. /dokument/:id (Deep-Link). */}
        <Route
          path="/*"
          element={
            loggedIn ? (
              <DocumentsPage
                key={sessionId ?? "anon"}
                onLogout={() => setLoggedIn(false)}
              />
            ) : (
              <Login onSuccess={() => setLoggedIn(true)} />
            )
          }
        />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
