import { useGlossary } from "../../data/glossary-context";

/**
 * Wraps a canonical field name with a dotted underline + hover tooltip showing
 * the original field name, Chinese label, and caliber definition.
 * Usage: <GlossaryTerm field="open_cost">开仓成本</GlossaryTerm>
 * If no children given, renders the Chinese label from the glossary.
 */
export function GlossaryTerm({ field, children }: { field: string; children?: React.ReactNode }) {
  const lookup = useGlossary();
  const entry = lookup(field);
  const label = children ?? entry?.zh ?? field;
  const title = entry
    ? `${field} · ${entry.zh}\n${entry.definition}\n来源: ${entry.source_doc}`
    : field;
  return (
    <span className="glossary-term" title={title}>
      {label}
    </span>
  );
}
