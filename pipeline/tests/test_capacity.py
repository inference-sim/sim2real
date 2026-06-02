"""Tests for pipeline/lib/capacity.py — GPU capacity probe."""

import json
from unittest.mock import patch, MagicMock

from pipeline.lib.capacity import probe_free_gpus, derive_gpu_resource_type, gpu_cost_per_pair
from pipeline.lib.capacity import NodeFilter, node_is_eligible
from pipeline.lib.capacity import extract_node_filters


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

    def _mock_pods(self, requested_gpus: list[int], resource="nvidia.com/gpu",
                   node_names: list[str | None] | None = None):
        """Build fake kubectl pods JSON.

        node_names: per-pod nodeName (None = Pending, no nodeName).
        Defaults to all pods having a nodeName.
        """
        if node_names is None:
            node_names = [f"node-{i}" for i in range(len(requested_gpus))]
        pods = []
        for i, gpu_req in enumerate(requested_gpus):
            spec: dict = {
                "containers": [
                    {"resources": {"requests": {resource: str(gpu_req)}}}
                ]
            }
            if node_names[i] is not None:
                spec["nodeName"] = node_names[i]
            pod = {
                "metadata": {"name": f"pod-{len(pods)}"},
                "spec": spec,
            }
            pods.append(pod)
        return json.dumps({"items": pods})

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_basic_computation(self, mock_run):
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
    def test_kubectl_failure_returns_error_string(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")

        result = probe_free_gpus()
        assert isinstance(result, str)
        assert "connection refused" in result

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_nodes_without_gpu_resource_ignored(self, mock_run):
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
    def test_malformed_gpu_value_returns_error(self, mock_run):
        nodes = {"items": [
            {"metadata": {"name": "n0"}, "status": {"allocatable": {"nvidia.com/gpu": "8000m"}}}
        ]}
        pods = {"items": []}
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps(nodes)),
            MagicMock(returncode=0, stdout=json.dumps(pods)),
        ]
        result = probe_free_gpus()
        assert isinstance(result, str)
        assert "8000m" in result

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_malformed_json_returns_error(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="not json"),
            MagicMock(returncode=0, stdout="{}"),
        ]
        result = probe_free_gpus()
        assert isinstance(result, str)
        assert "JSON parse error" in result

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_pending_pods_excluded(self, mock_run):
        """Pending pods (no nodeName) should not count as GPU consumers."""
        nodes_json = self._mock_nodes([8, 8])
        pods_json = self._mock_pods(
            [4, 4, 4],
            node_names=["node-0", "node-1", None],
        )

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]

        result = probe_free_gpus()
        assert result == (8, 16, 8)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_all_pods_pending_means_zero_requested(self, mock_run):
        """When every pod is Pending, total requested should be zero."""
        nodes_json = self._mock_nodes([8, 8])
        pods_json = self._mock_pods(
            [4, 4],
            node_names=[None, None],
        )

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]

        result = probe_free_gpus()
        assert result == (16, 16, 0)

    def _mock_nodes_full(self, specs):
        """specs: list of dicts with keys: gpu, name (optional), unschedulable,
        taints, gpu_product."""
        nodes = []
        for i, s in enumerate(specs):
            name = s.get("name", f"node-{i}")
            labels = {}
            if "gpu_product" in s:
                labels["nvidia.com/gpu.product"] = s["gpu_product"]
            spec = {}
            if s.get("unschedulable"):
                spec["unschedulable"] = True
            if s.get("taints"):
                spec["taints"] = s["taints"]
            status = {"allocatable": {"nvidia.com/gpu": str(s.get("gpu", 0))}}
            nodes.append({
                "metadata": {"name": name, "labels": labels},
                "spec": spec,
                "status": status,
            })
        return json.dumps({"items": nodes})

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_filter_excludes_cordoned_nodes(self, mock_run):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "good"},
            {"gpu": 8, "name": "cordoned", "unschedulable": True},
        ])
        pods_json = self._mock_pods([2], node_names=["cordoned"])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[NodeFilter()])
        assert result == (8, 8, 0)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_filter_excludes_tainted_nodes_when_no_tolerations(self, mock_run):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "good"},
            {"gpu": 8, "name": "tainted", "taints": [
                {"key": "app", "effect": "NoSchedule"}
            ]},
        ])
        pods_json = self._mock_pods([3], node_names=["good"])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[NodeFilter(tolerations=[])])
        assert result == (5, 8, 3)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_filter_excludes_wrong_gpu_product(self, mock_run):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "h100", "gpu_product": "NVIDIA-H100-80GB-HBM3"},
            {"gpu": 8, "name": "a100", "gpu_product": "NVIDIA-A100-40GB"},
        ])
        pods_json = self._mock_pods([])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[
            NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        ])
        assert result == (8, 8, 0)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_no_filter_preserves_legacy_behavior(self, mock_run):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "cordoned", "unschedulable": True},
            {"gpu": 8, "name": "tainted", "taints": [
                {"key": "x", "effect": "NoSchedule"}
            ]},
        ])
        pods_json = self._mock_pods([])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus()
        assert result == (16, 16, 0)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_pod_request_on_filtered_node_excluded(self, mock_run):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "good"},
            {"gpu": 8, "name": "wrong-product", "gpu_product": "NVIDIA-A100-40GB"},
        ])
        pods_json = self._mock_pods([4, 4], node_names=["good", "wrong-product"])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[
            NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        ])
        # Neither node is H100 → both excluded.
        assert result == (0, 0, 0)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_pod_with_no_nodename_still_skipped_under_filter(self, mock_run):
        nodes_json = self._mock_nodes_full([{"gpu": 8, "name": "good"}])
        pods_json = self._mock_pods([4, 4], node_names=["good", None])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[NodeFilter()])
        assert result == (4, 8, 4)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_mixed_eligibility_pod_accounting(self, mock_run):
        """One eligible node, one excluded; pods on each. Eligible counts, excluded drops."""
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "h100", "gpu_product": "NVIDIA-H100-80GB-HBM3"},
            {"gpu": 8, "name": "a100", "gpu_product": "NVIDIA-A100-40GB"},
        ])
        pods_json = self._mock_pods([3, 5], node_names=["h100", "a100"])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        result = probe_free_gpus(node_filters=[
            NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        ])
        # h100 included: 8 alloc, 3 requested. a100 and its 5-GPU pod both excluded.
        assert result == (5, 8, 3)

    @patch("pipeline.lib.capacity.subprocess.run")
    def test_warns_on_unrecognized_taint_effect(self, mock_run, capsys):
        nodes_json = self._mock_nodes_full([
            {"gpu": 8, "name": "typo", "taints": [{"key": "x", "effect": "Noschedule"}]},
        ])
        pods_json = self._mock_pods([])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=nodes_json),
            MagicMock(returncode=0, stdout=pods_json),
        ]
        probe_free_gpus(node_filters=[NodeFilter()])
        out = capsys.readouterr().out
        assert "unrecognized taint effects" in out
        assert "Noschedule" in out


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


