import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { getReviewer, setReviewer } from "./api";
import DashboardPage from "./pages/DashboardPage";
import DocumentPage from "./pages/DocumentPage";
import QueuePage from "./pages/QueuePage";
import "./styles.css";

function Layout() {
  const [name, setName] = useState(getReviewer());
  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="brand">
          <span className="logo" aria-hidden>
            ▤
          </span>{" "}
          InvoiceFlow
        </NavLink>
        <nav>
          <NavLink to="/" end>
            Queue
          </NavLink>
          <NavLink to="/dashboard">Dashboard</NavLink>
        </nav>
        <label className="reviewer">
          Reviewing as
          <input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setReviewer(e.target.value);
            }}
          />
        </label>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<QueuePage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
        </Routes>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Layout />
    </BrowserRouter>
  </StrictMode>,
);
