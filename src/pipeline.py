from __future__ import annotations

import json
from pathlib import Path
import argparse
from dotenv import load_dotenv

from qa import qa_check_transcript
from context_inference_llm import infer_context_llm
from editor import edit_transcript

load_dotenv()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--infile", required=True)
    p.add_argument("--outfile", required=True)
    p.add_argument("--model", default="gpt-5.2")
    args = p.parse_args()

    obj = json.loads(Path(args.infile).read_text(encoding="utf-8"))

    # Step 1: QA (chekc for any omission or long gaps)
    qa_report, _ = qa_check_transcript(obj)
    obj["qa_report"] = qa_report
    if not qa_report.get("ok", True):
        raise SystemExit(f"QA failed: {qa_report.get('errors')}")

    # Step 2: LLM context inference (extract context and mistakes)
    ctx = infer_context_llm(obj, model=args.model)
    obj["context_inferred"] = ctx["context_inferred"]
    obj["context_report"] = ctx["context_report"]

    # Step 3: Editor (safe fixes + trace according to result of step 2)
    obj = edit_transcript(obj)

    Path(args.outfile).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.outfile}")

    if qa_report["warnings"]:
        print("warnings:", qa_report["warnings"])


if __name__ == "__main__":
    main()
