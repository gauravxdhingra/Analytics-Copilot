import { PROVENANCE_LABEL } from "../lib/copy";
import type { Claim } from "../lib/types";

const TONE: Record<string, string> = {
  system_state: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  document: "bg-sky-50 text-sky-800 ring-sky-200",
  inference: "bg-amber-50 text-amber-800 ring-amber-200",
  unknown: "bg-rose-50 text-rose-800 ring-rose-200",
};

/** Every claim carries evidence; the chip says where it came from and how much to trust it. */
export function EvidenceChips({ claims }: { claims: Claim[] }) {
  const evidence = claims.flatMap((c) => c.evidence);
  if (evidence.length === 0) return null;
  return (
    <ul className="mt-3 flex flex-wrap gap-2" aria-label="Evidence">
      {evidence.map((e) => (
        <li
          key={e.evidence_id}
          className={`rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset ${TONE[e.provenance] ?? TONE.unknown}`}
          title={e.source_tool ?? undefined}
        >
          {PROVENANCE_LABEL[e.provenance] ?? "Unverified"} · {e.source_ref}
          {e.snapshot_at ? ` · ${e.snapshot_at.slice(0, 10)}` : ""}
        </li>
      ))}
    </ul>
  );
}
