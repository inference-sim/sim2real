# Deploy Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `pipeline/monitor.py`, a standalone script that watches active namespace slots alongside `deploy.py run`, auto-remediates transient pod failures, emits rules-based suggestions for known signatures, and calls the Anthropic API for novel failures, writing all findings to `health_report.md`.

**Architecture:** A poll loop reads `progress.json` to discover active slots, queries Kubernetes for pod states and events, and applies three-tier triage: tier 1 auto-remediates (evicted/OOMKilled pods), tier 2 emits rules-based suggestions, tier 3 calls the Anthropic API. All findings are written to `health_report.md`. The monitor exits when all pairs leave `running` state.

**Tech Stack:** Python 3.10+, `anthropic` SDK, `kubectl` via subprocess, existing `pipeline/lib/progress.py`.

---

## File Map

| File | Status | Purpose |
|---|---|---|
| `pipeline/lib/health.py` | Create | Pod/event parsing, triage logic, kubectl wrappers |
| `pipeline/monitor.py` | Create | CLI, poll loop, HealthReport, Anthropic API calls |
| `pipeline/tests/test_health.py` | Create | Tests for health.py (pure parsing + triage) |
| `pipeline/tests/test_monitor.py` | Create | Tests for monitor.py (slot discovery, HealthReport) |
| `requirements.txt` | Modify | Add `anthropic>=0.25.0` |
| `pipeline/README.md` | Modify | Document monitor.py usage |

### Spec deviations (intentional)

- **Pod label vs name filter:** The spec says `kubectl get pods -l modelLabel=sim2real-<experimentId>`. This plan filters pods by name containing `experiment_id` instead — more robust since the exact Helm label key is not verified. If the label is confirmed, swap the `get_pods` filter to use `-l` for precision.
- **PipelineRun task status:** The spec lists this as signal source 4. It is not used in any triage rule and is deferred — the pod-level signals cover all tier-1/2/3 decisions in this version.
- **Pending > 10 minutes (tier 3):** The spec mentions "persistent Pending with no clear event reason" as a tier-3 trigger. This requires tracking pod state across poll cycles and is deferred. Currently, a Pending pod with no matching events is silently skipped.

---

## Task 1: Scaffold health.py data structures

