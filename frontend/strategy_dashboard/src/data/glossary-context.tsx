import { createContext, useContext, useEffect, useState } from "react";
import { weatherApi } from "./weather-http";
import type { GlossaryEntry } from "./v2-types";

type Lookup = (field: string) => GlossaryEntry | undefined;

const GlossaryContext = createContext<Lookup>(() => undefined);

export function GlossaryProvider({ children }: { children: React.ReactNode }) {
  const [fields, setFields] = useState<Record<string, GlossaryEntry>>({});
  useEffect(() => {
    weatherApi.getGlossary().then((r) => setFields(r.fields)).catch(() => setFields({}));
  }, []);
  const lookup: Lookup = (field) => fields[field];
  return <GlossaryContext.Provider value={lookup}>{children}</GlossaryContext.Provider>;
}

export function useGlossary(): Lookup {
  return useContext(GlossaryContext);
}
