// Mirrors reference/daily20_prototype.html's QUAD table — the server computes
// which bucket each question falls into (app/modules/evaluation_report/scoring.py),
// this just maps that label to a badge tone/icon for display.
export const QUAD = {
  mastered: { label: "Mastered", tone: "good", icon: "✓" },
  fragile: { label: "Fragile", tone: "warning", icon: "◷" },
  careless: { label: "Careless", tone: "serious", icon: "!" },
  gap: { label: "Gap", tone: "critical", icon: "×" },
  unreached: { label: "Not reached", tone: "critical", icon: "—" },
};

export function quadInfo(quadrant) {
  return QUAD[quadrant] || { label: quadrant, tone: "warning", icon: "?" };
}

export const FINDING_ICON = { good: "✓", warning: "◷", serious: "!", critical: "×" };

// The app-wide quadrant score. Source of truth is QUADRANT_MASTERY_SCORE in
// app/core/constants.py — the backend averages the same table for `mastery_score` and the DI
// ladder signal, so keep the two in step. Averaging it is the same arithmetic as the prototype's
// (mastered + 0.5 x fragile) / n, written as a table so a change on either side is visible here.
export const QUADRANT_SCORE = {
  mastered: 100,
  fragile: 50,
  careless: 0,
  gap: 0,
  unreached: 0,
};

/** Mean QUADRANT_SCORE over a list of quadrants, 0-100. An empty list scores 0 — nothing
 *  attempted is not evidence of mastery (matching `di_section_score`). */
export function scoreOf(quadrants) {
  if (!quadrants.length) return 0;
  const total = quadrants.reduce((sum, q) => sum + (QUADRANT_SCORE[q] ?? 0), 0);
  return Math.round(total / quadrants.length);
}