**Files:**
- Create: `pipeline/lib/health.py`
- Create: `pipeline/tests/test_health.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add anthropic to requirements.txt**

  Append one line to `requirements.txt`:
  ```
  anthropic>=0.25.0
  ```
  Then install: `pip install anthropic`

- [ ] **Step 2: Write failing import tests**

  Create `pipeline/tests/test_health.py`:
  ```python
  """Tests for pipeline.lib.health."""
  import json
  import pytest
  from unittest.mock import patch, MagicMock


  def test_pod_state_dataclass():
      from pipeline.lib.health import PodState
      pod = PodState(name="test-pod", phase="Running", ready=True,
                     restart_count=0, reason="", message="")
      assert pod.name == "test-pod"
      assert pod.ready is True


  def test_event_record_dataclass():
      from pipeline.lib.health import EventRecord
      evt = EventRecord(reason="FailedScheduling", message="no nodes",
                        count=1, last_timestamp="2026-01-01T00:00:00Z",
                        involved_object="my-pod")
      assert evt.reason == "FailedScheduling"


  def test_triage_result_dataclass():
      from pipeline.lib.health import TriageResult
      r = TriageResult(tier=1, message="deleted", suggestion="",
                       needs_logs=False, action="delete_pod")
      assert r.tier == 1
  ```

- [ ] **Step 3: Run test to verify it fails**

  ```bash
  python -m pytest pipeline/tests/test_health.py::test_pod_state_dataclass -v
  ```
  Expected: `FAILED` — `ModuleNotFoundError: cannot import name 'PodState'`

- [ ] **Step 4: Create `pipeline/lib/health.py`**

  ```python
  """Pod health detection and remediation for the deploy monitor."""
  from __future__ import annotations

  import json
  import subprocess
  from dataclasses import dataclass


  @dataclass
  class PodState:
      name: str
      phase: str        # Pending, Running, Failed, Succeeded, Unknown
      ready: bool
      restart_count: int
      reason: str       # OOMKilled, CrashLoopBackOff, ImagePullBackOff, Evicted, ""
      message: str


  @dataclass
  class EventRecord:
      reason: str
      message: str
      count: int
      last_timestamp: str
      involved_object: str  # pod name


  @dataclass
  class TriageResult:
      tier: int         # 1, 2, or 3
      message: str      # one-line summary for stdout
      suggestion: str   # actionable suggestion for report (empty for tier 1)
      needs_logs: bool  # whether to fetch pod logs for tier 3
      action: str       # "delete_pod", "suggest", "api", "none"


  class RemediationTracker:
      """Tracks consecutive remediation attempts per pod name."""

      def __init__(self) -> None:
          self._counts: dict[str, int] = {}

      def record(self, pod_name: str) -> int:
          """Increment counter and return new count."""
          self._counts[pod_name] = self._counts.get(pod_name, 0) + 1
          return self._counts[pod_name]

      def reset(self, pod_name: str) -> None:
          """Reset counter when pod recovers to healthy state."""
          self._counts.pop(pod_name, None)

      def count(self, pod_name: str) -> int:
          return self._counts.get(pod_name, 0)
  ```

- [ ] **Step 5: Run tests to verify they pass**

  ```bash
  python -m pytest pipeline/tests/test_health.py -v
  ```
  Expected: 3 tests PASS

- [ ] **Step 6: Commit**

  ```bash
  git add pipeline/lib/health.py pipeline/tests/test_health.py requirements.txt
  git commit -m "feat: scaffold health.py data structures and add anthropic dependency"
  ```

---

## Task 2: Implement pod and event parsing

**Files:**
- Modify: `pipeline/lib/health.py`
- Modify: `pipeline/tests/test_health.py`

- [ ] **Step 1: Write failing tests — append to `pipeline/tests/test_health.py`**

  ```python
  _PODS_JSON = json.dumps({
      "items": [
          {
              "metadata": {"name": "sim2real-ac-decode-0"},
              "status": {
                  "phase": "Running",
                  "conditions": [{"type": "Ready", "status": "False"}],
                  "containerStatuses": [{
                      "name": "vllm", "ready": False, "restartCount": 2,
                      "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                      "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}},
                  }],
              },
          },
          {
              "metadata": {"name": "sim2real-ac-epp-0"},
              "status": {
                  "phase": "Pending",
                  "conditions": [],
                  "containerStatuses": [],
              },
          },
          {
              "metadata": {"name": "sim2real-ac-decode-1"},
              "status": {
                  "phase": "Running",
                  "conditions": [{"type": "Ready", "status": "True"}],
                  "containerStatuses": [{
                      "name": "vllm", "ready": True, "restartCount": 0,
                      "lastState": {}, "state": {"running": {}},
                  }],
              },
          },
      ]
  })

  _EVENTS_JSON = json.dumps({
      "items": [
          {
              "reason": "FailedScheduling",
              "message": "0/5 nodes available: 5 Insufficient nvidia.com/gpu",
              "count": 3,
              "lastTimestamp": "2026-04-27T14:30:00Z",
              "involvedObject": {"name": "sim2real-ac-epp-0", "kind": "Pod"},
          },
          {
              "reason": "OOMKilling",
              "message": "Memory cgroup out of memory",
              "count": 1,
              "lastTimestamp": "2026-04-27T14:28:00Z",
              "involvedObject": {"name": "sim2real-ac-decode-0", "kind": "Pod"},
          },
      ]
  })


  def test_parse_pods_count():
      from pipeline.lib.health import parse_pods
      assert len(parse_pods(_PODS_JSON)) == 3


  def test_parse_pods_oom_killed():
      from pipeline.lib.health import parse_pods
      pods = parse_pods(_PODS_JSON)
      p = next(x for x in pods if x.name == "sim2real-ac-decode-0")
      assert p.reason == "OOMKilled"
      assert p.restart_count == 2
      assert p.ready is False


  def test_parse_pods_pending():
      from pipeline.lib.health import parse_pods
      pods = parse_pods(_PODS_JSON)
      p = next(x for x in pods if x.name == "sim2real-ac-epp-0")
      assert p.phase == "Pending"
      assert p.ready is False


  def test_parse_pods_healthy():
      from pipeline.lib.health import parse_pods
      pods = parse_pods(_PODS_JSON)
      p = next(x for x in pods if x.name == "sim2real-ac-decode-1")
      assert p.ready is True
      assert p.reason == ""


  def test_parse_events_count():
      from pipeline.lib.health import parse_events
      assert len(parse_events(_EVENTS_JSON)) == 2


  def test_parse_events_fields():
      from pipeline.lib.health import parse_events
      events = parse_events(_EVENTS_JSON)
      sched = next(e for e in events if e.reason == "FailedScheduling")
      assert sched.involved_object == "sim2real-ac-epp-0"
      assert sched.count == 3
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_health.py::test_parse_pods_count -v
  ```
  Expected: `FAILED` — `cannot import name 'parse_pods'`

- [ ] **Step 3: Implement `parse_pods` and `parse_events` — add to `pipeline/lib/health.py` after `RemediationTracker`**

  ```python
  def parse_pods(json_str: str) -> list[PodState]:
      """Parse `kubectl get pods -o json` output into PodState list."""
      data = json.loads(json_str)
      pods = []
      for item in data.get("items", []):
          name = item["metadata"]["name"]
          status = item.get("status", {})
          phase = status.get("phase", "Unknown")
          ready = any(
              c.get("type") == "Ready" and c.get("status") == "True"
              for c in status.get("conditions", [])
          )
          reason = ""
          message = ""
          restart_count = 0
          for cs in status.get("containerStatuses", []):
              restart_count = max(restart_count, cs.get("restartCount", 0))
              last_term = cs.get("lastState", {}).get("terminated", {})
              if last_term.get("reason"):
                  reason = last_term["reason"]
                  message = last_term.get("message", "")
              waiting = cs.get("state", {}).get("waiting", {})
              if waiting.get("reason") and not reason:
                  reason = waiting["reason"]
                  message = waiting.get("message", "")
          if status.get("reason") == "Evicted":
              reason = "Evicted"
              message = status.get("message", "")
          pods.append(PodState(name=name, phase=phase, ready=ready,
                               restart_count=restart_count, reason=reason,
                               message=message))
      return pods


  def parse_events(json_str: str) -> list[EventRecord]:
      """Parse `kubectl get events -o json` output into EventRecord list."""
      data = json.loads(json_str)
      return [
          EventRecord(
              reason=item.get("reason", ""),
              message=item.get("message", ""),
              count=item.get("count", 1),
              last_timestamp=item.get("lastTimestamp", ""),
              involved_object=item.get("involvedObject", {}).get("name", ""),
          )
          for item in data.get("items", [])
      ]
  ```

- [ ] **Step 4: Run all health tests**

  ```bash
  python -m pytest pipeline/tests/test_health.py -v
  ```
  Expected: 9 tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/lib/health.py pipeline/tests/test_health.py
  git commit -m "feat: implement pod and event parsing in health.py"
  ```

---

## Task 3: Implement triage logic

**Files:**
- Modify: `pipeline/lib/health.py`
- Modify: `pipeline/tests/test_health.py`

