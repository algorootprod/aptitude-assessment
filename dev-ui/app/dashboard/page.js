"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { getUserId, clearLastReport } from "@/lib/session";
import { SECTION_NAMES, SECTION_ORDER } from "@/lib/sections";
import ThemeToggle from "@/components/ThemeToggle";

function LevelDots({ level }) {
  return (
    <span className="lvl">
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} className={n <= level ? "on" : ""} />
      ))}
    </span>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/");
      return;
    }
    setUserId(uid);
    api
      .signup(uid)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [router]);

  const bySection = {};
  (data?.topics || []).forEach((t) => {
    (bySection[t.section] = bySection[t.section] || []).push(t);
  });

  return (
    <section className="wrap">
      <div className="topbar">
        <div>
          <h1>{userId || "…"}</h1>
          <p className="muted">
            {data ? `Cycle ${data.cycle_version} · ${data.topics.length} topics tracked` : "Loading…"}
          </p>
        </div>
        <ThemeToggle />
      </div>

      {error && <div className="errbox">{error}</div>}

      <div className="card">
        <h2>What to do</h2>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="cta" onClick={() => router.push("/test")}>
            Start Test →
          </button>
          <button
            className="ghost"
            onClick={() => {
              clearLastReport();
              router.push("/report");
            }}
          >
            View Reports →
          </button>
          <button className="ghost" onClick={() => router.push("/")}>
            Switch candidate
          </button>
        </div>
      </div>

      {!loading && data && (
        <div className="card">
          <h5>Topic levels</h5>
          <p className="cap" style={{ marginBottom: 12 }}>
            Level 1–5 per topic, seeded at 2 and moved by the ladder after each evaluation.
          </p>
          {SECTION_ORDER.filter((s) => bySection[s]).map((section) => (
            <div key={section} style={{ marginBottom: 16 }}>
              <h3>{SECTION_NAMES[section] || section}</h3>
              <div className="topics">
                {bySection[section]
                  .sort((a, b) => a.topic.localeCompare(b.topic))
                  .map((t) => (
                    <div className="topicrow" key={t.topic}>
                      <div className="tn">
                        {t.topic} <LevelDots level={t.current_level} />
                      </div>
                      <div className="tm">
                        level {t.current_level}
                        {t.pending_dir !== 0 &&
                          ` · probation ${t.pending_dir > 0 ? "↑" : "↓"}`}
                        {" · streak "}
                        {t.streak}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
