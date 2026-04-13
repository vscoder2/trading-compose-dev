const PROFILE_LABELS: Record<string, string> = {
  aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m: "Semiconductor Momentum Pro",
  aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m_switch_v1: "Semiconductor Momentum Pro",
};

function _titleCaseVariant(variant: string): string {
  const normalized = String(variant || "").replace(/[_-]+/g, " ").trim();
  if (!normalized) return "Baseline";
  return normalized
    .split(" ")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1).toLowerCase() : ""))
    .join(" ");
}

export function toFriendlyProfileName(profile: string): string {
  const raw = String(profile || "").trim();
  if (!raw) return "Runtime Strategy";
  if (PROFILE_LABELS[raw]) return PROFILE_LABELS[raw];
  for (const [id, label] of Object.entries(PROFILE_LABELS)) {
    if (raw.includes(id)) {
      return raw.replaceAll(id, label);
    }
  }
  return raw;
}

export function toFriendlyStrategyLabel(strategy: string): string {
  const raw = String(strategy || "").trim();
  if (!raw) return "Runtime Strategy";
  const variantMatch = raw.match(/\|\s*variant\s*=\s*([A-Za-z0-9_-]+)/i);
  if (!variantMatch) return toFriendlyProfileName(raw);
  const variant = _titleCaseVariant(variantMatch[1]);
  const base = raw.replace(variantMatch[0], "").trim();
  return `${toFriendlyProfileName(base)} (${variant})`;
}