- [ ] **Step 1: Write failing tests — append to `pipeline/tests/test_health.py`**

  ```python
  def _make_pod(**kwargs) -> "PodState":
      from pipeline.lib.health import PodState
      defaults = dict(name="p", phase="Running", ready=False,
                      restart_count=0, reason="", message="")
      defaults.update(kwargs)
      return PodState(**defaults)


  def _make_event(reason="", message="", involved_object="p") -> "EventRecord":
      from pipeline.lib.health import EventRecord
      return EventRecord(reason=reason, message=message, count=1,
                         last_timestamp="", involved_object=involved_object)


  def test_triage_healthy_pod_returns_none():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(phase="Running", ready=True, reason="")
      assert triage_pod(pod, [], RemediationTracker()) is None


  def test_triage_evicted():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(reason="Evicted", phase="Failed")
      result = triage_pod(pod, [], RemediationTracker())
      assert result.tier == 1
      assert result.action == "delete_pod"


  def test_triage_oom_first_attempt():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(reason="OOMKilled")
      result = triage_pod(pod, [], RemediationTracker())
      assert result.tier == 1
      assert result.action == "delete_pod"
      assert "1/2" in result.message


  def test_triage_oom_second_attempt():
      from pipeline.lib.health import triage_pod, RemediationTracker
      tracker = RemediationTracker()
      tracker.record("p")  # first attempt already recorded
      pod = _make_pod(name="p", reason="OOMKilled")
      result = triage_pod(pod, [], tracker)
      assert result.tier == 1
      assert result.action == "delete_pod"
      assert "2/2" in result.message


  def test_triage_oom_escalates_on_third():
      from pipeline.lib.health import triage_pod, RemediationTracker
      tracker = RemediationTracker()
      tracker.record("p")
      tracker.record("p")  # count is now 2; next call sees attempt 3
      pod = _make_pod(name="p", reason="OOMKilled")
      result = triage_pod(pod, [], tracker)
      assert result.tier == 2
      assert result.action == "suggest"
      assert "gpu-memory-utilization" in result.suggestion


  def test_triage_image_pull_backoff():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(reason="ImagePullBackOff", phase="Pending",
                      message="Back-off pulling ghcr.io/example/bad:tag")
      result = triage_pod(pod, [], RemediationTracker())
      assert result.tier == 2
      assert result.action == "suggest"
      assert "env_defaults.yaml" in result.suggestion


  def test_triage_failed_scheduling_gpu():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(phase="Pending")
      events = [_make_event(reason="FailedScheduling",
                            message="0/5 nodes available: 5 Insufficient nvidia.com/gpu")]
      result = triage_pod(pod, events, RemediationTracker())
      assert result.tier == 2
      assert "affinity" in result.suggestion.lower() or "nvidia" in result.suggestion.lower()


  def test_triage_quota_exceeded():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(phase="Pending")
      events = [_make_event(reason="FailedScheduling",
                            message="exceeded quota: requests.nvidia.com/gpu=4")]
      result = triage_pod(pod, events, RemediationTracker())
      assert result.tier == 2
      assert "quota" in result.suggestion.lower()


  def test_triage_crash_loop_tier3():
      from pipeline.lib.health import triage_pod, RemediationTracker
      pod = _make_pod(reason="CrashLoopBackOff", restart_count=5)
      result = triage_pod(pod, [], RemediationTracker())
      assert result.tier == 3
      assert result.needs_logs is True
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_health.py::test_triage_evicted -v
  ```
  Expected: `FAILED` — `cannot import name 'triage_pod'`

- [ ] **Step 3: Implement `triage_pod` — add to `pipeline/lib/health.py` after `parse_events`**

  ```python
  _OOM_MAX_ATTEMPTS = 2  # tier-1 retries before escalating


  def triage_pod(
      pod: PodState,
      events: list[EventRecord],
      tracker: RemediationTracker,
  ) -> "TriageResult | None":
      """Return a TriageResult if the pod needs attention, None if healthy.

      Does NOT modify tracker — caller records remediation after acting.
      """
      pod_events = [e for e in events if e.involved_object == pod.name]

      if pod.phase == "Running" and pod.ready:
          return None

      # Tier 1: Evicted
      if pod.reason == "Evicted":
          return TriageResult(
              tier=1, action="delete_pod", needs_logs=False,
              message=f"{pod.name}: Evicted → deleting pod",
              suggestion="",
          )

      # Tier 1/2: OOMKilled
      if pod.reason == "OOMKilled":
          attempt = tracker.count(pod.name) + 1
          if attempt <= _OOM_MAX_ATTEMPTS:
              return TriageResult(
                  tier=1, action="delete_pod", needs_logs=False,
                  message=f"{pod.name}: OOMKilled (attempt {attempt}/{_OOM_MAX_ATTEMPTS}) → deleting pod",
                  suggestion="",
              )
          return TriageResult(
              tier=2, action="suggest", needs_logs=False,
              message=f"{pod.name}: OOMKilled (attempt {attempt}) — persistent",
              suggestion=(
                  "Persistent OOM: reduce --gpu-memory-utilization (e.g. 0.85), "
                  "--max-model-len, or replica count in "
                  "env_defaults.yaml → stack.model.helmValues.decode.containers"
              ),
          )

      # Tier 2: Image pull failure
      if pod.reason in ("ImagePullBackOff", "ErrImagePull"):
          img_detail = next(
              (e.message for e in pod_events if "pull" in e.message.lower()),
              pod.message,
          )
          return TriageResult(
              tier=2, action="suggest", needs_logs=False,
              message=f"{pod.name}: {pod.reason}",
              suggestion=(
                  f"Image pull failed: {img_detail}\n"
                  "Check env_defaults.yaml → stack.model.vllm_image "
                  "or stack.gaie.epp_image.build.tag"
              ),
          )

      # Tier 2: Scheduling failure
      if pod.phase == "Pending":
          sched = next((e for e in pod_events if e.reason == "FailedScheduling"), None)
          if sched:
              msg_lower = sched.message.lower()
              if "quota" in msg_lower or "exceeded" in msg_lower:
                  return TriageResult(
                      tier=2, action="suggest", needs_logs=False,
                      message=f"{pod.name}: Pending (resource quota exceeded)",
                      suggestion=f"Resource quota exhausted: {sched.message}",
                  )
              if "insufficient" in msg_lower or "nodes available" in msg_lower:
                  return TriageResult(
                      tier=2, action="suggest", needs_logs=False,
                      message=f"{pod.name}: Pending (no nodes match GPU affinity)",
                      suggestion=(
                          f"No schedulable nodes: {sched.message}\n"
                          "Check nodeAffinity in env_defaults.yaml → "
                          "stack.model.helmValues.decode.extraConfig.affinity"
                      ),
                  )

      # Tier 2: Startup probe timeout
      if pod.phase == "Running" and not pod.ready:
          startup_fail = next(
              (e for e in pod_events
               if e.reason == "Unhealthy" and "startup probe" in e.message.lower()),
              None,
          )
          if startup_fail:
              return TriageResult(
                  tier=2, action="suggest", needs_logs=False,
                  message=f"{pod.name}: startup probe failing",
                  suggestion=(
                      "Startup probe timing out before model finishes loading.\n"
                      "Increase failureThreshold in "
                      "env_defaults.yaml → stack.model.helmValues.decode.containers"
                      "[].extraConfig.startupProbe.failureThreshold"
                  ),
              )

      # Tier 3: CrashLoopBackOff or other failure requiring log analysis
      if pod.reason == "CrashLoopBackOff" or pod.phase in ("Failed", "Unknown"):
          return TriageResult(
              tier=3, action="api", needs_logs=True,
              message=f"{pod.name}: {pod.reason or pod.phase} — API diagnosis",
              suggestion="",
          )

      return None
  ```

