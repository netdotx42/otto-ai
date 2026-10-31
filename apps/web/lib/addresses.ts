const EVM = /0x[0-9a-fA-F]{40}/g;
const SOL = /\b[1-9A-HJ-NP-Za-km-z]{32,44}\b/g;

/** Mirrors apps/api/app/execution/intent.py:detect_addresses for live highlighting. */
export function detectAddresses(text: string) {
  const evm = text.match(EVM) ?? [];
  const sol = (text.replace(EVM, " ").match(SOL) ?? []).filter((a) => !/^[A-Za-z]+$/.test(a));
  return { evm, sol };
}

export const isEvmAddress = (a: string) => /^0x[0-9a-fA-F]{40}$/.test(a.trim());
export const isSolAddress = (a: string) => /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a.trim());
