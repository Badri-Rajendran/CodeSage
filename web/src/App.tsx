import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ReviewConsole } from "./pages/ReviewConsole";
import { HistoryPage } from "./pages/History";
import { ReviewDetail } from "./pages/ReviewDetail";
import { ApprovalQueue } from "./pages/ApprovalQueue";
import { TelemetryPage } from "./pages/Telemetry";
import { IngestPage } from "./pages/Ingest";
import { EvalPage } from "./pages/Eval";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<ReviewConsole />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/reviews/:id" element={<ReviewDetail />} />
        <Route path="/approvals" element={<ApprovalQueue />} />
        <Route path="/telemetry" element={<TelemetryPage />} />
        <Route path="/ingest" element={<IngestPage />} />
        <Route path="/eval" element={<EvalPage />} />
      </Routes>
    </Layout>
  );
}
