"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { getUserId, setUserId, clearLastReport } from "@/lib/session";
import ThemeToggle from "@/components/ThemeToggle";

export default function SignupPage() {
  const router = useRouter();
  const [userId, setUserIdInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const existing = getUserId();
    if (existing) setUserIdInput(existing);
  }, []);

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = userId.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.signup(trimmed);
      setUserId(trimmed);
      clearLastReport();
      router.push("/dashboard");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="wrap">
      <div className="topbar">
        <div>
          <h1>Daily 20</h1>
          <p className="muted">
            Four sections, five questions each. Sign up (or continue) with a candidate id to get a
            diagnostic aptitude test.
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div className="card">
        <h2>Enter a candidate id</h2>
        {error && <div className="errbox">{error}</div>}
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="user_id">user_id</label>
            <input
              id="user_id"
              type="text"
              value={userId}
              onChange={(e) => setUserIdInput(e.target.value)}
              placeholder="e.g. test-candidate-1"
              autoComplete="off"
              autoFocus
            />
          </div>
          <div className="row" style={{ marginTop: 0 }}>
            <button className="cta" type="submit" disabled={!userId.trim() || busy}>
              {busy ? "Signing up…" : "Continue →"}
            </button>
          </div>
        </form>
        <p className="cap" style={{ marginTop: 16 }}>
          Signup is idempotent — re-entering an existing id just takes you back into their current
          cycle, it never resets progress.
        </p>
      </div>
    </section>
  );
}
