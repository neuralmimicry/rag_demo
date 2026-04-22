"""
Command-line interface for the refiner package.

Preferred invocation patterns:
- Installed as a package: run the console script `refiner`
- From source without installation: python -m refiner.cli

This keeps the CLI thin and delegates argument parsing and execution to the
unified run_refiner.run() function so we have a single source of truth for
Jira stats, Confluence analysis, topic research, and project solver modes.
"""
from typing import Optional, List


def main(argv: Optional[List[str]] = None) -> int:
    """CLI wrapper that delegates execution to :func:`run_refiner.run`."""
    # Delegate to the canonical workflow router used by ``python -m refiner.run_refiner``.
    from refiner.run_refiner import run as unified_run
    return unified_run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
