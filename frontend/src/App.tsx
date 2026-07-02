import { useState } from "react";
import { isLoggedIn } from "./api";
import Login from "./components/Login";
import DocumentsPage from "./components/DocumentsPage";

export default function App() {
  const [loggedIn, setLoggedIn] = useState(isLoggedIn());

  if (!loggedIn) {
    return <Login onSuccess={() => setLoggedIn(true)} />;
  }
  return <DocumentsPage onLogout={() => setLoggedIn(false)} />;
}
