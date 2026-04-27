"""Tests for pipeline.monitor."""


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