class TestGpuCostPerPair:
    def test_default_single_decode_replica(self):
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
        assert cost == 4

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
        assert cost == 8

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
        assert cost == 4

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
        assert cost == 3

    def test_top_level_accelerator_count_as_fallback(self):
        defaults = {
            "accelerator": {"count": 8},
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 2, "dataLocal": 2}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test"}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 8

    def test_empty_scenario_returns_defaults_cost(self):
        defaults = {
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 1, "dataLocal": 1}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test"}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert cost == 1

    def test_non_numeric_accelerator_count_returns_error(self):
        defaults = {
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 1, "dataLocal": 1}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test", "accelerator": {"count": "auto"}}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert isinstance(cost, str)
        assert "auto" in cost
        assert "accelerator.count" in cost

    def test_non_numeric_role_accelerator_count_returns_error(self):
        defaults = {
            "decode": {"enabled": True, "replicas": 1, "parallelism": {"tensor": 1, "dataLocal": 1}},
            "prefill": {"enabled": False, "replicas": 0},
        }
        scenario = {"scenario": [{"name": "test", "decode": {"accelerator": {"count": "bad"}}}]}
        cost = gpu_cost_per_pair(scenario, defaults)
        assert isinstance(cost, str)
        assert "bad" in cost
        assert "decode.accelerator.count" in cost


# ── load_defaults tests ───────────────────────────────────────────────────────


from pipeline.lib.capacity import load_defaults


def test_load_defaults_explicit_path(tmp_path):
    """load_defaults with explicit defaults_path reads from that path directly."""
    defaults_file = tmp_path / "my-defaults.yaml"
    defaults_file.write_text("decode:\n  accelerator:\n    count: 4\n")
    result = load_defaults(tmp_path, defaults_path=defaults_file)
    assert result == {"decode": {"accelerator": {"count": 4}}}