- [ ] **Step 4: Run all health tests**

  ```bash
  python -m pytest pipeline/tests/test_health.py -v
  ```
  Expected: 18 tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/lib/health.py pipeline/tests/test_health.py
  git commit -m "feat: implement triage_pod and RemediationTracker in health.py"
  ```

---

## Task 4: Implement kubectl action wrappers

**Files:**
- Modify: `pipeline/lib/health.py`
- Modify: `pipeline/tests/test_health.py`

- [ ] **Step 1: Write failing tests — append to `pipeline/tests/test_health.py`**

  ```python
  def test_get_pods_calls_kubectl():
      from pipeline.lib.health import get_pods
      mock_result = MagicMock()
      mock_result.returncode = 0
      mock_result.stdout = _PODS_JSON
      with patch("subprocess.run", return_value=mock_result) as mock_run:
          pods = get_pods("kalantar-0", "ac")
      cmd = mock_run.call_args[0][0]
      assert "kubectl" in cmd
      assert "kalantar-0" in " ".join(cmd)
      assert len(pods) == 3


  def test_get_pods_filters_by_experiment_id():
      from pipeline.lib.health import get_pods
      mock_result = MagicMock()
      mock_result.returncode = 0
      mock_result.stdout = _PODS_JSON
      with patch("subprocess.run", return_value=mock_result):
          pods = get_pods("kalantar-0", "ac")
      assert all("ac" in p.name for p in pods)


  def test_get_pods_returns_empty_on_error():
      from pipeline.lib.health import get_pods
      mock_result = MagicMock()
      mock_result.returncode = 1
      mock_result.stdout = ""
      with patch("subprocess.run", return_value=mock_result):
          assert get_pods("kalantar-0", "ac") == []


  def test_get_events_calls_kubectl():
      from pipeline.lib.health import get_events
      mock_result = MagicMock()
      mock_result.returncode = 0
      mock_result.stdout = _EVENTS_JSON
      with patch("subprocess.run", return_value=mock_result):
          events = get_events("kalantar-0")
      assert len(events) == 2


  def test_delete_pod_calls_kubectl():
      from pipeline.lib.health import delete_pod
      mock_result = MagicMock()
      mock_result.returncode = 0
      with patch("subprocess.run", return_value=mock_result) as mock_run:
          result = delete_pod("kalantar-0", "sim2real-ac-decode-0")
      assert result is True
      cmd = mock_run.call_args[0][0]
      assert "delete" in cmd
      assert "sim2real-ac-decode-0" in cmd


  def test_get_pod_logs_previous_flag():
      from pipeline.lib.health import get_pod_logs
      mock_result = MagicMock()
      mock_result.returncode = 0
      mock_result.stdout = "some logs"
      with patch("subprocess.run", return_value=mock_result) as mock_run:
          logs = get_pod_logs("kalantar-0", "sim2real-ac-decode-0",
                              tail=200, previous=True)
      assert logs == "some logs"
      cmd = mock_run.call_args[0][0]
      assert "--previous" in cmd
      assert "--tail=200" in cmd
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_health.py::test_get_pods_calls_kubectl -v
  ```
  Expected: `FAILED` — `cannot import name 'get_pods'`

- [ ] **Step 3: Implement kubectl wrappers — add to `pipeline/lib/health.py` after `triage_pod`**

  ```python
  def _kubectl(*args: str) -> "tuple[int, str]":
      """Run kubectl with args. Returns (returncode, stdout)."""
      result = subprocess.run(
          ["kubectl", *args],
          check=False, text=True, capture_output=True,
      )
      return result.returncode, result.stdout


  def get_pods(namespace: str, experiment_id: str) -> list[PodState]:
      """Return pod states for pods whose name contains experiment_id."""
      rc, stdout = _kubectl("get", "pods", f"-n={namespace}", "-o", "json")
      if rc != 0 or not stdout.strip():
          return []
      return [p for p in parse_pods(stdout) if experiment_id in p.name]


  def get_events(namespace: str) -> list[EventRecord]:
      """Return recent events from the namespace."""
      rc, stdout = _kubectl(
          "get", "events", f"-n={namespace}",
          "--sort-by=lastTimestamp", "-o", "json",
      )
      if rc != 0 or not stdout.strip():
          return []
      return parse_events(stdout)


  def get_pod_logs(namespace: str, pod_name: str,
                   tail: int = 200, previous: bool = False) -> str:
      """Fetch pod logs. Returns empty string on error."""
      cmd = ["logs", pod_name, f"-n={namespace}", f"--tail={tail}"]
      if previous:
          cmd.append("--previous")
      rc, stdout = _kubectl(*cmd)
      return stdout if rc == 0 else ""


  def delete_pod(namespace: str, pod_name: str) -> bool:
      """Delete a pod. Returns True on success."""
      rc, _ = _kubectl("delete", "pod", pod_name,
                       f"-n={namespace}", "--ignore-not-found")
      return rc == 0
  ```

- [ ] **Step 4: Run all health tests**

  ```bash
  python -m pytest pipeline/tests/test_health.py -v
  ```
  Expected: 24 tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/lib/health.py pipeline/tests/test_health.py
  git commit -m "feat: add kubectl wrappers to health.py"
  ```

