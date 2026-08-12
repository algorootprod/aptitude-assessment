"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { getUserId, setLastReport } from "@/lib/session";
import { sectionName } from "@/lib/sections";
import { formatClock } from "@/lib/format";
import ThemeToggle from "@/components/ThemeToggle";

// Fills in `unreached` for any question in the ending section that never got an
// answer entry — either the section clock ran out, or (defensively) it was
// never reached for some other reason. Questions already answered are untouched.
function endSection(prev, expired) {
  const S = prev.testData.sections[prev.si];
  const answers = { ...prev.answers };
  S.questions.forEach((q) => {
    if (!answers[q.id]) {
      answers[q.id] = { question_id: q.id, picked: null, elapsed_seconds: 0, unreached: true };
    }
  });
  const timeUsed = expired
    ? S.budget_seconds
    : Math.round((Date.now() - prev.secStart) / 1000);
  const secUsed = { ...prev.secUsed, [S.section]: timeUsed };
  return { ...prev, phase: "transition", answers, secUsed, expired };
}

function beginSection(prev, index) {
  const S = prev.testData.sections[index];
  const now = Date.now();
  return {
    ...prev,
    phase: "question",
    si: index,
    qi: 0,
    selected: null,
    tLeft: S.budget_seconds,
    secStart: now,
    qStart: now,
  };
}

function commitAnswer(prev, picked) {
  const S = prev.testData.sections[prev.si];
  const q = S.questions[prev.qi];
  const elapsed = Math.max(0, Math.round((Date.now() - prev.qStart) / 1000));
  const answers = {
    ...prev.answers,
    [q.id]: { question_id: q.id, picked, elapsed_seconds: elapsed, unreached: false },
  };
  if (prev.qi < S.questions.length - 1) {
    return { ...prev, answers, qi: prev.qi + 1, selected: null, qStart: Date.now() };
  }
  return endSection({ ...prev, answers }, false);
}

