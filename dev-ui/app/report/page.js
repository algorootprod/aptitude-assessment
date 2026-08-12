"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { getUserId, getLastReport } from "@/lib/session";
import { SECTION_ORDER, sectionName } from "@/lib/sections";
import { formatClock } from "@/lib/format";
import { QUAD, quadInfo, scoreOf, FINDING_ICON } from "@/lib/quadrants";
import ThemeToggle from "@/components/ThemeToggle";

export default function ReportPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/");
      return;
    }
    setUserId(uid);

    const cached = getLastReport();
    if (cached && cached.user_id === uid) {
      setReport(cached);
      setLoading(false);
      return;
    }

    api
      .getReport(uid)
      .then(setReport)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [router]);

  if (loading) {
    return (
      <section className="wrap">
        <div className="card">
          <p className="muted">Loading report…</p>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="wrap">
        <div className="topbar">
          <h1>No report yet</h1>
          <ThemeToggle />
        </div>
        <div className="errbox">{error}</div>
        <div className="row">
          <button className="cta" onClick={() => router.push("/test")}>
            Take the test →
          </button>
          <button className="ghost" onClick={() => router.push("/dashboard")}>
            ← Back to dashboard
          </button>
        </div>
      </section>
    );
  }

  const bySection = {};
  (report.questions || []).forEach((q) => {
    (bySection[q.section] = bySection[q.section] || []).push(q);
  });

  // Only the section level carries enough questions to mean anything: 5 each, against a single
  // question for most topics, where a score could only ever read 0 or 100.
  const MIX_ORDER = ["mastered", "fragile", "careless", "gap", "unreached"];
  const sectionScores = SECTION_ORDER.filter((s) => bySection[s]).map((section) => {
    const rows = bySection[section];
    // Pace over *attempted* questions only. An unreached question contributes nothing to the
    // numerator but would still carry its full expected time in the denominator, so counting it
    // makes running out of time look like good pacing — backwards.
    const attempted = rows.filter((q) => q.quadrant !== "unreached");
    const expected = attempted.reduce((s, q) => s + q.expected_time_seconds, 0);
    const elapsed = attempted.reduce((s, q) => s + q.elapsed_seconds, 0);
    return {
      section,
      name: sectionName(section),
      score: scoreOf(rows.map((q) => q.quadrant)),
      correct: rows.filter((q) => q.is_correct).length,
      total: rows.length,
      pace: expected ? Math.round((elapsed / expected) * 100) : 0,
      mix: MIX_ORDER.map((k) => ({
        quadrant: k,
        count: rows.filter((q) => q.quadrant === k).length,
      })).filter((m) => m.count),
    };
  });

  // The prototype closed this block with "Tomorrow's set will over-sample your weaker topics."
  // `evaluation_report/report.py` deliberately drops that line and we follow suit: topic rotation
  // is strict round-robin and ignores performance entirely — only a topic's *level* moves — so it
  // is a promise the service does not keep. Describe the list instead of predicting the next test.
  const actionsNote = report.actions.length
    ? "Ordered by what is costing you most — knowledge gaps first, then speed."
    : "Nothing to prioritise — keep the streak going.";

  return (
    <section className="wrap wide">
      <div className="topbar">
        <div>
          <h1>What just happened</h1>
          <p className="muted" style={{ fontSize: 17, color: "var(--ink)" }}>
            {report.headline}
          </p>
          <p className="cap">
            {userId} · cycle {report.cycle_version}
          </p>
        </div>
        <ThemeToggle />
      </div>

      <div className="card">
        <h2>Section scores</h2>
        <p className="muted" style={{ marginBottom: 12 }}>
          Scored per section, not per topic — five questions a section is enough to mean
          something, whereas most topics get a single question, where a score could only ever
          read 0 or 100.
        </p>
        <div className="scores">
          {sectionScores.map((s) => (
            <div className="sc" key={s.section}>
              <div className="top">
                <span className="nm">{s.name}</span>
                <span className="val">{s.score}</span>
              </div>
              <div className="mix">
                {s.mix.map((m) => (
                  <span
                    key={m.quadrant}
                    className={`m-${m.quadrant}`}
                    style={{ flex: m.count }}
                    title={`${quadInfo(m.quadrant).label}: ${m.count}`}
                  />
                ))}
              </div>
              <div className="kv">
                <span>
                  Accuracy{" "}
                  <b>
                    {s.correct}/{s.total}
                  </b>
                </span>
                <span>
                  Pace <b className={s.pace > 100 ? "over" : ""}>{s.pace}%</b>
                </span>
              </div>
            </div>
          ))}
        </div>
        <div className="slegend">
          {["mastered", "fragile", "careless", "gap"].map((k) => (
            <span key={k}>
              <i className={`m-${k}`} />
              {QUAD[k].label}
            </span>
          ))}
        </div>
        <p className="cap" style={{ marginTop: 14 }}>
          <b>Score</b> = questions answered correctly <i>and</i> inside their time budget, with
          half credit where the answer was right but slow — the method is there, the clock isn&apos;t.{" "}
          <b>Pace</b> is time spent against time budgeted on the questions you reached, so above
          100% means you ran over.
        </p>
      </div>

      <div className="card">
        <h5>Every question, classified</h5>
        <div className="tiles">
          {report.tiles.map((t) => (
            <div className={`tile t-${t.tone}`} key={t.quadrant}>
              <div className="tnum">{t.count}</div>
              <div className="tlab">{t.label}</div>
              <p>{t.blurb}</p>
            </div>
          ))}
        </div>
        <table>
          <thead>
            <tr>
              <th>Section</th>
              <th>Right</th>
              <th>Clock</th>
              <th>Read</th>
            </tr>
          </thead>
          <tbody>
            {report.section_table.map((row) => {
              const pct = Math.min((row.time_used_seconds / row.budget_seconds) * 100, 100);
              return (
                <tr key={row.section}>
                  <td>
                    <b>{row.section_name}</b>
                  </td>
                  <td className="n">
                    {row.correct}/{row.total}
                  </td>
                  <td>
                    <div className="mtrack">
                      <span className="mbar" style={{ width: `${pct.toFixed(0)}%` }} />
                    </div>
                    <span className="mtxt">
                      {formatClock(row.time_used_seconds)} of {formatClock(row.budget_seconds)}
                    </span>
                  </td>
                  <td style={{ fontSize: "12.8px" }}>{row.note}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="cap" style={{ marginTop: 14 }}>
          Five questions a section is too few to call a topic strong or weak on its own. These
          verdicts are about individual questions; topic-level mastery builds up over repeated
          sessions.
        </p>
      </div>

      <div className="card">
        <h2>What stands out</h2>
        <p className="muted" style={{ marginBottom: 4 }}>
          Patterns across the whole paper — not a list of mistakes.
        </p>
        <ul className="finds">
          {report.findings.length ? (
            report.findings.map((f, i) => (
              <li key={i}>
                <span className={`fico f-${f.tone}`}>{FINDING_ICON[f.tone] || "•"}</span>
                <div>
                  <h4>{f.heading}</h4>
                  <p>{f.detail}</p>
                </div>
              </li>
            ))
          ) : (
            <li>
              <div>
                <h4>Nothing stands out</h4>
                <p>Come back tomorrow for a clearer picture.</p>
              </div>
            </li>
          )}
        </ul>
      </div>

      <div className="card">
        <h2>Do these next</h2>
        <p className="muted" style={{ marginBottom: 4 }}>
          {actionsNote}
        </p>
        <ol className="act">
          {report.actions.map((a, i) => (
            <li key={i}>
              <h4>{a.heading}</h4>
              <p>{a.detail}</p>
              <div className="tagline">{a.tag}</div>
            </li>
          ))}
        </ol>
      </div>

      <div className="card">
        <h2>Every question</h2>
        <p className="muted">
          Open any one for the worked method, why your option was tempting, and the faster
          route.
        </p>
        {SECTION_ORDER.filter((s) => bySection[s]).map((section) => (
          <div key={section}>
            <h3 style={{ margin: "20px 0 9px" }}>{sectionName(section)}</h3>
            {bySection[section].map((q) => {
              const Q = quadInfo(q.quadrant);
              const yours =
                q.picked === null ? (
                  <span className="tag bad">
                    {q.quadrant === "unreached" ? "not reached" : "skipped"}
                  </span>
                ) : (
                  <span className={`tag ${q.is_correct ? "ok" : "bad"}`}>
                    you chose {q.picked}
                  </span>
                );
              return (
                <details className="q" key={q.question_id}>
                  <summary>
                    <span className={`badge b-${Q.tone}`}>{Q.label}</span>
                    <span className="stitle">
                      {q.question_text.split("\n")[0].slice(0, 72)}…
                    </span>
                    <span className="stime">
                      {q.elapsed_seconds}s / {q.expected_time_seconds}s
                    </span>
                  </summary>
                  <div className="qbody">
                    <p className="yours">
                      {yours}{" "}
                      {q.correct_option && (
                        <span className="tag ok">
                          correct: {q.correct_option} —{" "}
                          {q.options[["A", "B", "C", "D"].indexOf(q.correct_option)]}
                        </span>
                      )}
                    </p>
                    {q.explanation && (
                      <>
                        <h5>Explanation</h5>
                        <p className="x">{q.explanation}</p>
                      </>
                    )}
                    {!q.is_correct && q.picked && q.distractor_rationale && (
                      <>
                        <h5>Why that option looked right</h5>
                        <p className="x">{q.distractor_rationale}</p>
                      </>
                    )}
                    {q.shortcut_name && q.quadrant !== "mastered" && (
                      <div className="short">
                        <h6>
                          ⚡ {q.shortcut_name}
                          {q.shortcut_saves_seconds != null && (
                            <span>saves ~{q.shortcut_saves_seconds}s</span>
                          )}
                        </h6>
                        <p>{q.shortcut_how}</p>
                      </div>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        ))}
        <div className="row">
          <button className="ghost" onClick={() => router.push("/dashboard")}>
            ← Back to dashboard
          </button>
        </div>
      </div>
    </section>
  );
}
