import { PageFrame } from "../../components/PageFrame";
import { PlatformOperationsPanel } from "../../components/PlatformOperationsPanel";

export function WeatherOperationsPage() {
  return <PageFrame title="Platform Operations" desc="Durable control-ledger timeline · 只读，不执行操作"><PlatformOperationsPanel /></PageFrame>;
}