---

## Task 5: HealthReport writer and monitor.py skeleton

**Files:**
- Create: `pipeline/monitor.py`
- Create: `pipeline/tests/test_monitor.py`

- [ ] **Step 1: Write failing tests for HealthReport**

  Create `pipeline/tests/test_monitor.py`:
  ```python
  """Tests for pipeline.monitor."""
  import json
  from pathlib import Path


  def test_health_report_creates_file(tmp_path):
      from pipeline.monitor import HealthReport
      report = HealthReport(tmp_path / "health_report.md")
      report.add_finding(
          timestamp="2026-04-27 14:32:11",
          namespace="kalantar-0",
          pair_key="wl-chatbot-mid-treatment",
          pod_name="sim2real-ac-decode-0",
          signal="OOMKilled (attempt 3 — escalating)",
          action_taken="none",
          diagnosis="GPU memory exceeded.",
          suggestion="Reduce --gpu-memory-utilization to 0.85",
          tier=3,
      )
      content = (tmp_path / "health_report.md").read_text()
      assert "kalantar-0" in content
      assert "wl-chatbot-mid-treatment" in content
      assert "OOMKilled" in content
      assert "GPU memory exceeded" in content


  def test_health_report_summary_counts(tmp_path):
      from pipeline.monitor import HealthReport
      path = tmp_path / "health_report.md"
      report = HealthReport(path)
      report.add_finding("2026-04-27 14:00:00", "ns-0", "wl-a", "pod-a",
                         "Evicted", "deleted pod", "", "", tier=1)
      report.add_finding("2026-04-27 14:01:00", "ns-0", "wl-b", "pod-b",
                         "OOMKilled", "none", "analysis", "fix", tier=3)
      content = path.read_text()
      assert "2 finding" in content.lower()


  def test_health_report_preserves_on_reopen(tmp_path):
      from pipeline.monitor import HealthReport
      path = tmp_path / "health_report.md"
      r1 = HealthReport(path)
      r1.add_finding("2026-04-27 14:00:00", "ns-0", "wl-a", "pod-a",
                     "Evicted", "deleted", "", "", tier=1)
      r2 = HealthReport(path)
      r2.add_finding("2026-04-27 14:01:00", "ns-0", "wl-b", "pod-b",
                     "OOMKilled", "deleted", "", "", tier=1)
      content = path.read_text()
      assert "wl-a" in content
      assert "wl-b" in content
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_monitor.py -v
  ```
  Expected: `FAILED` — `cannot import name 'HealthReport'`

