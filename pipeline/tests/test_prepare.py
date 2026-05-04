"""Tests for prepare.py — Phase 1 (Init), Phase 3 (Translation Checkpoint), validate-assembly."""
import json
import pytest
import warnings
import yaml

from pipeline.lib.state_machine import StateMachine

# ── Fixtures ────────────────────────────────────────────────────────────────

MINIMAL_MANIFEST = {
    "kind": "sim2real-transfer",
    "version": 3,
    "scenario": "routing",
    "algorithm": {
        "source": "sim2real_golden/routers/router_adaptive_v2.go",
        "config": "sim2real_golden/routers/policy_adaptive_v2.yaml",
    },
    "baseline": {
        "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
        "real": {"config": None, "notes": ""},
    },
    "workloads": ["sim2real_golden/workloads/wl1.yaml"],
    "target": {"repo": "llm-d-inference-scheduler"},
    "config": {
        "kind": "EndpointPickerConfig",
        "helm_path": "gaie.treatment.helmValues.inferenceExtension.pluginsCustomConfig.custom-plugins.yaml",
    },
}

MINIMAL_ENV_DEFAULTS = {
    "common": {
        "observe": {"request_multiplier": 10},
        "build": {
            "commands": [["go", "build", "./..."]],
        },
    },
    "scenarios": {
        "routing": {
            "target": {
                "repo": "llm-d-inference-scheduler",
            },
            "build": {},
            "config": {
                "kind": "EndpointPickerConfig",
                "helm_path": "gaie.treatment.helmValues.inferenceExtension.pluginsCustomConfig.custom-plugins.yaml",
            },
            "gaie": {"baseline": {"helmValues": {}}},
        },
    },
}


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False))


