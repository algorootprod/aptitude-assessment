export const SECTION_NAMES = {
  di: "Data Interpretation",
  quant: "Quantitative",
  reasoning: "Reasoning",
  english: "English",
};

export const SECTION_ORDER = ["di", "quant", "reasoning", "english"];

export function sectionName(key) {
  return SECTION_NAMES[key] || key;
}