- [ ] **Step 3: Create `pipeline/monitor.py`**

  ```python
  #!/usr/bin/env python3
  """sim2real deploy monitor — watches active slots and diagnoses pod failures."""
  from __future__ import annotations

  import argparse
  import json
  import os
  import sys
  import time
  from dataclasses import dataclass
  from pathlib import Path

  _REPO_ROOT = Path(__file__).resolve().parent.parent
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))

  from pipeline.lib.health import (
      RemediationTracker, get_pods, get_events, get_pod_logs,
      delete_pod, triage_pod, _kubectl,
  )
  from pipeline.lib.progress import LocalProgressStore

  try:
      import anthropic
  except ImportError:
      anthropic = None  # type: ignore[assignment]

  # ── Color helpers (mirrors deploy.py) ────────────────────────────────────────
  _tty = sys.stdout.isatty()


  def _c(code: str, text: str) -> str:
      return f"\033[{code}m{text}\033[0m" if _tty else text


  def info(msg: str)  -> None: print(_c("34", "[INFO]  ") + msg)
  def ok(msg: str)    -> None: print(_c("32", "[OK]    ") + msg)
  def warn(msg: str)  -> None: print(_c("33", "[WARN]  ") + msg)
  def err(msg: str)   -> None: print(_c("31", "[ERROR] ") + msg, file=sys.stderr)


  # ── Health report ─────────────────────────────────────────────────────────────
  @dataclass
  class _Finding:
      timestamp: str
      namespace: str
      pair_key: str
      pod_name: str
      signal: str
      action_taken: str
      diagnosis: str
      suggestion: str
      tier: int


  class HealthReport:
      """Manages health_report.md. Regenerated on every write; prior session
      content preserved as an opaque block."""

      def __init__(self, path: Path) -> None:
          self._path = Path(path)
          self._findings: list[_Finding] = []
          self._prior_text = ""
          if self._path.exists():
              self._prior_text = self._path.read_text()

      def add_finding(
          self,
          timestamp: str,
          namespace: str,
          pair_key: str,
          pod_name: str,
          signal: str,
          action_taken: str,
          diagnosis: str,
          suggestion: str,
          tier: int,
      ) -> None:
          self._findings.append(_Finding(
              timestamp=timestamp, namespace=namespace, pair_key=pair_key,
              pod_name=pod_name, signal=signal, action_taken=action_taken,
              diagnosis=diagnosis, suggestion=suggestion, tier=tier,
          ))
          self._write()

      def _write(self) -> None:
          n = len(self._findings)
          tier_counts: dict[int, int] = {}
          for f in self._findings:
              tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
          summary_parts = " | ".join(
              f"tier-{t}: {c}" for t, c in sorted(tier_counts.items())
          )
          lines = [
              "# Deploy Monitor Health Report\n\n",
              f"**{n} new finding{'s' if n != 1 else ''} this session**",
              f" — {summary_parts}\n\n---\n",
          ]
          for f in self._findings:
              lines.append(f"\n## {f.timestamp}  {f.namespace} / {f.pair_key}\n")
              lines.append(f"\n**Signal:** {f.signal}")
              lines.append(f"\n**Pod:** {f.pod_name}")
              lines.append(f"\n**Action taken:** {f.action_taken}")
              if f.diagnosis:
                  lines.append(f"\n\n**Diagnosis (Claude):**\n{f.diagnosis}")
              if f.suggestion:
                  lines.append(f"\n\n**Suggested fix:**\n  {f.suggestion}")
              lines.append("\n")
          if self._prior_text:
              lines.append("\n---\n\n## Prior session findings\n\n")
              lines.append(self._prior_text)
          self._path.write_text("".join(lines))
  ```