def _write_text(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


import subprocess as _subprocess


def _init_git_repo(path):
    """Initialize a real git repo with an initial empty commit at path."""
    _subprocess.run(["git", "init"], cwd=path, capture_output=True)
    _subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    _subprocess.run(["git", "config", "user.name", "T"], cwd=path, capture_output=True)
    _subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=path, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """Set up a minimal repo layout for prepare.py testing."""
    # Config files
    _write_yaml(tmp_path / "config" / "env_defaults.yaml", MINIMAL_ENV_DEFAULTS)
    manifest_data = dict(MINIMAL_MANIFEST)
    _write_yaml(tmp_path / "config" / "transfer.yaml", manifest_data)

    # Algorithm files referenced by manifest
    _write_text(tmp_path / "sim2real_golden" / "routers" / "router_adaptive_v2.go", "package main")
    _write_text(tmp_path / "sim2real_golden" / "routers" / "policy_adaptive_v2.yaml", "policy: v2")
    _write_text(tmp_path / "sim2real_golden" / "routers" / "policy_baseline_211.yaml", "policy: baseline")
    _write_text(tmp_path / "sim2real_golden" / "workloads" / "wl1.yaml",
                yaml.dump({"name": "wl1", "num_requests": 100}))

    # Target repo
    (tmp_path / "llm-d-inference-scheduler" / "pkg" / "plugins" / "scorer").mkdir(parents=True)
    _write_text(tmp_path / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go",
                'func init() {\n\tRegister("test-scorer", NewTestScorer)\n}')

    # inference-sim submodule: real git repo so git rev-parse HEAD succeeds
    inf_sim = tmp_path / "inference-sim"
    inf_sim.mkdir()
    _init_git_repo(inf_sim)

    return tmp_path


# ── Test helpers ────────────────────────────────────────────────────────────

# We test prepare.py functions by importing them with a patched REPO_ROOT.
# This avoids running the full CLI which has side effects (sys.exit, input).

def _import_prepare_with_root(repo_root):
    """Import prepare module with REPO_ROOT patched to tmp_path."""
    import importlib
    import pipeline.prepare as mod
    importlib.reload(mod)
    mod.REPO_ROOT = repo_root
    mod.EXPERIMENT_ROOT = repo_root
    return mod


# ── Phase 1: Init ──────────────────────────────────────────────────────────

class TestPhaseInit:
    def test_init_creates_state_machine(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"

        class Args:
            force = False
            run = "test-run"
            manifest = None
            rebuild_context = False

        state = mod._phase_init(Args(), manifest, run_dir)
        assert state.is_done("init")
        assert state.run_name == "test-run"
        assert state.scenario == "routing"

    def test_init_skips_when_done(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"

        # Pre-create state
        sm = StateMachine("test-run", "routing", run_dir)
        sm.mark_done("init")

        class Args:
            force = False
            run = "test-run"
            manifest = None
            rebuild_context = False

        state = mod._phase_init(Args(), manifest, run_dir)
        assert state.is_done("init")

    def test_init_force_recreates(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"

        # Pre-create state
        sm = StateMachine("test-run", "routing", run_dir)
        sm.mark_done("init")
        sm.mark_done("context")

        class Args:
            force = True
            run = "test-run"
            manifest = None
            rebuild_context = False

        state = mod._phase_init(Args(), manifest, run_dir)
        assert state.is_done("init")
        # Force creates a new state machine, context should not be done
        assert not state.is_done("context")

    def test_init_missing_algorithm_source_exits(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        manifest["algorithm"] = {
            "source": "nonexistent/file.go",
            "config": "sim2real_golden/routers/policy_adaptive_v2.yaml",
        }
        run_dir = repo / "workspace" / "runs" / "test-run"

        class Args:
            force = True
            run = "test-run"
            manifest = None
            rebuild_context = False

        with pytest.raises(SystemExit):
            mod._phase_init(Args(), manifest, run_dir)

    def test_init_missing_workload_exits(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        manifest["workloads"] = ["nonexistent/workload.yaml"]
        run_dir = repo / "workspace" / "runs" / "test-run"

        class Args:
            force = True
            run = "test-run"
            manifest = None
            rebuild_context = False

        with pytest.raises(SystemExit):
            mod._phase_init(Args(), manifest, run_dir)

    def test_init_unknown_scenario_no_env_defaults(self, repo):
        """Unknown scenario is OK when env_defaults.yaml is absent (new flow)."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        manifest["scenario"] = "unknown_scenario"
        run_dir = repo / "workspace" / "runs" / "test-run"

        class Args:
            force = True
            run = "test-run"
            manifest = None
            rebuild_context = False

        state = mod._phase_init(Args(), manifest, run_dir)
        assert state is not None


# ── Phase 3: Translation Checkpoint ────────────────────────────────────────

class TestPhaseTranslate:
    def test_writes_skill_input_json(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        # Should exit cleanly at checkpoint (no translation_output.json)
        with pytest.raises(SystemExit) as exc_info:
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)
        assert exc_info.value.code == 0

        # Verify skill_input.json was written
        si_path = run_dir / "skill_input.json"
        assert si_path.exists()
        si = json.loads(si_path.read_text())
        assert si["run_name"] == "test-run"
        assert si["scenario"] == "routing"
        assert si["algorithm_source"] == "sim2real_golden/routers/router_adaptive_v2.go"
        assert si["config_kind"] == "EndpointPickerConfig"
        assert isinstance(si["build_commands"], list)
        # Test should not append, skill determines test scope
        assert si["build_commands"] == [["go", "build", "./..."]]

    def test_skips_when_done(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")
        state.mark_done("translate", plugin_type="test-scorer", files_created=[])

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        # Should not raise — just skip
        mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

    def test_detects_translation_output(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        # Write translation_output.json with all required fields
        output = {
            "plugin_type": "adaptive-v2-scorer",
            "files_created": ["pkg/plugins/scorer/adaptive_v2.go"],
            "files_modified": ["pkg/plugins/register.go"],
            "package": "scorer",
            "test_commands": [["go", "test", "./..."]],
            "config_kind": "EndpointPickerConfig",
            "helm_path": "gaie.treatment.helmValues.config",
            "treatment_config_generated": True,
            "description": "Adaptive v2 scorer",
            "register_file": "pkg/plugins/register.go",
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        class Args:
            force = False
            manifest = None

        # Should complete (not exit at checkpoint)
        mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)
        assert state.is_done("translate")
        assert state.get_phase("translate")["plugin_type"] == "adaptive-v2-scorer"

    def test_missing_required_field_in_output_exits(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        # Missing files_modified
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

    def test_checkpoint_increments_hits(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        # First checkpoint hit
        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)
        assert state.get_phase("translate").get("checkpoint_hits") == 1

        # Second checkpoint hit
        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)
        assert state.get_phase("translate").get("checkpoint_hits") == 2

    def test_skill_input_contains_hints_empty(self, repo):
        """When manifest has no hints, skill_input.json has hints={text:'', files:[]}."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        # no 'hints' key in manifest
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

        si = json.loads((run_dir / "skill_input.json").read_text())
        assert "hints" in si
        assert si["hints"]["text"] == ""
        assert si["hints"]["files"] == []
        # target has only repo
        assert set(si["target"].keys()) == {"repo"}
        # old fields removed
        assert "context_notes" not in si
        assert "plugin_dir" not in si.get("target", {})
        assert "register_file" not in si.get("target", {})

    def test_skill_input_contains_hints_from_manifest(self, repo):
        """When manifest has hints, they are written to skill_input.json."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        manifest["hints"] = {
            "text": "Modify scorer X",
            "files": [{"path": "hint.md", "content": "# Hint content"}],
        }
        run_dir = repo / "workspace" / "runs" / "test-run2"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run2", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

        si = json.loads((run_dir / "skill_input.json").read_text())
        assert si["hints"]["text"] == "Modify scorer X"
        assert si["hints"]["files"][0]["content"] == "# Hint content"

    def test_translation_output_validates_all_required_fields(self, repo):
        """translation_output.json must have all required fields."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        # Missing test_commands (one of the new required fields)
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "package": "scorer",
            "config_kind": "EndpointPickerConfig",
            "helm_path": "some.path",
            "treatment_config_generated": True,
            "description": "A test scorer",
            "register_file": None,
            # missing test_commands
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

    def test_translation_output_validates_register_file_present(self, repo):
        """register_file must be present even if null."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        # Missing register_file field entirely
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "package": "scorer",
            "test_commands": [["go", "test", "./..."]],
            "config_kind": "EndpointPickerConfig",
            "helm_path": "some.path",
            "treatment_config_generated": True,
            "description": "A test scorer",
            # missing register_file
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

    def test_translation_output_saves_register_file_and_treatment_config_generated(self, repo):
        """State saves register_file and treatment_config_generated from output."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        # Complete valid output
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "package": "scorer",
            "test_commands": [["go", "test", "./..."]],
            "config_kind": "EndpointPickerConfig",
            "helm_path": "some.path",
            "treatment_config_generated": True,
            "description": "A test scorer",
            "register_file": "pkg/plugins/register.go",
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        class Args:
            force = False
            manifest = None

        mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)
        assert state.is_done("translate")
        phase = state.get_phase("translate")
        assert phase["register_file"] == "pkg/plugins/register.go"
        assert phase["treatment_config_generated"] is True

    def test_build_commands_excludes_test_scope(self, repo):
        """build_commands should not include test scope appended by prepare.py."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")

        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

        si = json.loads((run_dir / "skill_input.json").read_text())
        # Should only have the build commands from config, not test appended
        build_cmds = si["build_commands"]
        # From MINIMAL_ENV_DEFAULTS, common.build.commands has [["go", "build", "./..."]]
        assert len(build_cmds) == 1
        assert build_cmds[0] == ["go", "build", "./..."]
        # Should NOT have go test appended
        assert not any("test" in str(cmd) for cmd in build_cmds)

    def test_skill_input_includes_baseline_sim_config(self, repo):
        """skill_input.json contains baseline_sim_config from v3 manifest."""
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)  # already v3 normalized shape

        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")
        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

        si = json.loads((run_dir / "skill_input.json").read_text())
        assert si["baseline_sim_config"] == "sim2real_golden/routers/policy_baseline_211.yaml"
        assert si["baseline_real_config"] is None
        assert si["baseline_real_notes"] == ""

    def test_skill_input_includes_baseline_real_config_when_present(self, repo):
        """skill_input.json includes baseline_real_config when v3 provides it."""
        mod = _import_prepare_with_root(repo)

        # Write a v3 manifest with baseline.real fields
        v3_data = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "routing",
            "algorithm": {
                "source": "sim2real_golden/routers/router_adaptive_v2.go",
                "config": "sim2real_golden/routers/policy_adaptive_v2.yaml",
            },
            "baseline": {
                "sim": {"config": "sim2real_golden/routers/policy_baseline_211.yaml"},
                "real": {
                    "config": "sim2real_golden/routers/baseline_epp_template.yaml",
                    "notes": "Use EndpointPickerConfig",
                },
            },
            "workloads": ["sim2real_golden/workloads/wl1.yaml"],
            "target": {"repo": "llm-d-inference-scheduler"},
            "config": {
                "kind": "EndpointPickerConfig",
                "helm_path": "gaie.treatment.helmValues.inferenceExtension.pluginsCustomConfig.custom-plugins.yaml",
            },
        }
        _write_yaml(repo / "config" / "transfer.yaml", v3_data)
        # Create the real config file so manifest validation passes
        _write_text(repo / "sim2real_golden" / "routers" / "baseline_epp_template.yaml",
                    "kind: EndpointPickerConfig\n")

        from pipeline.lib.manifest import load_manifest
        manifest = load_manifest(repo / "config" / "transfer.yaml")

        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved = mod._load_resolved_config(manifest)
        state = StateMachine("test-run", "routing", run_dir)
        state.mark_done("init")
        context_path = run_dir / "context.md"
        context_path.write_text("# Context")

        class Args:
            force = False
            manifest = None

        with pytest.raises(SystemExit):
            mod._phase_translate(Args(), state, manifest, run_dir, resolved, context_path)

        si = json.loads((run_dir / "skill_input.json").read_text())
        assert si["baseline_sim_config"] == "sim2real_golden/routers/policy_baseline_211.yaml"
        assert si["baseline_real_config"] == "sim2real_golden/routers/baseline_epp_template.yaml"
        assert si["baseline_real_notes"] == "Use EndpointPickerConfig"


# ── validate-assembly ───────────────────────────────────────────────────────

class TestValidateAssembly:
    def test_passes_when_consistent(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]

        # Set up translation output
        output = {
            "plugin_type": "test-scorer",
            "files_created": ["pkg/plugins/scorer/test_scorer.go"],
            "files_modified": ["pkg/plugins/register.go"],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        # Make register.go contain the plugin type
        register_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register_path.write_text('Register("test-scorer", NewTestScorer)')

        # Create treatment-pipeline.yaml with plugin type
        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("- type: test-scorer\n")

        # Create treatment_config with correct kind
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "EndpointPickerConfig"})

        # Create the files_created file
        scorer_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "scorer" / "test_scorer.go"
        scorer_path.write_text("package scorer")

        # Should not raise
        mod._validate_assembly(run_dir, resolved)

    def test_fails_plugin_type_not_in_register(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]

        output = {
            "plugin_type": "missing-scorer",
            "files_created": [],
            "files_modified": [],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        # register.go doesn't contain "missing-scorer"
        register_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register_path.write_text('Register("test-scorer", NewTestScorer)')

        # Create treatment-pipeline.yaml with the plugin type so Check 2 passes
        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("- type: missing-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_plugin_type_not_in_pipeline(self, repo):
        """Check 2 now reads treatment-pipeline.yaml, not epp.yaml."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]

        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        # register.go contains the plugin type
        register_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register_path.write_text('Register("test-scorer", NewTestScorer)')

        # treatment-pipeline.yaml does NOT contain the plugin type
        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("- type: other-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_kind_mismatch(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]

        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        # register.go contains the plugin type
        register_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register_path.write_text('Register("test-scorer", NewTestScorer)')

        # treatment-pipeline.yaml contains the plugin type
        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("- type: test-scorer\n")

        # treatment_config has wrong kind
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "WrongKind"})

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_files_created_missing(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]

        output = {
            "plugin_type": "test-scorer",
            "files_created": ["pkg/plugins/scorer/nonexistent.go"],
            "files_modified": [],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        # register.go contains the plugin type
        register_path = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register_path.write_text('Register("test-scorer", NewTestScorer)')

        # treatment-pipeline.yaml contains the plugin type
        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("- type: test-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_check1_skipped_when_register_file_null(self, repo):
        """register_file=null (rewrite mode) — Check 1 skipped."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": ["pkg/plugins/scorer/precise_prefix_cache.go"],
            "register_file": None,
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("type: test-scorer\n")
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "EndpointPickerConfig"})

        # register.go does NOT contain plugin_type — but should be ignored
        register = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register.write_text("no scorer here")

        mod._validate_assembly(run_dir, resolved)  # should not raise

    def test_check2_uses_pipeline_yaml_not_epp(self, repo):
        """Check 2 now reads treatment-pipeline.yaml, not epp.yaml."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": [],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        register = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register.write_text('Register("test-scorer", NewTestScorer)')

        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("type: test-scorer\n")
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "EndpointPickerConfig"})

        mod._validate_assembly(run_dir, resolved)  # should not raise

    def test_check3_skipped_when_not_generated(self, repo):
        """treatment_config_generated=False — Check 3 (kind match) skipped."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": ["pkg/plugins/scorer/precise_prefix_cache.go"],
            "register_file": "pkg/plugins/register.go",
            "treatment_config_generated": False,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        register = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register.write_text('Register("test-scorer", NewTestScorer)')

        pkg_dir = run_dir / "cluster" / "treatment"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "treatment-pipeline.yaml").write_text("type: test-scorer\n")
        # No treatment_config.yaml — but treatment_config_generated=False, so Check 3 is skipped

        mod._validate_assembly(run_dir, resolved)  # should not raise


# ── Config resolution ───────────────────────────────────────────────────────

class TestConfigResolution:
    def test_deep_merge(self, repo):
        mod = _import_prepare_with_root(repo)
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        overlay = {"b": 2, "nested": {"y": 3, "z": 4}}
        result = mod._deep_merge(base, overlay)
        assert result == {"a": 1, "b": 2, "nested": {"x": 1, "y": 3, "z": 4}}

    def test_deep_merge_does_not_mutate(self, repo):
        mod = _import_prepare_with_root(repo)
        base = {"nested": {"x": 1}}
        overlay = {"nested": {"x": 2}}
        result = mod._deep_merge(base, overlay)
        assert base["nested"]["x"] == 1
        assert result["nested"]["x"] == 2

    def test_load_resolved_config(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        resolved = mod._load_resolved_config(manifest)
        # Should have merged common + routing scenario
        assert resolved["observe"]["request_multiplier"] == 10
        assert resolved["target"]["repo"] == "llm-d-inference-scheduler"
        assert resolved["config"]["kind"] == "EndpointPickerConfig"


# ── _generate_algorithm_values ──────────────────────────────────────────────

class TestGenerateAlgorithmValues:
    def test_embeds_treatment_config_when_generated(self, repo):
        """treatment_config_generated=true → treatment slot gets custom EPP config."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": True,
        }))
        tc_yaml = "kind: EndpointPickerConfig\npluginType: test-scorer\n"
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        (run_dir / "generated" / "treatment_config.yaml").write_text(tc_yaml)

        manifest = dict(MINIMAL_MANIFEST)
        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]
        out_path = run_dir / "algorithm_values.yaml"

        mod._generate_algorithm_values(manifest, resolved, out_path)
        result = yaml.safe_load(out_path.read_text())
        custom = (result["stack"]["gaie"]["treatment"]["helmValues"]
                  ["inferenceExtension"]["pluginsCustomConfig"])
        assert custom["custom-plugins.yaml"] == tc_yaml

    def test_copies_baseline_config_when_not_generated(self, repo):
        """treatment_config_generated=false → baseline EPP config copied to treatment."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": False,
        }))

        baseline_cfg = {"custom-plugins.yaml": "baseline epp config"}
        manifest = dict(MINIMAL_MANIFEST)
        resolved = {
            **MINIMAL_ENV_DEFAULTS["scenarios"]["routing"],
            "stack": {
                "gaie": {
                    "baseline": {
                        "helmValues": {
                            "inferenceExtension": {
                                "pluginsCustomConfig": baseline_cfg
                            }
                        }
                    }
                }
            }
        }
        out_path = run_dir / "algorithm_values.yaml"

        mod._generate_algorithm_values(manifest, resolved, out_path)
        result = yaml.safe_load(out_path.read_text())
        custom = (result["stack"]["gaie"]["treatment"]["helmValues"]
                  ["inferenceExtension"]["pluginsCustomConfig"])
        assert custom == baseline_cfg

    def test_raises_when_treatment_config_missing(self, repo):
        """treatment_config_generated=true but no treatment_config.yaml → RuntimeError."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": True,
        }))

        manifest = dict(MINIMAL_MANIFEST)
        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]
        out_path = run_dir / "algorithm_values.yaml"

        with pytest.raises(RuntimeError, match="generated/treatment_config.yaml"):
            mod._generate_algorithm_values(manifest, resolved, out_path)

    def test_warns_when_no_baseline_and_not_generated(self, repo):
        """treatment_config_generated=false, baseline has no EPP config → warning emitted."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": False,
        }))

        manifest = dict(MINIMAL_MANIFEST)
        resolved = MINIMAL_ENV_DEFAULTS["scenarios"]["routing"]  # gaie.baseline has no pluginsCustomConfig
        out_path = run_dir / "algorithm_values.yaml"

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            mod._generate_algorithm_values(manifest, resolved, out_path)
        texts = [str(warning.message) for warning in w]
        assert any("treatment pluginsCustomConfig" in t for t in texts)

    def test_emits_blis_commit_when_git_succeeds(self, repo):
        """observe.blis_commit is set from inference-sim HEAD when git succeeds."""
        import subprocess
        inf_sim = repo / "inference-sim"
        expected_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=inf_sim, capture_output=True, text=True
        ).stdout.strip()

        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": False,
        }))

        mod = _import_prepare_with_root(repo)
        out_path = run_dir / "algorithm_values.yaml"
        mod._generate_algorithm_values(dict(MINIMAL_MANIFEST), MINIMAL_ENV_DEFAULTS["scenarios"]["routing"], out_path)

        result = yaml.safe_load(out_path.read_text())
        assert result["observe"]["blis_commit"] == expected_sha
        assert "image" not in result["observe"]

    def test_raises_when_blis_commit_empty(self, repo):
        """_generate_algorithm_values raises RuntimeError when git rev-parse returns empty output."""
        from unittest.mock import patch, MagicMock
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": False,
        }))

        mod = _import_prepare_with_root(repo)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch.object(mod, "run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="empty output"):
                mod._generate_algorithm_values(
                    dict(MINIMAL_MANIFEST), MINIMAL_ENV_DEFAULTS["scenarios"]["routing"],
                    run_dir / "algorithm_values.yaml",
                )

    def test_raises_when_git_fails(self, repo):
        """_generate_algorithm_values raises RuntimeError when git rev-parse fails."""
        import shutil
        # Remove .git so git rev-parse HEAD fails (no git repo, but dir still exists)
        shutil.rmtree(repo / "inference-sim" / ".git")
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "translation_output.json").write_text(json.dumps({
            "plugin_type": "test-scorer",
            "treatment_config_generated": False,
        }))

        mod = _import_prepare_with_root(repo)
        out_path = run_dir / "algorithm_values.yaml"
        with pytest.raises(RuntimeError, match="inference-sim commit"):
            mod._generate_algorithm_values(dict(MINIMAL_MANIFEST), MINIMAL_ENV_DEFAULTS["scenarios"]["routing"], out_path)


# ── _compile_cluster_packages ───────────────────────────────────────────────

class TestCompileClusterPackages:
    def test_no_epp_yaml_generated(self, repo):
        """epp.yaml must NOT be generated."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        values_path = run_dir / "values.yaml"
        values_path.write_text(yaml.dump({
            "observe": {"workloads": [{"name": "wl1", "num_requests": 10}]},
            "gaie": {"baseline": {}, "treatment": {}},
        }))
        setup_config = {"namespace": "sim2real"}

        class Args:
            pipeline_template = None

        mod._compile_cluster_packages(Args(), run_dir, {}, values_path, setup_config)

        assert not (run_dir / "cluster" / "baseline" / "epp.yaml").exists()
        assert not (run_dir / "cluster" / "treatment" / "epp.yaml").exists()

    def test_pipelinerun_yaml_per_phase(self, repo):
        """One pipelinerun-{phase}.yaml per package (baseline and treatment)."""
        from unittest.mock import patch as _patch
        mod = _import_prepare_with_root(repo)
        # Create tektonc_dir so the compile_pipeline branch is taken
        (repo / "tektonc-data-collection" / "tektoncsample" / "sim2real").mkdir(parents=True)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        values_path = run_dir / "values.yaml"
        values_path.write_text(yaml.dump({
            "observe": {
                "workloads": [
                    {"name": "bursty", "num_requests": 50},
                    {"name": "steady", "num_requests": 100},
                ]
            },
        }))
        setup_config = {"namespace": "test-ns"}

        _MINIMAL_PIPELINE = {
            "apiVersion": "tekton.dev/v1", "kind": "Pipeline",
            "metadata": {"name": "test-pipeline"},
            "spec": {
                "params": [
                    {"name": "workloadName", "type": "string"},
                    {"name": "workloadSpec", "type": "string"},
                ],
                "tasks": [{"name": "run-task", "taskRef": {"name": "t"},
                            "params": [{"name": "workloadName",
                                        "value": "$(params.workloadName)"}]}],
            },
        }

        def _fake_compile(template_dir, values_path, phase, out_dir, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{phase}-pipeline.yaml").write_text(
                yaml.dump(_MINIMAL_PIPELINE, default_flow_style=False)
            )
            return True

        class Args:
            pipeline_template = None

        with _patch.object(mod, "compile_pipeline", side_effect=_fake_compile):
            mod._compile_cluster_packages(Args(), run_dir, {}, values_path, setup_config)

        for phase in ["baseline", "treatment"]:
            assert (run_dir / "cluster" / phase / f"pipelinerun-{phase}.yaml").exists()

    def test_pipelinerun_content_is_valid_k8s(self, repo):
        """PipelineRun YAML is a valid Tekton PipelineRun resource."""
        from unittest.mock import patch as _patch
        mod = _import_prepare_with_root(repo)
        # Create tektonc_dir so the compile_pipeline branch is taken
        (repo / "tektonc-data-collection" / "tektoncsample" / "sim2real").mkdir(parents=True)
        run_dir = repo / "workspace" / "runs" / "my-run"
        run_dir.mkdir(parents=True, exist_ok=True)
        values_path = run_dir / "values.yaml"
        values_path.write_text(yaml.dump({
            "observe": {"workloads": [{"name": "wl1", "num_requests": 5}]},
        }))
        setup_config = {"namespace": "myns"}

        _MINIMAL_PIPELINE = {
            "apiVersion": "tekton.dev/v1", "kind": "Pipeline",
            "metadata": {"name": "test-pipeline"},
            "spec": {
                "params": [
                    {"name": "workloadName", "type": "string"},
                    {"name": "workloadSpec", "type": "string"},
                ],
                "tasks": [{"name": "run-task", "taskRef": {"name": "t"},
                            "params": [{"name": "workloadName",
                                        "value": "$(params.workloadName)"}]}],
            },
        }

        def _fake_compile(template_dir, values_path, phase, out_dir, **kwargs):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{phase}-pipeline.yaml").write_text(
                yaml.dump(_MINIMAL_PIPELINE, default_flow_style=False)
            )
            return True

        class Args:
            pipeline_template = None

        with _patch.object(mod, "compile_pipeline", side_effect=_fake_compile):
            mod._compile_cluster_packages(Args(), run_dir, {}, values_path, setup_config)

        pr_path = run_dir / "cluster" / "baseline" / "pipelinerun-baseline.yaml"
        pr = yaml.safe_load(pr_path.read_text())
        assert pr["apiVersion"] == "tekton.dev/v1"
        assert pr["kind"] == "PipelineRun"
        assert pr["metadata"]["namespace"] == "myns"
        params = {p["name"]: p["value"] for p in pr["spec"]["params"]}
        assert params["workloadName-baseline-wl1"] == "wl1"
        assert params["namespace"] == "myns"


