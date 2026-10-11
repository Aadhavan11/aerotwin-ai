import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import App from "./App";
import Login from "./pages/Login";
import { AuthProvider, useAuth } from "./lib/auth";
import { RoleProvider } from "./lib/role";
import "./index.css";

/** The app only shows once there is a session (or when this server has no sign-in at all, as in local runs). */
function Gate() {
  const { status } = useAuth();
  if (status === "loading") return <div className="login-splash">AeroTwin</div>;
  if (status === "anon") return <Login />;
  return (
    <RoleProvider>
      <App />
    </RoleProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
