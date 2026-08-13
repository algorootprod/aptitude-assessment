"""
Verifies the daily20_questions table in Neon after you've run push_to_neon.py
(or pasted neon_schema_and_data.sql into the SQL Editor).

Usage:
    pip install psycopg2-binary
    python3 verify_neon.py

Prints a pass/fail report comparing what's in the DB against the known-good
counts from the source dataset (1,310 total rows).
"""
import os

import psycopg2

CONN_STR = os.environ["DATABASE_URL"]

EXPECTED_TOTAL = 1310
EXPECTED_BY_SECTION = {"quant": 340, "reasoning": 340, "english": 330, "di": 300}
EXPECTED_DI_WITH_SVG = 300


def check(label, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    return ok


def main():
    conn = psycopg2.connect(CONN_STR)
    cur = conn.cursor()
    all_ok = True

    print("1. Table exists and row count")
    cur.execute("SELECT COUNT(*) FROM daily20_questions;")
    total = cur.fetchone()[0]
    all_ok &= check(f"total rows == {EXPECTED_TOTAL}", total == EXPECTED_TOTAL, f"found {total}")

    print("\n2. Per-section counts")
    cur.execute("SELECT section, COUNT(*) FROM daily20_questions GROUP BY section ORDER BY section;")
    got = dict(cur.fetchall())
    for section, expected in EXPECTED_BY_SECTION.items():
        actual = got.get(section, 0)
        all_ok &= check(f"section '{section}' == {expected}", actual == expected, f"found {actual}")
    extra = set(got) - set(EXPECTED_BY_SECTION)
    if extra:
        all_ok &= check("no unexpected sections", False, f"unexpected sections: {extra}")

    print("\n3. No duplicate ids")
    cur.execute("SELECT COUNT(*) FROM (SELECT id FROM daily20_questions GROUP BY id HAVING COUNT(*) > 1) t;")
    dupes = cur.fetchone()[0]
    all_ok &= check("no duplicate ids", dupes == 0, f"{dupes} duplicated ids" if dupes else "")

    print("\n4. Required fields are non-null")
    for col in ["id", "section", "topic", "question_text", "option_a", "option_b",
                "option_c", "option_d", "answer", "explanation"]:
        cur.execute(f"SELECT COUNT(*) FROM daily20_questions WHERE {col} IS NULL;")
        n = cur.fetchone()[0]
        all_ok &= check(f"{col} has no NULLs", n == 0, f"{n} NULL rows" if n else "")

    print("\n5. Answer key is always A-D")
    cur.execute("SELECT COUNT(*) FROM daily20_questions WHERE answer NOT IN ('A','B','C','D');")
    bad_ans = cur.fetchone()[0]
    all_ok &= check("answer always in A-D", bad_ans == 0, f"{bad_ans} bad rows" if bad_ans else "")

    print("\n6. DI chart data")
    cur.execute("SELECT COUNT(*) FROM daily20_questions WHERE section='di' AND chart_data IS NOT NULL;")
    n = cur.fetchone()[0]
    all_ok &= check(f"DI rows with chart_data == {EXPECTED_DI_WITH_SVG}", n == EXPECTED_DI_WITH_SVG, f"found {n}")

    cur.execute("SELECT COUNT(*) FROM daily20_questions WHERE section='di' AND chart_image_svg IS NOT NULL;")
    n = cur.fetchone()[0]
    all_ok &= check(f"DI rows with chart_image_svg == {EXPECTED_DI_WITH_SVG}", n == EXPECTED_DI_WITH_SVG, f"found {n}")

    print("\n7. Difficulty and time fields sane")
    cur.execute("SELECT COUNT(*) FROM daily20_questions WHERE difficulty NOT BETWEEN 1 AND 5;")
    n = cur.fetchone()[0]
    all_ok &= check("difficulty always 1-5", n == 0, f"{n} bad rows" if n else "")

    cur.execute("SELECT COUNT(*) FROM daily20_questions WHERE expected_time_seconds IS NULL OR expected_time_seconds <= 0;")
    n = cur.fetchone()[0]
    all_ok &= check("expected_time_seconds always positive", n == 0, f"{n} bad rows" if n else "")

    print("\n8. Topic breakdown (sanity spot-check)")
    cur.execute("SELECT section, topic, COUNT(*) FROM daily20_questions GROUP BY section, topic ORDER BY section, topic;")
    for section, topic, n in cur.fetchall():
        print(f"    {section:10s} {topic:35s} {n}")

    print("\n" + ("=" * 40))
    print("OVERALL:", "ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED -- see above")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()