def test_compile_cluster_packages_parallel_creates_per_pair_pipelineruns(tmp_path, monkeypatch):
    """Parallel mode generates one PipelineRun per (workload, package) pair."""
    import yaml as _yaml
    from unittest.mock import patch
    from pipeline.prepare import _compile_cluster_packages_parallel

    run_dir = tmp_path / "runs" / "sim2real-test"
    run_dir.mkdir(parents=True)
    values_path = run_dir / "values.yaml"
    values_path.write_text(_yaml.dump({
        "observe": {"workloads": [{"name": "wl-smoke"}, {"name": "wl-load"}]},
        "stack": {
            "gaie": {
                "baseline":  {"helmValues": {}},
                "treatment": {"helmValues": {}},
                "inferenceObjectives": [],
            }
        },
    }))
    setup_config = {
        "namespace": "sim2real-0",
        "namespaces": ["sim2real-0", "sim2real-1"],
        "workspaces": {"model-cache": {"persistentVolumeClaim": {"claimName": "model-pvc"}}},
    }

    def _fake_compile(template_dir, values_path, phase, out_dir, run_name="", **kwargs):
        stub = out_dir / f"sim2real-{run_name}.yaml"
        stub.write_text("# stub\n")
        return True

    with patch("pipeline.prepare.compile_pipeline", side_effect=_fake_compile):
        _compile_cluster_packages_parallel(
            run_dir=run_dir,
            resolved={},
            values_path=values_path,
            setup_config=setup_config,
            run_name="sim2real-test",
            template_dir=tmp_path,
        )

    cluster_dir = run_dir / "cluster"
    # Shared pipeline stub
    assert (cluster_dir / "sim2real-sim2real-test.yaml").exists()

    # One PipelineRun per pair
    for wl in ["wl-smoke", "wl-load"]:
        for pkg in ["baseline", "treatment"]:
            pr_path = cluster_dir / f"wl-{wl}-{pkg}" / f"pipelinerun-{wl}-{pkg}.yaml"
            assert pr_path.exists(), f"Missing: {pr_path}"
            pr_data = _yaml.safe_load(pr_path.read_text())
            params = {p["name"]: p["value"] for p in pr_data["spec"]["params"]}
            assert params["phase"] == pkg
            assert params["workloadName"] == wl


