"""Tests for pipeline/lib/capacity.py — GPU capacity probe."""

import json
from unittest.mock import patch, MagicMock

from pipeline.lib.capacity import probe_free_gpus, derive_gpu_resource_type


class TestProbeFreeGpus:
    def _mock_nodes(self, allocatable_gpus: list[int], resource="nvidia.com/gpu"):
        """Build fake kubectl nodes JSON."""
        nodes = []
        for gpu_count in allocatable_gpus:
            node = {
                "metadata": {"name": f"node-{len(nodes)}"},
                "status": {
                    "allocatable": {resource: str(gpu_count)} if gpu_count > 0 else {}
                },
            }
            nodes.append(node)
        return json.dumps({"items": nodes})

    def _mock_pods(self, requested_gpus: list[int], resource="nvidia.com/gpu"):
        """Build fake kubectl pods JSON."""
        pods = []
        for gpu_req in requested_gpus:
            pod = {
                "metadata": {"name": f"pod-{len(pods)}"},
                "spec": {
                    "containers": [
                        {"resources": {"requests": {resource: str(gpu_req)}}}
                    ]
                },
            }
            pods.append(pod)
        return json.dumps({"items": pods})

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_basic_computation(self, mock_run):
        # 14 nodes × 8 GPUs = 112 allocatable, 108 requested → 4 free
        nodes_json = self._mock_nodes([8] * 14)
        pods_json = self._mock_pods([1] * 108)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]

        result = probe_free_gpus()
        assert result == (4, 112, 108)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_clamps_to_zero(self, mock_run):
        nodes_json = self._mock_nodes([2])
        pods_json = self._mock_pods([1] * 5)

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]

        result = probe_free_gpus()
        assert result == (0, 2, 5)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_custom_resource_type(self, mock_run):
        nodes_json = self._mock_nodes([4, 4], resource="habana.ai/gaudi")
        pods_json = self._mock_pods([2], resource="habana.ai/gaudi")

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]

        result = probe_free_gpus(gpu_resource_type="habana.ai/gaudi")
        assert result == (6, 8, 2)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_kubectl_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")

        result = probe_free_gpus()
        assert result is None

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_nodes_without_gpu_resource_ignored(self, mock_run):
        # Mix of GPU and non-GPU nodes
        nodes = {
            "items": [
                {"metadata": {"name": "gpu-0"}, "status": {"allocatable": {"nvidia.com/gpu": "8", "cpu": "64"}}},
                {"metadata": {"name": "cpu-0"}, "status": {"allocatable": {"cpu": "96"}}},
            ]
        }
        pods = {"items": []}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(nodes)),
            MagicMock(returncode=0, stdout=json.dumps(pods)),
        ]

        result = probe_free_gpus()
        assert result == (8, 8, 0)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_malformed_gpu_value_returns_none(self, mock_run):
        nodes = {"items": [
            {"metadata": {"name": "n0"}, "status": {"allocatable": {"nvidia.com/gpu": "not-a-number"}}}
        ]}
        pods = {"items": []}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(nodes)),
            MagicMock(returncode=0, stdout=json.dumps(pods)),
        ]
        result = probe_free_gpus()
        assert result is None


# ── derive_gpu_resource_type tests ─────────────────────────────────────────────


class TestDeriveGpuResourceType:
    def test_derives_from_defaults(self):
        defaults = {"accelerator": {"resource": "nvidia.com/gpu"}}
        scenario = {"scenario": [{"name": "test"}]}
        assert derive_gpu_resource_type(scenario, defaults) == "nvidia.com/gpu"

    def test_scenario_overrides_defaults(self):
        defaults = {"accelerator": {"resource": "nvidia.com/gpu"}}
        scenario = {"scenario": [{"name": "test", "accelerator": {"resource": "habana.ai/gaudi"}}]}
        assert derive_gpu_resource_type(scenario, defaults) == "habana.ai/gaudi"

    def test_missing_accelerator_falls_back(self):
        defaults = {}
        scenario = {"scenario": [{"name": "test"}]}
        assert derive_gpu_resource_type(scenario, defaults) == "nvidia.com/gpu"


# ── gpu_cost_per_pair tests ────────────────────────────────────────────────────

from pipeline.lib.capacity import gpu_cost_per_pair


class TestGpuCostPerPair:
    def test_default_single_decode_replica(self):
        # Minimal overlay: only decode.replicas=4, merge with defaults
        defaults = {
            "accelerator": {"resource": "nvidia.com/gpu"},
            "decode": {
                "enabled": True,
                "replicas": 1,
                "parallelism": {"tensor": 1, "dataLocal": 1},
            },
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {
            "scenario": [{"name": "test", "decode": {"replicas": 4}}]
        }
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 4  # 4 replicas × 1 GPU per pod

    def test_tensor_parallel_derivation(self):
        defaults = {
            "decode": {
                "enabled": True,
                "replicas": 1,
                "parallelism": {"tensor": 4, "dataLocal": 1},
            },
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test", "decode": {"replicas": 2}}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 8  # 2 replicas × 4 GPUs per pod

    def test_role_accelerator_count_overrides_parallelism(self):
        defaults = {
            "decode": {
                "enabled": True,
                "replicas": 1,
                "parallelism": {"tensor": 4, "dataLocal": 1},
            },
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {
            "scenario": [{"name": "test", "decode": {"replicas": 2, "accelerator": {"count": 2}}}]
        }
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 4  # 2 replicas × 2 (role override)

    def test_top_level_accelerator_count_zero_means_cpu_only(self):
        defaults = {
            "accelerator": {"count": 0},
            "decode": {"enabled": True, "replicas": 4, "parallelism": {"tensor": 4, "dataLocal": 1}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test"}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 0

    def test_prefill_enabled_adds_cost(self):
        defaults = {
            "decode": {
                "enabled": True,
                "replicas": 2,
                "parallelism": {"tensor": 1, "dataLocal": 1},
            },
            "prefill": {
                "enabled": False,
                "replicas": 0,
                "parallelism": {"tensor": 1, "dataLocal": 1},
            },
        }
        scenario = {
            "scenario": [{"name": "test", "prefill": {"enabled": True, "replicas": 1}}]
        }
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 3  # decode: 2×1 + prefill: 1×1

    def test_top_level_accelerator_count_as_fallback(self):
        defaults = {
            "accelerator": {"count": 8},
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 2, "dataLocal": 2}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test"}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        # accelerator.count (8) takes precedence over tensor*dataLocal (4)
        assert cost == 8

    def test_empty_scenario_returns_defaults_cost(self):
        defaults = {
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 1, "dataLocal": 1}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test"}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 1
