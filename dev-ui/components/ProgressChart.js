"use client";

import { useState } from "react";
import { sectionName } from "@/lib/sections";

// Geometry. Right padding is wide enough for the direct labels, which are required rather than
// decorative: two of the four series sit below 3:1 contrast on the light surface, so identity
// must not rest on colour alone.
const W = 720;
const H = 280;
const PAD = { top: 14, right: 104, bottom: 30, left: 38 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;
const GRID = [0, 25, 50, 75, 100];

const yOf = (value) => PAD.top + PLOT_H * (1 - Math.max(0, Math.min(100, value)) / 100);
const xOf = (index, count) =>
  count <= 1 ? PAD.left + PLOT_W / 2 : PAD.left + (PLOT_W * index) / (count - 1);

export default function ProgressChart({ sections }) {
  const [hover, setHover] = useState(null);

  // X domain is the union of every section's cycles, so a section that missed a cycle still
  // lines up with the others rather than being silently compressed.
  const cycles = [
    ...new Set((sections || []).flatMap((s) => s.points.map((p) => p.cycle_version))),
  ].sort((a, b) => a - b);

  if (!cycles.length) {
    return (
      <p className="cap" style={{ margin: "10px 0 0" }}>
        No tests evaluated yet — the chart appears once you finish your first test.
      </p>
    );
  }

  const series = (sections || [])
    .filter((s) => s.points.length)
    .map((s, i) => {
      const byCycle = new Map(s.points.map((p) => [p.cycle_version, p]));
      return {
        section: s.section,
        slot: i + 1,
        points: cycles
          .map((c, index) => {
            const point = byCycle.get(c);
            return point ? { ...point, x: xOf(index, cycles.length), y: yOf(point.progress_score) } : null;
          })
          .filter(Boolean),
      };
    })
    .filter((s) => s.points.length);

  // Direct labels are anchored to each line's last point, so two sections finishing on similar
  // scores would overprint each other. Push them apart vertically, keeping their vertical order,
  // then lift the whole stack if it has run past the bottom of the plot.
  const LABEL_GAP = 15;
  const labels = series
    .map((s) => {
      const last = s.points[s.points.length - 1];
      return { section: s.section, slot: s.slot, anchorX: last.x, anchorY: last.y, y: last.y };
    })
    .sort((a, b) => a.anchorY - b.anchorY);
  for (let i = 1; i < labels.length; i++) {
    labels[i].y = Math.max(labels[i].y, labels[i - 1].y + LABEL_GAP);
  }
  const overflow = labels.length ? labels[labels.length - 1].y - (PAD.top + PLOT_H) : 0;
  if (overflow > 0) {
    labels.forEach((l) => {
      l.y -= overflow;
    });
    for (let i = labels.length - 2; i >= 0; i--) {
      labels[i].y = Math.min(labels[i].y, labels[i + 1].y - LABEL_GAP);
    }
  }

  function onMove(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * W;
    let nearest = 0;
    cycles.forEach((_, i) => {
      if (Math.abs(xOf(i, cycles.length) - x) < Math.abs(xOf(nearest, cycles.length) - x)) {
        nearest = i;
      }
    });
    setHover(nearest);
  }

  const hoverCycle = hover === null ? null : cycles[hover];

  return (
    <div className="chartwrap">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="progresschart"
        role="img"
        aria-label="Per-section progress score across tests"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {GRID.map((value) => (
          <g key={value}>
            <line
              className="cgrid"
              x1={PAD.left}
              x2={PAD.left + PLOT_W}
              y1={yOf(value)}
              y2={yOf(value)}
            />
            <text className="ctick" x={PAD.left - 8} y={yOf(value) + 4} textAnchor="end">
              {value}
            </text>
          </g>
        ))}

        {cycles.map((cycle, i) => (
          <text
            key={cycle}
            className="ctick"
            x={xOf(i, cycles.length)}
            y={H - 10}
            textAnchor="middle"
          >
            T{cycle}
          </text>
        ))}

        {hover !== null && (
          <line
            className="ccross"
            x1={xOf(hover, cycles.length)}
            x2={xOf(hover, cycles.length)}
            y1={PAD.top}
            y2={PAD.top + PLOT_H}
          />
        )}

        {series.map((s) => (
          <g key={s.section} className={`cs cs-${s.slot}`}>
            {s.points.length > 1 && (
              <polyline
                className="cline"
                points={s.points.map((p) => `${p.x},${p.y}`).join(" ")}
              />
            )}
            {s.points.map((p) => (
              // A single-point series draws nothing as a polyline, so every point gets a marker.
              <circle
                key={p.cycle_version}
                className="cdot"
                cx={p.x}
                cy={p.y}
                r={hover !== null && cycles[hover] === p.cycle_version ? 5 : 4}
              />
            ))}
          </g>
        ))}

        {/* Direct labels, de-collided. Required, not decorative: two series fall below 3:1 on the
            light surface, so identity must not rest on colour alone. The swatch carries identity;
            the text stays in an ink token. A leader line connects a label that had to move. */}
        {labels.map((l) => (
          <g key={l.section} className={`cs cs-${l.slot}`}>
            {Math.abs(l.y - l.anchorY) > 1 && (
              <line
                className="cleader"
                x1={l.anchorX + 4}
                y1={l.anchorY}
                x2={l.anchorX + 12}
                y2={l.y}
              />
            )}
            <circle className="cdot" cx={l.anchorX + 14} cy={l.y} r={3.5} />
            <text className="clabel" x={l.anchorX + 22} y={l.y + 4}>
              {sectionName(l.section)}
            </text>
          </g>
        ))}
      </svg>

      <div className="clegend">
        {series.map((s) => (
          <span key={s.section} className={`cs-${s.slot}`}>
            <i />
            {sectionName(s.section)}
          </span>
        ))}
      </div>

      {hoverCycle !== null && (
        // Sits opposite the hovered test so it never covers the point being read.
        <div className={`ctip ${hover > (cycles.length - 1) / 2 ? "left" : "right"}`}>
          <strong>Test {hoverCycle}</strong>
          {series.map((s) => {
            const point = s.points.find((p) => p.cycle_version === hoverCycle);
            if (!point) return null;
            return (
              <div key={s.section} className={`cs-${s.slot}`}>
                <i />
                {sectionName(s.section)}: <b>{Math.round(point.progress_score)}</b>{" "}
                <span className="cmeta">
                  (L{point.current_level} · raw {Math.round(point.raw_score)})
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
