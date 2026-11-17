"""Fixed, external T0 oracle runner. Runs ONLY inside isolated Docker container."""
import json
import math
import runpy
import sys

def main():
    try:
        namespace = runpy.run_path("/work/candidate.py")
        analyze = namespace.get("analyze")
        if not callable(analyze):
            raise ValueError()
        cases = json.loads(open("/work/oracles.json", encoding="utf-8").read())
        for test in cases:
            value = analyze(test["input"])
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError()
            if not math.isfinite(float(value)) or not math.isclose(
                float(value), float(test["expected"]), rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError()
    except BaseException:
        # Do NOT echo candidate output, exceptions or private local paths.
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
