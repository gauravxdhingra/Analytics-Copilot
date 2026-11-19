// The backend wraps text that came from outside the company as <untrusted source="...">...</untrusted>
// so that the language model treats it as data. People should see the text, not the wrapper.

const WRAPPED = /^<untrusted source="[^"]*">([\s\S]*)<\/untrusted>$/;

export function unwrapUntrusted(cell: unknown): { text: string; external: boolean } {
  if (typeof cell === "string") {
    const match = WRAPPED.exec(cell);
    if (match) return { text: match[1] ?? "", external: true };
  }
  return { text: cell === null || cell === undefined ? "" : String(cell), external: false };
}
