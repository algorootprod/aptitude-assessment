"""CLI stub: import curated questions from a local file/directory into `question_bank`.

Not implemented — the curated question set lives on the user's machine today and its exact
file format hasn't been shared yet (see CLAUDE.md, "Phase 2"). The column shape the importer
must produce is `app.modules.user_test_mapping.models.QuestionBank`, sourced from
`daily20_prototype.html`'s embedded question JSON.

Intended usage once implemented:
    ./scripts/run_worker.sh import_question_bank --path /path/to/questions
"""

from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_question_bank",
        description="Import curated questions into the question_bank table (not yet implemented)",
    )
    parser.add_argument("--path", required=True, help="Path to the curated question file(s)")
    return parser


def main() -> None:
    _build_parser().parse_args()
    raise NotImplementedError(
        "Question bank import is not implemented yet — see CLAUDE.md, 'Phase 2'."
    )


if __name__ == "__main__":
    main()