- [ ] **Step 4: Run tests**

  ```bash
  python -m pytest pipeline/tests/test_monitor.py -v
  ```
  Expected: 3 tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/monitor.py pipeline/tests/test_monitor.py
  git commit -m "feat: add HealthReport to monitor.py skeleton"
  ```

---

## Task 6: Poll loop and CLI (tier 1 + 2)

**Files:**
- Modify: `pipeline/monitor.py`
- Modify: `pipeline/tests/test_monitor.py`

- [ ] **Step 1: Write failing tests — append to `pipeline/tests/test_monitor.py`**

  ```python
  _PROGRESS_MIXED = {
      "wl-chatbot-mid-treatment": {
          "workload": "chatbot_mid", "package": "treatment",
          "status": "running", "namespace": "kalantar-0", "retries": 0,
      },
      "wl-chatbot-mid-baseline": {
          "workload": "chatbot_mid", "package": "baseline",
          "status": "done", "namespace": None, "retries": 0,
      },
      "wl-load-treatment": {
          "workload": "load", "package": "treatment",
          "status": "running", "namespace": "kalantar-1", "retries": 0,
      },
  }

  _PROGRESS_ALL_DONE = {
      "wl-chatbot-mid-treatment": {
          "workload": "chatbot_mid", "package": "treatment",
          "status": "done", "namespace": None, "retries": 0,
      },
  }


  def test_resolve_active_slots_returns_running_only():
      from pipeline.monitor import _resolve_active_slots
      slots = _resolve_active_slots(_PROGRESS_MIXED)
      assert set(slots.keys()) == {"kalantar-0", "kalantar-1"}
      assert "wl-chatbot-mid-treatment" in slots["kalantar-0"]


  def test_resolve_active_slots_empty_when_all_done():
      from pipeline.monitor import _resolve_active_slots
      assert _resolve_active_slots(_PROGRESS_ALL_DONE) == {}


  def test_work_remaining_true_when_running():
      from pipeline.monitor import _work_remaining
      assert _work_remaining(_PROGRESS_MIXED) is True


  def test_work_remaining_false_when_all_done():
      from pipeline.monitor import _work_remaining
      assert _work_remaining(_PROGRESS_ALL_DONE) is False
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_monitor.py::test_resolve_active_slots_returns_running_only -v
  ```
  Expected: `FAILED` — `cannot import name '_resolve_active_slots'`

- [ ] **Step 3: Add slot helpers, poll cycle, and CLI to `pipeline/monitor.py`**

  Append after the `HealthReport` class:

  ```python
  # ── Slot discovery ────────────────────────────────────────────────────────────

  def _resolve_active_slots(progress: dict) -> dict[str, list[str]]:
      """Return {namespace: [pair_key, ...]} for all running pairs."""
      slots: dict[str, list[str]] = {}
      for key, entry in progress.items():
          if entry.get("status") == "running" and entry.get("namespace"):
              ns = entry["namespace"]
              slots.setdefault(ns, []).append(key)
      return slots


  def _work_remaining(progress: dict) -> bool:
      return any(v.get("status") == "running" for v in progress.values())


  # ── Setup config ──────────────────────────────────────────────────────────────

  def _load_setup_config(experiment_root: Path) -> dict:
      for p in [
          experiment_root / "workspace" / "setup_config.json",
          _REPO_ROOT / "workspace" / "setup_config.json",
      ]:
          if p.exists():
              return json.loads(p.read_text())
      return {}


  # ── One poll cycle ────────────────────────────────────────────────────────────

  def _now() -> str:
      import datetime
      return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


  def _poll_once(
      progress: dict,
      experiment_id: str,
      tracker: RemediationTracker,
      report: HealthReport,
      log_lines: int,
  ) -> None:
      """Run one health-check pass over all active slots."""
      slots = _resolve_active_slots(progress)
      for ns, pair_keys in slots.items():
          events = get_events(ns)
          pods = get_pods(ns, experiment_id)
          for pod in pods:
              result = triage_pod(pod, events, tracker)
              if result is None:
                  if pod.ready:
                      tracker.reset(pod.name)
                  continue

              ts = _now()
              pair_label = pair_keys[0] if pair_keys else "?"
              action_taken = "none"

              if result.tier == 1:
                  if result.action == "delete_pod":
                      tracker.record(pod.name)
                      success = delete_pod(ns, pod.name)
                      action_taken = "deleted pod" if success else "delete failed"
                  warn(f"{ns} / {pair_label}: {result.message}")
                  report.add_finding(
                      timestamp=ts, namespace=ns, pair_key=pair_label,
                      pod_name=pod.name, signal=result.message,
                      action_taken=action_taken,
                      diagnosis="", suggestion="", tier=1,
                  )

              elif result.tier == 2:
                  warn(f"{ns} / {pair_label}: {result.message}")
                  report.add_finding(
                      timestamp=ts, namespace=ns, pair_key=pair_label,
                      pod_name=pod.name, signal=result.message,
                      action_taken=action_taken,
                      diagnosis="", suggestion=result.suggestion, tier=2,
                  )

              elif result.tier == 3:
                  err(f"{ns} / {pair_label}: {result.message}")
                  logs = ""
                  if result.needs_logs:
                      logs = get_pod_logs(ns, pod.name, tail=log_lines)
                      if pod.restart_count > 0:
                          prev = get_pod_logs(ns, pod.name, tail=100, previous=True)
                          if prev:
                              logs = (f"=== previous container ===\n{prev}\n"
                                      f"=== current ===\n{logs}")
                  _, describe_out = _kubectl("describe", "pod", pod.name, f"-n={ns}")
                  events_summary = "\n".join(
                      f"{e.reason}: {e.message}"
                      for e in events if e.involved_object == pod.name
                  )
                  diagnosis = _diagnose_with_api(
                      pod_name=pod.name, namespace=ns, signal=result.message,
                      describe_output=describe_out, logs=logs,
                      events_summary=events_summary, log_lines=log_lines,
                  )
                  report.add_finding(
                      timestamp=ts, namespace=ns, pair_key=pair_label,
                      pod_name=pod.name, signal=result.message,
                      action_taken=action_taken,
                      diagnosis=diagnosis, suggestion=result.suggestion, tier=3,
                  )
                  info(f"{ns}: diagnosis written to {report._path.name}")


  # ── Anthropic API diagnosis (stub — implemented in Task 7) ───────────────────

  def _diagnose_with_api(
      pod_name: str,
      namespace: str,
      signal: str,
      describe_output: str,
      logs: str,
      events_summary: str,
      log_lines: int = 200,
  ) -> str:
      return "(API diagnosis not yet configured)"


  # ── CLI ───────────────────────────────────────────────────────────────────────

  def build_parser() -> argparse.ArgumentParser:
      p = argparse.ArgumentParser(
          prog="monitor.py",
          description="sim2real deploy monitor — watches active slots for pod failures",
      )
      p.add_argument("--experiment-root", metavar="PATH", dest="experiment_root",
                     help="Root of the experiment repo (default: cwd)")
      p.add_argument("--run", metavar="NAME",
                     help="Run name (default: current_run from setup_config.json)")
      p.add_argument("--interval", type=int, default=30, metavar="SECONDS",
                     help="Poll interval in seconds [30]")
      p.add_argument("--log-lines", type=int, default=200, dest="log_lines",
                     help="Tail depth for pod logs sent to API [200]")
      return p


  def main() -> None:
      parser = build_parser()
      args = parser.parse_args()
      print(_c("36", "\n━━━ sim2real-monitor ━━━\n"))

      experiment_root = (Path(args.experiment_root).resolve()
                         if args.experiment_root else Path.cwd())
      setup_config = _load_setup_config(experiment_root)
      run_name = args.run or setup_config.get("current_run", "")
      if not run_name:
          err("No run name. Use --run NAME or set current_run in setup_config.json.")
          sys.exit(1)

      run_dir = experiment_root / "workspace" / "runs" / run_name
      if not run_dir.exists():
          err(f"Run directory not found: {run_dir}")
          sys.exit(1)

      report = HealthReport(run_dir / "health_report.md")
      tracker = RemediationTracker()
      store = LocalProgressStore(run_dir / "progress.json")

      info(f"Monitoring run '{run_name}' (interval: {args.interval}s)")
      info(f"Report: {run_dir}/health_report.md")

      while True:
          progress = store.load()
          if not _work_remaining(progress):
              info("No active pairs remaining — exiting.")
              break
          _poll_once(progress, run_name, tracker, report, args.log_lines)
          time.sleep(args.interval)


  if __name__ == "__main__":
      main()
  ```

- [ ] **Step 4: Run all monitor tests**

  ```bash
  python -m pytest pipeline/tests/test_monitor.py -v
  ```
  Expected: 7 tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/monitor.py pipeline/tests/test_monitor.py
  git commit -m "feat: implement monitor.py poll loop and CLI (tier 1+2)"
  ```

---

## Task 7: Anthropic API diagnosis

**Files:**
- Modify: `pipeline/monitor.py`
- Modify: `pipeline/tests/test_monitor.py`

