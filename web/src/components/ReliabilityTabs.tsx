import { useLocation, useNavigate } from "react-router-dom";

export default function ReliabilityTabs() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <div className="page-heading"><div><h1>Reliability</h1><p>Unscheduled removals, alerts and the ATA taxonomy.</p></div><span className="sim-tag">SIMULATED</span></div>
      <nav className="seg" aria-label="Reliability views">
        <button className={location.pathname === "/reliability" ? "on" : ""} aria-current={location.pathname === "/reliability" ? "page" : undefined} onClick={() => navigate("/reliability")}>Reliability pack</button>
        <button className={location.pathname === "/reliability/ata" ? "on" : ""} aria-current={location.pathname === "/reliability/ata" ? "page" : undefined} onClick={() => navigate("/reliability/ata")}>ATA table</button>
      </nav>
    </>
  );
}
