const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8090/v1";

class ApiError extends Error {
  constructor(status, body) {
    super(
      typeof body === "string"
        ? body
        : body?.detail
          ? String(body.detail)
          : `Request failed with status ${status}`,
    );
    this.status = status;
    this.body = body;
  }
}

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(BASE_URL + path, opts);
  } catch (err) {
    throw new ApiError(0, `Could not reach the API at ${BASE_URL} — is it running? (${err.message})`);
  }
  const text = await response.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!response.ok) {
    throw new ApiError(response.status, parsed);
  }
  return parsed;
}

export const api = {
  baseUrl: BASE_URL,
  health: () => request("GET", "/health"),
  signup: (userId) => request("POST", "/users/signup", { user_id: userId }),
  startTest: (userId) => request("POST", "/tests/start", { user_id: userId }),
  completeTest: (payload) => request("POST", "/tests/complete", payload),
  progressHistory: (userId, tests = 10) =>
    request("GET", `/progress/${encodeURIComponent(userId)}?tests=${tests}`),
  getReport: (userId, cycleVersion) =>
    request(
      "GET",
      `/reports/${encodeURIComponent(userId)}${cycleVersion ? `?cycle_version=${encodeURIComponent(cycleVersion)}` : ""}`,
    ),
};

export { ApiError };
