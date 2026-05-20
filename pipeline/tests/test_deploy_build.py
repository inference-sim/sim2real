"""Tests for deploy build subcommand."""
import pytest
from pipeline.deploy import build_parser


def test_build_parser_exists():
    """build subcommand is registered."""
    parser = build_parser()
    args = parser.parse_args(["build"])
    assert args.command == "build"


def test_build_parser_skip_flag():
    """build subcommand has --skip-build flag."""
    parser = build_parser()
    args = parser.parse_args(["build", "--skip-build"])
    assert args.skip_build is True


def test_run_parser_skip_build_flag():
    """run subcommand has --skip-build (not --skip-build-epp)."""
    parser = build_parser()
    args = parser.parse_args(["run", "--skip-build"])
    assert args.skip_build is True


def test_run_parser_no_skip_build_epp():
    """--skip-build-epp is no longer accepted."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--skip-build-epp"])