def test_load_defaults_explicit_path_missing(tmp_path):
    """load_defaults with explicit path to non-existent file returns None."""
    result = load_defaults(tmp_path, defaults_path=tmp_path / "nope.yaml")
    assert result is None


class TestNodeFilter:
    def _node(self, *, name="n", unschedulable=False, taints=None,
              gpu_product=None, allocatable=None):
        labels = {}
        if gpu_product is not None:
            labels["nvidia.com/gpu.product"] = gpu_product
        spec = {"unschedulable": unschedulable} if unschedulable else {}
        if taints:
            spec["taints"] = taints
        status = {"allocatable": allocatable or {}}
        return {
            "metadata": {"name": name, "labels": labels},
            "spec": spec,
            "status": status,
        }

    def test_no_filter_accepts_any_node(self):
        node = self._node(unschedulable=True, taints=[
            {"key": "x", "effect": "NoSchedule"}
        ])
        # When no filter is supplied, every node is eligible (legacy behavior).
        assert node_is_eligible(node, []) is True

    def test_cordoned_node_rejected_when_filter_present(self):
        node = self._node(unschedulable=True)
        f = NodeFilter()
        assert node_is_eligible(node, [f]) is False

    def test_node_with_no_unschedulable_field_is_treated_as_schedulable(self):
        node = self._node()
        f = NodeFilter()
        assert node_is_eligible(node, [f]) is True

    def test_unschedulable_false_is_treated_as_schedulable(self):
        node = self._node()
        f = NodeFilter()
        assert node_is_eligible(node, [f]) is True

    def test_noschedule_taint_excludes_when_no_role_tolerates(self):
        node = self._node(taints=[
            {"key": "app", "value": "harness", "effect": "NoSchedule"}
        ])
        f = NodeFilter(tolerations=[])
        assert node_is_eligible(node, [f]) is False

    def test_noexecute_taint_excludes_when_no_role_tolerates(self):
        node = self._node(taints=[
            {"key": "app", "effect": "NoExecute"}
        ])
        f = NodeFilter(tolerations=[])
        assert node_is_eligible(node, [f]) is False

    def test_prefer_no_schedule_taint_is_ignored(self):
        node = self._node(taints=[
            {"key": "app", "effect": "PreferNoSchedule"}
        ])
        f = NodeFilter(tolerations=[])
        assert node_is_eligible(node, [f]) is True

    def test_tolerated_noschedule_taint_does_not_exclude(self):
        node = self._node(taints=[
            {"key": "app", "value": "harness", "effect": "NoSchedule"}
        ])
        f = NodeFilter(tolerations=[
            {"key": "app", "operator": "Equal", "value": "harness", "effect": "NoSchedule"}
        ])
        assert node_is_eligible(node, [f]) is True

    def test_tolerations_with_operator_exists_match_any_value(self):
        node = self._node(taints=[
            {"key": "app", "value": "harness", "effect": "NoSchedule"}
        ])
        f = NodeFilter(tolerations=[
            {"key": "app", "operator": "Exists", "effect": "NoSchedule"}
        ])
        assert node_is_eligible(node, [f]) is True

    def test_toleration_must_match_effect(self):
        node = self._node(taints=[
            {"key": "app", "value": "harness", "effect": "NoSchedule"}
        ])
        f = NodeFilter(tolerations=[
            {"key": "app", "operator": "Equal", "value": "harness", "effect": "NoExecute"}
        ])
        assert node_is_eligible(node, [f]) is False

    def test_required_gpu_product_match(self):
        node = self._node(gpu_product="NVIDIA-H100-80GB-HBM3")
        f = NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        assert node_is_eligible(node, [f]) is True

    def test_required_gpu_product_mismatch(self):
        node = self._node(gpu_product="NVIDIA-A100-40GB")
        f = NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        assert node_is_eligible(node, [f]) is False

    def test_required_gpu_product_with_missing_label(self):
        node = self._node()
        f = NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        assert node_is_eligible(node, [f]) is False

    def test_empty_required_set_means_no_product_constraint(self):
        node = self._node(gpu_product="NVIDIA-A100-40GB")
        f = NodeFilter(required_gpu_products=set())
        assert node_is_eligible(node, [f]) is True

    def test_union_eligibility_across_roles(self):
        node = self._node(gpu_product="NVIDIA-H100-80GB-HBM3")
        decode = NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        prefill = NodeFilter(required_gpu_products={"NVIDIA-A100-40GB"})
        assert node_is_eligible(node, [decode, prefill]) is True

    def test_union_eligibility_neither_role_accepts(self):
        node = self._node(gpu_product="NVIDIA-V100")
        decode = NodeFilter(required_gpu_products={"NVIDIA-H100-80GB-HBM3"})
        prefill = NodeFilter(required_gpu_products={"NVIDIA-A100-40GB"})
        assert node_is_eligible(node, [decode, prefill]) is False

    def test_cordon_excludes_regardless_of_tolerations(self):
        node = self._node(unschedulable=True)
        f = NodeFilter(tolerations=[
            {"key": "node.kubernetes.io/unschedulable", "operator": "Exists"}
        ])
        assert node_is_eligible(node, [f]) is False