export default function TestPage() {
  const router = useRouter();
  const [userId, setUserId] = useState("");
  const [st, setSt] = useState({ phase: "loading" });
  const [loadError, setLoadError] = useState(null);
  const [submitError, setSubmitError] = useState(null);

  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/");
      return;
    }
    setUserId(uid);
    api
      .startTest(uid)
      .then((testData) => {
        // Guard against React Strict Mode's double-invoked mount effect: if the
        // test has already moved past "loading" (the user started it before this
        // resolved a second time), don't clobber their progress.
        setSt((prev) =>
          prev.phase !== "loading"
            ? prev
            : {
                phase: "intro",
                testData,
                si: 0,
                qi: 0,
                selected: null,
                tLeft: 0,
                secStart: 0,
                qStart: 0,
                answers: {},
                secUsed: {},
              },
        );
      })
      .catch((err) => setLoadError(err.message));
  }, [router]);

  // Section clock — one interval per section, alive only while a question is showing.
  useEffect(() => {
    if (st.phase !== "question") return;
    const id = setInterval(() => {
      setSt((prev) => {
        if (prev.phase !== "question") return prev;
        const nextLeft = prev.tLeft - 1;
        if (nextLeft <= 0) return endSection(prev, true);
        return { ...prev, tLeft: nextLeft };
      });
    }, 1000);
    return () => clearInterval(id);
  }, [st.phase, st.si]);

  async function submitTest(testData, answers, secUsed) {
    setSt((prev) => ({ ...prev, phase: "submitting" }));
    setSubmitError(null);
    try {
      const payload = {
        user_id: userId,
        cycle_version: testData.cycle_version,
        answers: Object.values(answers),
        sections: testData.sections.map((S) => ({
          section: S.section,
          time_used_seconds: secUsed[S.section] ?? 0,
        })),
      };
      const report = await api.completeTest(payload);
      setLastReport(report);
      router.push("/report");
    } catch (err) {
      setSubmitError(err.message);
      setSt((prev) => ({ ...prev, phase: "transition" }));
    }
  }

  if (loadError) {
    return (
      <section className="wrap">
        <div className="topbar">
          <h1>Couldn&apos;t start the test</h1>
          <ThemeToggle />
        </div>
        <div className="errbox">{loadError}</div>
        <button className="ghost" onClick={() => router.push("/dashboard")}>
          ← Back to dashboard
        </button>
      </section>
    );
  }

  if (st.phase === "loading") {
    return (
      <section className="wrap">
        <div className="card">
          <p className="muted">Loading your test…</p>
        </div>
      </section>
    );
  }

  const { testData } = st;

  if (st.phase === "intro") {
    const totalBudget = testData.sections.reduce((s, sec) => s + sec.budget_seconds, 0);
    return (
      <section className="wrap">
        <div className="topbar">
          <div>
            <h1>Daily 20</h1>
            <p className="muted">
              {testData.sections.length} sections · {testData.sections.reduce((n, s) => n + s.questions.length, 0)} questions total.
            </p>
          </div>
          <ThemeToggle />
        </div>
        <div className="card">
          <h2>Before you start</h2>
          <div className="plan">
            {testData.sections.map((S) => (
              <div className="pl" key={S.section}>
                <div className="n">{sectionName(S.section)}</div>
                <div className="t">
                  {S.questions.length} questions · {formatClock(S.budget_seconds)}
                </div>
              </div>
            ))}
          </div>
          <ul className="rules">
            <li>
              <b>Each section is timed on its own.</b> Spare time in one section does not carry
              into the next.
            </li>
            <li>
              <b>Answers lock immediately.</b> Once you confirm a question you cannot return to
              it — so commit before you move on.
            </li>
            <li>
              <b>No negative marking.</b> A blank scores the same as a wrong answer, so never
              leave one.
            </li>
            <li>
              <b>You are not being graded.</b> This is a diagnostic — the report afterwards is
              the point.
            </li>
          </ul>
          <div className="row">
            <button className="cta" onClick={() => setSt((prev) => beginSection(prev, 0))}>
              Start — {formatClock(totalBudget)}
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (st.phase === "question") {
    const S = testData.sections[st.si];
    const q = S.questions[st.qi];
    const letters = ["A", "B", "C", "D"];
    const clockClass =
      st.tLeft <= 20 ? "clock crit" : st.tLeft <= 60 ? "clock warn" : "clock";
    return (
      <section>
        <div className="bar">
          <div className="barin">
            <span className="sname">{sectionName(S.section)}</span>
            <span className="dots">
              {S.questions.map((_, i) => (
                <span
                  key={i}
                  className={`dot ${i < st.qi ? "done" : ""} ${i === st.qi ? "now" : ""}`}
                />
              ))}
            </span>
            <span className="qcount">
              {st.qi + 1} of {S.questions.length}
            </span>
            <div style={{ marginLeft: "auto", textAlign: "right" }}>
              <div className={clockClass}>{formatClock(st.tLeft)}</div>
              <div className="clocklab">Section time</div>
            </div>
          </div>
        </div>
        <div className="wrap wide">
          {S.direction && (
            <div className="direction">
              <p>{S.direction}</p>
              {S.chart_svg && (
                <div
                  className="chartbox"
                  dangerouslySetInnerHTML={{ __html: S.chart_svg }}
                />
              )}
            </div>
          )}
          <div className="card">
            <h5>{q.topic}</h5>
            <p className="qtext">{q.question_text}</p>
            <div>
              {q.options.map((opt, i) => (
                <button
                  key={letters[i]}
                  className={`opt ${st.selected === letters[i] ? "sel" : ""}`}
                  onClick={() => setSt((prev) => ({ ...prev, selected: letters[i] }))}
                >
                  <span className="k">{letters[i]}</span>
                  {opt}
                </button>
              ))}
            </div>
            <div className="foot">
              <button
                className="cta"
                disabled={!st.selected}
                onClick={() => setSt((prev) => commitAnswer(prev, prev.selected))}
              >
                Lock answer →
              </button>
              <button className="ghost" onClick={() => setSt((prev) => commitAnswer(prev, null))}>
                Skip
              </button>
              <span className="warnnote">
                Locking is final.
                <br />
                You cannot come back to this question.
              </span>
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (st.phase === "transition" || st.phase === "submitting") {
    const S = testData.sections[st.si];
    const used = st.secUsed[S.section] || 0;
    const isLast = st.si === testData.sections.length - 1;
    return (
      <section className="wrap">
        <div className="card tr">
          <div className="big">{sectionName(S.section)} complete</div>
          <p className="muted" style={{ margin: 0 }}>
            {st.expired
              ? "Time ran out. Any questions you did not reach are recorded as unattempted."
              : "This section is now locked."}
          </p>
          <div className="trstat">
            <div>
              <div className="v">{formatClock(used)}</div>
              <div className="l">Time used</div>
            </div>
            <div>
              <div className="v">{formatClock(Math.max(S.budget_seconds - used, 0))}</div>
              <div className="l">Unused</div>
            </div>
          </div>
          {submitError && <div className="errbox">{submitError}</div>}
          <button
            className="cta"
            disabled={st.phase === "submitting"}
            onClick={() => {
              if (isLast) {
                submitTest(testData, st.answers, st.secUsed);
              } else {
                setSt((prev) => beginSection(prev, prev.si + 1));
              }
            }}
          >
            {st.phase === "submitting"
              ? "Submitting…"
              : isLast
                ? "See your report →"
                : `Start ${sectionName(testData.sections[st.si + 1].section)} → ${formatClock(testData.sections[st.si + 1].budget_seconds)}`}
          </button>
        </div>
      </section>
    );
  }

  return null;
}