- [ ] **Step 1: Write failing tests — append to `pipeline/tests/test_monitor.py`**

  ```python
  def test_diagnose_with_api_returns_text():
      from unittest.mock import patch, MagicMock
      from pipeline.monitor import _diagnose_with_api
      import os

      mock_client = MagicMock()
      mock_msg = MagicMock()
      mock_msg.content = [MagicMock(text="The pod OOMKilled because of X.")]
      mock_client.messages.create.return_value = mock_msg

      with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
          with patch("pipeline.monitor.anthropic") as mock_anthropic:
              mock_anthropic.Anthropic.return_value = mock_client
              result = _diagnose_with_api(
                  pod_name="sim2real-ac-decode-0",
                  namespace="kalantar-0",
                  signal="CrashLoopBackOff",
                  describe_output="Name: sim2real-ac-decode-0\n...",
                  logs="ERROR: CUDA out of memory\n",
                  events_summary="OOMKilling: Memory cgroup out of memory",
              )
      assert "OOMKilled" in result


  def test_diagnose_with_api_no_key():
      from pipeline.monitor import _diagnose_with_api
      import os

      with patch.dict(os.environ, {}, clear=True):
          result = _diagnose_with_api(
              pod_name="pod", namespace="ns", signal="CrashLoopBackOff",
              describe_output="", logs="", events_summary="",
          )
      assert "ANTHROPIC_API_KEY" in result or "unavailable" in result.lower() or result == ""
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  python -m pytest pipeline/tests/test_monitor.py::test_diagnose_with_api_returns_text -v
  ```
  Expected: `FAILED` — assertion fails (returns stub text)

- [ ] **Step 3: Replace the `_diagnose_with_api` stub in `pipeline/monitor.py`**

  Find and replace the stub function:

  ```python
  _DIAGNOSIS_MODEL = "claude-haiku-4-5-20251001"

  _DIAGNOSIS_PROMPT = """\
  You are a Kubernetes operations expert diagnosing a failing pod in a sim2real \
  benchmarking pipeline.

  Namespace: {namespace}
  Pod: {pod_name}
  Signal: {signal}

  --- kubectl describe pod ---
  {describe_output}

  --- Recent events ---
  {events_summary}

  --- Pod logs (last {log_lines} lines) ---
  {logs}

  Provide:
  1. A concise diagnosis of the root cause (2-3 sentences).
  2. A specific suggested fix: the exact config key to change and the new value, \
  or the kubectl command to run.

  Keep your response under 200 words.
  """


  def _diagnose_with_api(
      pod_name: str,
      namespace: str,
      signal: str,
      describe_output: str,
      logs: str,
      events_summary: str,
      log_lines: int = 200,
  ) -> str:
      """Call Anthropic API to diagnose a pod failure. Returns diagnosis text."""
      if anthropic is None:
          return "(anthropic package not installed — pip install anthropic)"
      api_key = os.environ.get("ANTHROPIC_API_KEY", "")
      if not api_key:
          return "(ANTHROPIC_API_KEY not set — API diagnosis unavailable)"
      try:
          client = anthropic.Anthropic(api_key=api_key)
          prompt = _DIAGNOSIS_PROMPT.format(
              namespace=namespace,
              pod_name=pod_name,
              signal=signal,
              describe_output=describe_output[:3000],
              events_summary=events_summary[:1000],
              logs=logs[:4000],
              log_lines=log_lines,
          )
          message = client.messages.create(
              model=_DIAGNOSIS_MODEL,
              max_tokens=512,
              messages=[{"role": "user", "content": prompt}],
          )
          return message.content[0].text
      except Exception as exc:
          return f"(API diagnosis failed: {exc})"
  ```

- [ ] **Step 4: Run all tests**

  ```bash
  python -m pytest pipeline/tests/test_health.py pipeline/tests/test_monitor.py -v
  ```
  Expected: all tests PASS

- [ ] **Step 5: Commit**

  ```bash
  git add pipeline/monitor.py pipeline/tests/test_monitor.py
  git commit -m "feat: implement Anthropic API diagnosis in monitor.py"
  ```

---

## Task 8: Update README

**Files:**
- Modify: `pipeline/README.md`

- [ ] **Step 1: Read `pipeline/README.md` to find where to insert**

  Run:
  ```bash
  grep -n "deploy.py\|run.py\|setup.py\|prepare.py" pipeline/README.md | head -20
  ```

- [ ] **Step 2: Add monitor.py section to `pipeline/README.md`**

  After the existing `deploy.py` section, insert:

  ```markdown
  ## monitor.py

  Watches active namespace slots while `deploy.py run` is running. Detects pod failures,
  auto-remediates transient issues (tier 1), emits rules-based suggestions (tier 2), and
  calls the Anthropic API for novel failures (tier 3). Writes all findings to
  `workspace/runs/<run>/health_report.md`.

  ```bash
  # Start in a second terminal alongside deploy.py run
  python pipeline/monitor.py --experiment-root ../admission-control

  # Or background it
  python pipeline/monitor.py --experiment-root ../admission-control &
  ```

  **Requires:** `ANTHROPIC_API_KEY` in the environment for tier-3 API diagnosis.
  If unset, tier-3 findings are written with a placeholder and no API call is made.

  | Flag | Default | Description |
  |------|---------|-------------|
  | `--experiment-root PATH` | cwd | Root of the experiment repo |
  | `--run NAME` | `current_run` from setup_config.json | Run name |
  | `--interval SECONDS` | 30 | Poll interval |
  | `--log-lines N` | 200 | Tail depth for pod logs sent to API |
  ```

- [ ] **Step 3: Run the full test suite to confirm nothing broke**

  ```bash
  python -m pytest pipeline/ -v
  ```
  Expected: all tests PASS

- [ ] **Step 4: Commit**

  ```bash
  git add pipeline/README.md
  git commit -m "docs: add monitor.py to pipeline/README.md"
  ```