class TestExtractNodeFilters:
    def _scenario(self, *, helm_values=None):
        return {
            "scenario": [{
                "name": "test",
                "model": {"helmValues": helm_values or {}},
            }]
        }

    def test_empty_scenario_returns_empty_dict(self):
        assert extract_node_filters({}) == {}

    def test_no_helm_values_returns_empty_dict(self):
        assert extract_node_filters(self._scenario()) == {}

    def test_decode_with_no_affinity_yields_unconstrained_filter(self):
        scenario = self._scenario(helm_values={"decode": {}})
        result = extract_node_filters(scenario)
        assert "decode" in result
        assert result["decode"].required_gpu_products == frozenset()
        assert result["decode"].tolerations == ()

    def test_decode_with_gpu_product_affinity(self):
        scenario = self._scenario(helm_values={
            "decode": {
                "extraConfig": {
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [{
                                    "matchExpressions": [{
                                        "key": "nvidia.com/gpu.product",
                                        "operator": "In",
                                        "values": ["NVIDIA-H100-80GB-HBM3"],
                                    }]
                                }]
                            }
                        }
                    }
                }
            }
        })
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})

    def test_multiple_products_in_match_expression(self):
        scenario = self._scenario(helm_values={
            "decode": {
                "extraConfig": {
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [{
                                    "matchExpressions": [{
                                        "key": "nvidia.com/gpu.product",
                                        "operator": "In",
                                        "values": ["NVIDIA-H100-80GB-HBM3", "NVIDIA-H100-PCIe"],
                                    }]
                                }]
                            }
                        }
                    }
                }
            }
        })
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({
            "NVIDIA-H100-80GB-HBM3", "NVIDIA-H100-PCIe"
        })

    def test_tolerations_are_always_empty_for_now(self):
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"tolerations": [
                {"key": "app", "operator": "Equal", "value": "x", "effect": "NoSchedule"}
            ]}}
        })
        result = extract_node_filters(scenario)
        assert result["decode"].tolerations == ()

    def test_per_role_decode_and_prefill(self):
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [{
                        "key": "nvidia.com/gpu.product", "operator": "In",
                        "values": ["NVIDIA-H100-80GB-HBM3"]}]}]
                }}}}},
            "prefill": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [{
                        "key": "nvidia.com/gpu.product", "operator": "In",
                        "values": ["NVIDIA-A100-40GB"]}]}]
                }}}}},
        })
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})
        assert result["prefill"].required_gpu_products == frozenset({"NVIDIA-A100-40GB"})

    def test_ignores_non_gpu_product_match_expressions(self):
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [
                        {"key": "topology.kubernetes.io/zone", "operator": "In", "values": ["us-east-1a"]},
                        {"key": "nvidia.com/gpu.product", "operator": "In", "values": ["NVIDIA-H100-80GB-HBM3"]},
                    ]}]
                }}}}},
        })
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})

    def test_ignores_unsupported_operators(self):
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [
                        {"key": "nvidia.com/gpu.product", "operator": "NotIn", "values": ["NVIDIA-V100"]},
                    ]}]
                }}}}},
        })
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset()

    def test_warns_on_unsupported_operator_for_gpu_product(self, capsys):
        """Operator typo on the gpu.product key (e.g. lowercase 'in') should warn.

        Distinguishes intentional no-constraint from a config typo.
        """
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [
                        {"key": "nvidia.com/gpu.product", "operator": "in",
                         "values": ["NVIDIA-H100-80GB-HBM3"]},
                    ]}]
                }}}}},
        })
        extract_node_filters(scenario)
        out = capsys.readouterr().out
        assert "nvidia.com/gpu.product" in out
        assert "'in'" in out
        assert "only 'In' is supported" in out

    def test_no_warn_when_no_gpu_product_expression(self, capsys):
        """Affinity present for unrelated keys (zone) should not warn."""
        scenario = self._scenario(helm_values={
            "decode": {"extraConfig": {"affinity": {"nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{"matchExpressions": [
                        {"key": "topology.kubernetes.io/zone", "operator": "In",
                         "values": ["us-east-1a"]},
                    ]}]
                }}}}},
        })
        extract_node_filters(scenario)
        out = capsys.readouterr().out
        assert "only 'In' is supported" not in out

    def test_extracts_from_acceleratorType_schema(self):
        """Real scenarios use scenario[0].{role}.acceleratorType.labelKey/labelValue."""
        scenario = {
            "scenario": [{
                "name": "expceil",
                "decode": {
                    "replicas": 2,
                    "acceleratorType": {
                        "labelKey": "nvidia.com/gpu.product",
                        "labelValue": "NVIDIA-H100-80GB-HBM3",
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert "decode" in result
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})

    def test_acceleratorType_with_non_gpu_product_label_ignored(self):
        """A non-GPU-product labelKey produces no product constraint."""
        scenario = {
            "scenario": [{
                "decode": {
                    "acceleratorType": {
                        "labelKey": "topology.kubernetes.io/zone",
                        "labelValue": "us-east-1a",
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert "decode" in result
        assert result["decode"].required_gpu_products == frozenset()

    def test_per_role_acceleratorType(self):
        """Different acceleratorType per role yields different product sets."""
        scenario = {
            "scenario": [{
                "decode": {
                    "acceleratorType": {
                        "labelKey": "nvidia.com/gpu.product",
                        "labelValue": "NVIDIA-H100-80GB-HBM3",
                    },
                },
                "prefill": {
                    "acceleratorType": {
                        "labelKey": "nvidia.com/gpu.product",
                        "labelValue": "NVIDIA-A100-80GB",
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})
        assert result["prefill"].required_gpu_products == frozenset({"NVIDIA-A100-80GB"})

    def test_acceleratorType_warns_on_missing_labelValue(self, capsys):
        """labelKey set to the GPU product label with empty labelValue must warn."""
        scenario = {
            "scenario": [{
                "decode": {
                    "acceleratorType": {
                        "labelKey": "nvidia.com/gpu.product",
                        "labelValue": "",
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset()
        captured = capsys.readouterr()
        output = captured.err + captured.out
        assert "acceleratorType" in output
        assert "labelValue" in output
        assert "decode" in output

    def test_acceleratorType_no_warn_for_non_gpu_product_label(self, capsys):
        """A non-GPU-product labelKey is silent — not every label is a typo."""
        scenario = {
            "scenario": [{
                "decode": {
                    "acceleratorType": {
                        "labelKey": "topology.kubernetes.io/zone",
                        "labelValue": "us-east-1a",
                    },
                },
            }],
        }
        extract_node_filters(scenario)
        captured = capsys.readouterr()
        assert "acceleratorType" not in captured.err
        assert "acceleratorType" not in captured.out

    def test_acceleratorType_takes_precedence_over_helmValues_affinity(self):
        """When both schemas are present, the canonical acceleratorType wins."""
        scenario = {
            "scenario": [{
                "decode": {
                    "acceleratorType": {
                        "labelKey": "nvidia.com/gpu.product",
                        "labelValue": "NVIDIA-H100-80GB-HBM3",
                    },
                },
                "model": {
                    "helmValues": {
                        "decode": {
                            "extraConfig": {
                                "affinity": {
                                    "nodeAffinity": {
                                        "requiredDuringSchedulingIgnoredDuringExecution": {
                                            "nodeSelectorTerms": [{
                                                "matchExpressions": [{
                                                    "key": "nvidia.com/gpu.product",
                                                    "operator": "In",
                                                    "values": ["NVIDIA-A100-80GB"],
                                                }],
                                            }],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})

    def test_falls_back_to_helmValues_affinity_when_no_acceleratorType(self):
        """Users who override affinity directly via helmValues still work."""
        scenario = {
            "scenario": [{
                "model": {
                    "helmValues": {
                        "decode": {
                            "extraConfig": {
                                "affinity": {
                                    "nodeAffinity": {
                                        "requiredDuringSchedulingIgnoredDuringExecution": {
                                            "nodeSelectorTerms": [{
                                                "matchExpressions": [{
                                                    "key": "nvidia.com/gpu.product",
                                                    "operator": "In",
                                                    "values": ["NVIDIA-L40S"],
                                                }],
                                            }],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }],
        }
        result = extract_node_filters(scenario)
        assert result["decode"].required_gpu_products == frozenset({"NVIDIA-L40S"})
