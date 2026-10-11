import type { WorkOrder } from "../api/types";
import MaintenanceRequest from "./MaintenanceRequest";

/** A work order in a list or an answer: the maintenance request, collapsed to its headline until opened. */
export default function WorkOrderCard({ w, onDone }: { w: WorkOrder; onDone: () => void }) {
  return <MaintenanceRequest w={w} onDone={onDone} compact />;
}
