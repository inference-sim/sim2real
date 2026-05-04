"""Tests for prepare.py — Phase 1 (Init), Phase 3 (Translation Checkpoint), validate-assembly."""
import json
import pytest
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
    "observe": {"request_multiplier": 10},
    "build": {"commands": [["go", "build", "./..."]]},
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

    def test_init_unknown_scenario_succeeds(self, repo):
        """Unknown scenario no longer requires matching env_defaults entry."""
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
        # Should only have the build commands from manifest, not test appended
        build_cmds = si["build_commands"]
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

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}

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

        # Create treatment.yaml with plugin type
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("- type: test-scorer\n")

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

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}

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

        # Create treatment.yaml with the plugin type so Check 2 passes
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("- type: missing-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_plugin_type_not_in_pipeline(self, repo):
        """Check 2 reads cluster/treatment.yaml for plugin_type."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}

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

        # treatment.yaml does NOT contain the plugin type
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("- type: other-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_kind_mismatch(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}

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

        # treatment.yaml contains the plugin type
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("- type: test-scorer\n")

        # treatment_config has wrong kind
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "WrongKind"})

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_fails_files_created_missing(self, repo):
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}

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

        # treatment.yaml contains the plugin type
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("- type: test-scorer\n")

        with pytest.raises(SystemExit):
            mod._validate_assembly(run_dir, resolved)

    def test_check1_skipped_when_register_file_null(self, repo):
        """register_file=null (rewrite mode) — Check 1 skipped."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}
        output = {
            "plugin_type": "test-scorer",
            "files_created": [],
            "files_modified": ["pkg/plugins/scorer/precise_prefix_cache.go"],
            "register_file": None,
            "treatment_config_generated": True,
        }
        (run_dir / "translation_output.json").write_text(json.dumps(output))

        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("type: test-scorer\n")
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "EndpointPickerConfig"})

        # register.go does NOT contain plugin_type — but should be ignored
        register = repo / "llm-d-inference-scheduler" / "pkg" / "plugins" / "register.go"
        register.write_text("no scorer here")

        mod._validate_assembly(run_dir, resolved)  # should not raise

    def test_check2_uses_treatment_yaml(self, repo):
        """Check 2 reads cluster/treatment.yaml for plugin_type."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}
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

        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("type: test-scorer\n")
        (run_dir / "generated").mkdir(parents=True, exist_ok=True)
        _write_yaml(run_dir / "generated" / "treatment_config.yaml", {"kind": "EndpointPickerConfig"})

        mod._validate_assembly(run_dir, resolved)  # should not raise

    def test_check3_skipped_when_not_generated(self, repo):
        """treatment_config_generated=False — Check 3 (kind match) skipped."""
        mod = _import_prepare_with_root(repo)
        run_dir = repo / "workspace" / "runs" / "test-run"
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = {"target": {"repo": "llm-d-inference-scheduler"}, "config": {"kind": "EndpointPickerConfig"}}
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

        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True, exist_ok=True)
        (cluster_dir / "treatment.yaml").write_text("type: test-scorer\n")
        # No treatment_config.yaml — but treatment_config_generated=False, so Check 3 is skipped

        mod._validate_assembly(run_dir, resolved)  # should not raise


# ── Config resolution ───────────────────────────────────────────────────────

class TestConfigResolution:
    def test_load_resolved_config(self, repo):
        mod = _import_prepare_with_root(repo)
        manifest = dict(MINIMAL_MANIFEST)
        resolved = mod._load_resolved_config(manifest)
        # Should have merged common + routing scenario
        assert resolved["observe"]["request_multiplier"] == 10
        assert resolved["target"]["repo"] == "llm-d-inference-scheduler"
        assert resolved["config"]["kind"] == "EndpointPickerConfig"




class TestExperimentRootSeparation:
    """Verify --experiment-root correctly separates experiment paths from framework paths."""

    def _make_framework(self, tmp_path):
        """Create a minimal framework directory (no experiment files)."""
        fw = tmp_path / "sim2real"
        # inference-sim stub
        (fw / "inference-sim" / ".git").mkdir(parents=True)
        _write_text(fw / "inference-sim" / ".git" / "config", "[core]\n")
        # pipeline/pipeline.yaml (static Pipeline resource)
        (fw / "pipeline").mkdir(parents=True, exist_ok=True)
        _write_text(fw / "pipeline" / "pipeline.yaml", "apiVersion: tekton.dev/v1\nkind: Pipeline\nmetadata:\n  name: sim2real\n")
        return fw

    def _make_experiment(self, tmp_path):
        """Create a minimal experiment repo (transfer.yaml at root)."""
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