class TestExperimentRootSeparation:
    """Verify --experiment-root correctly separates experiment paths from framework paths."""

    def _make_framework(self, tmp_path):
        """Create a minimal framework directory (no experiment files)."""
        fw = tmp_path / "sim2real"
        # inference-sim stub
        (fw / "inference-sim" / ".git").mkdir(parents=True)
        _write_text(fw / "inference-sim" / ".git" / "config", "[core]\n")
        # pipeline/templates (framework default template)
        (fw / "pipeline" / "templates").mkdir(parents=True)
        _write_text(fw / "pipeline" / "templates" / "pipeline.yaml.j2", "# template\n")
        return fw

    def _make_experiment(self, tmp_path):
        """Create a minimal experiment repo (transfer.yaml and env_defaults.yaml at root)."""
        exp = tmp_path / "admission-control"
        manifest_data = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "admission_control",
            "algorithm": {"source": "algorithm/admission.go"},
            "baseline": {"sim": {"config": None}, "real": {"config": None, "notes": ""}},
            "workloads": ["workloads/w1.yaml"],
            "target": {"repo": "llm-d-inference-scheduler"},
            "config": {"kind": "AdmissionConfig", "helm_path": "x"},
        }
        _write_yaml(exp / "transfer.yaml", manifest_data)
        env = {
            "common": {"observe": {"request_multiplier": 1}},
            "scenarios": {
                "admission_control": {
                    "target": {"repo": "llm-d-inference-scheduler"},
                    "build": {},
                    "config": {"kind": "AdmissionConfig", "helm_path": "x"},
                    "gaie": {"baseline": {"helmValues": {}}},
                },
            },
        }
        _write_yaml(exp / "env_defaults.yaml", env)
        _write_text(exp / "algorithm" / "admission.go", "package main\n")
        _write_text(exp / "workloads" / "w1.yaml",
                    "version: '2'\nnum_requests: 10\naggregate_rate: 10\n")
        (exp / "llm-d-inference-scheduler" / "pkg").mkdir(parents=True)
        _write_text(exp / "llm-d-inference-scheduler" / "pkg" / "register.go", "")
        (exp / "workspace").mkdir(parents=True)
        return exp

    def _patch(self, mod, fw, exp):
        """Patch both REPO_ROOT and EXPERIMENT_ROOT to separate framework and experiment."""
        mod.REPO_ROOT = fw
        mod.EXPERIMENT_ROOT = exp

    def test_init_resolves_files_from_experiment_root(self, tmp_path):
        fw = self._make_framework(tmp_path)
        exp = self._make_experiment(tmp_path)

        import importlib
        import pipeline.prepare as mod
        importlib.reload(mod)
        self._patch(mod, fw, exp)

        from pipeline.lib.manifest import load_manifest
        manifest = load_manifest(exp / "transfer.yaml")
        run_dir = exp / "workspace" / "runs" / "test-run"

        class Args:
            force = False
            run = "test-run"
            manifest = str(exp / "transfer.yaml")
            rebuild_context = False
            experiment_root = str(exp)
            pipeline_template = None

        state = mod._phase_init(Args(), manifest, run_dir)
        assert state.is_done("init")
        assert state.scenario == "admission_control"

    def test_env_defaults_resolved_from_experiment_root(self, tmp_path):
        fw = self._make_framework(tmp_path)
        exp = self._make_experiment(tmp_path)

        import importlib
        import pipeline.prepare as mod
        importlib.reload(mod)
        self._patch(mod, fw, exp)

        from pipeline.lib.manifest import load_manifest
        manifest = load_manifest(exp / "transfer.yaml")
        resolved = mod._load_resolved_config(manifest)
        assert resolved["target"]["repo"] == "llm-d-inference-scheduler"

    def test_template_dir_resolves_framework_default(self, tmp_path):
        fw = self._make_framework(tmp_path)
        exp = self._make_experiment(tmp_path)

        import importlib
        import pipeline.prepare as mod
        importlib.reload(mod)
        self._patch(mod, fw, exp)

        class Args:
            pipeline_template = None

        result = mod._resolve_template_dir(Args(), exp)
        assert result == fw / "pipeline" / "templates"

    def test_template_dir_resolves_experiment_override(self, tmp_path):
        fw = self._make_framework(tmp_path)
        exp = self._make_experiment(tmp_path)
        _write_text(exp / "pipeline.yaml.j2", "# experiment override\n")

        import importlib
        import pipeline.prepare as mod
        importlib.reload(mod)
        self._patch(mod, fw, exp)

        class Args:
            pipeline_template = None

        result = mod._resolve_template_dir(Args(), exp)
        assert result == exp

    def test_workspace_created_in_experiment_root(self, tmp_path):
        """run_dir should be under EXPERIMENT_ROOT, not REPO_ROOT."""
        fw = self._make_framework(tmp_path)
        exp = self._make_experiment(tmp_path)

        import importlib
        import pipeline.prepare as mod
        importlib.reload(mod)
        self._patch(mod, fw, exp)

        from pipeline.lib.manifest import load_manifest
        manifest = load_manifest(exp / "transfer.yaml")
        run_dir = exp / "workspace" / "runs" / "test-run"

        class Args:
            force = False
            run = "test-run"
            manifest = str(exp / "transfer.yaml")
            rebuild_context = False
            experiment_root = str(exp)
            pipeline_template = None

        mod._phase_init(Args(), manifest, run_dir)
        # State file should exist in experiment root, not framework root
        assert (exp / "workspace" / "runs" / "test-run" / ".state.json").exists()
        assert not (fw / "workspace").exists()
