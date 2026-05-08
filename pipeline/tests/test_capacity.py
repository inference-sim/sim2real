"""Tests for pipeline/lib/capacity.py — GPU capacity probe."""

import json
from unittest.mock import patch, MagicMock

from pipeline.lib.capacity import probe_free_gpus


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
