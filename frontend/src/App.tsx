import { useState } from "react";
import { isLoggedIn } from "./api";
import Login from "./components/Login";
import DocumentsPage from "./components/DocumentsPage";
import SharePage from "./components/SharePage";

// Sehr schlichtes Pfad-Routing (die SPA nutzt keinen Router): nur die
// Freigabe-Aufrufseite /share/<token> wird gesondert behandelt, alles andere
// ist die reguläre Dokumenten-App. Der Nginx-/Vite-SPA-Fallback liefert für
// Deep-Links ohnehin index.html aus.
function shareToken(): string | null {
  const m = window.location.pathname.match(/^\/share\/([^/?#]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : null;
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());
  const token = shareToken();

  if (token) {
    // Kein anonymer Zugriff: erst anmelden. Der Login wird an Ort und Stelle
    // gerendert, die Browser-URL bleibt /share/<token> — nach erfolgreicher
    // Anmeldung rendert dieselbe URL die Vorschau (impliziter Return zur Seite).
    if (!loggedIn) {
      return (
        <Login
          onSuccess={() => setLoggedIn(true)}
          hint="Bitte melde dich an, um das geteilte Dokument zu sehen."
        />
      );
    }
    return <SharePage token={token} onAuthLost={() => setLoggedIn(false)} />;
  }

  if (!loggedIn) {
    return <Login onSuccess={() => setLoggedIn(true)} />;
  }
  return <DocumentsPage onLogout={() => setLoggedIn(false)} />;
}
