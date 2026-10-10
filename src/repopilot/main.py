"""RepoPilot Main CLI Entrypoint.

Supports zero-cost GitHub Actions scheduling and safe local test mode.
"""

import argparse
import logging
import os
from pathlib import Path
import sys
from typing import Optional

from repopilot.ai.mock_provider import MockAIProvider
from repopilot.ai.router import get_ai_provider
from repopilot.config.loader import load_config
from repopilot.config.models import RepoPilotConfig
from repopilot.core.exceptions import ConfigurationError, RepoPilotError
from repopilot.core.logging import setup_logger
from repopilot.github.client import MockGitHubClient, RestGitHubClient
from repopilot.intelligence.analyzer import LocalRepositoryAnalyzer
from repopilot.orchestration.runner import Orchestrator
from repopilot.reporting.reporter import ExecutionReporter
from repopilot.state.store import GitBranchStateStore, LocalFileStateStore, MockStateStore


def parse_args(args: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="repopilot",
        description="Autonomous, zero-cost GitHub repository management and development agent.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Path to YAML configuration file (default: config/repos.yaml or config/repos.example.yaml)",
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Run in isolated local test mode with mock providers and zero external side effects.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for --test-mode.",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Emit structured JSON logs instead of plain text.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity level (default: INFO).",
    )
    parser.add_argument(
        "--report-out",
        type=str,
        default=None,
        help="Optional path to output the JSON execution report.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Target a specific repository by name (e.g., 'owner/repo' or 'repo').",
    )
    return parser.parse_args(args)


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    is_test_mode = args.test_mode or args.dry_run

    # Initialize structured logger
    logger = setup_logger(name="repopilot", level=args.log_level, json_mode=args.json_logs)
    logger.info("Starting RepoPilot autonomous agent...")

    # Load configuration
    try:
        config: RepoPilotConfig = load_config(args.config)
        logger.info(f"Loaded configuration (version {config.version}) with {len(config.repositories)} repositories.")
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error loading configuration: {e}", exc_info=True)
        return 1

    # Filter target repo if specified
    if args.repo:
        filtered = [
            r for r in config.repositories
            if r.name == args.repo or f"{r.owner}/{r.name}" == args.repo
        ]
        if not filtered:
            logger.error(f"Requested target repository '{args.repo}' not found in configuration.")
            return 1
        config.repositories = filtered

    # Select GitHub client
    if is_test_mode:
        logger.info("Initializing in LOCAL TEST MODE: using Mock GitHub Client.")
        github_client = MockGitHubClient()
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            logger.warning("No GITHUB_TOKEN or GH_TOKEN detected in environment. Falling back to Mock GitHub Client.")
            github_client = MockGitHubClient()
        else:
            github_client = RestGitHubClient(token=token)

    # Select AI provider
    if is_test_mode:
        logger.info("Initializing in LOCAL TEST MODE: using deterministic Mock AI Provider.")
        ai_provider = MockAIProvider(available=True)
    else:
        ai_provider = get_ai_provider(config.settings)
        status = ai_provider.get_status()
        logger.info(f"AI Provider '{ai_provider.get_name()}' initialized with status: {status.value}")

    # Select State Store
    if is_test_mode:
        state_store = MockStateStore()
    else:
        backend = (config.settings.state_backend or "local_file").lower()
        if backend == "git_branch":
            state_store = GitBranchStateStore(
                branch_name=config.settings.state_branch_name,
                file_rel_path=Path(config.settings.state_file_path).name,
            )
        else:
            state_store = LocalFileStateStore(config.settings.state_file_path)

    # Repository Analyzer
    analyzer = LocalRepositoryAnalyzer()

    # Orchestrator
    orchestrator = Orchestrator(
        config=config,
        github_client=github_client,
        ai_provider=ai_provider,
        state_store=state_store,
        analyzer=analyzer,
    )

    try:
        report = orchestrator.run()

        # Output JSON report if requested
        if args.report_out:
            ExecutionReporter.write_json_report(report, Path(args.report_out))

        summary_md = ExecutionReporter.generate_markdown_summary(report)
        print("\n" + summary_md + "\n")

        return 0 if not report.errors else 2

    except RepoPilotError as e:
        logger.error(f"Execution failed with RepoPilot error: {e}")
        return 2
    except Exception as e:
        logger.error(f"Unhandled operational error: {e}", exc_info=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
