const USER_ID_KEY = "aptitude:user_id";
const LAST_REPORT_KEY = "aptitude:last_report";

export function getUserId() {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(USER_ID_KEY) || "";
}

export function setUserId(userId) {
  window.sessionStorage.setItem(USER_ID_KEY, userId);
}

export function getLastReport() {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(LAST_REPORT_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setLastReport(report) {
  window.sessionStorage.setItem(LAST_REPORT_KEY, JSON.stringify(report));
}

export function clearLastReport() {
  window.sessionStorage.removeItem(LAST_REPORT_KEY);
}
