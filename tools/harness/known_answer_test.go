package harness

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	sim "github.com/inference-sim/inference-sim/sim"
)

// syntheticAlgorithm implements Algorithm with score = QueueDepth / maxQueueDepth.
// Used for the known-answer test only.
type syntheticAlgorithm struct{}

func (a *syntheticAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
	const maxQueueDepth = 100.0
	scores := make(map[string]float64, len(state.Snapshots))
	for _, snap := range state.Snapshots {
		scores[snap.ID] = float64(snap.QueueDepth) / maxQueueDepth
	}
	if len(state.Snapshots) == 0 {
		return sim.RoutingDecision{Reason: "no-endpoints"}
	}
	bestID := state.Snapshots[0].ID
	bestScore := scores[bestID]
	for _, snap := range state.Snapshots[1:] {
		if scores[snap.ID] > bestScore {
			bestScore = scores[snap.ID]
			bestID = snap.ID
		}
	}
	return sim.NewRoutingDecisionWithScores(bestID, "synthetic", scores)
}

// TestKnownAnswer verifies BC-3 and BC-4: RunTuples produces scores matching
// testdata/known_answer_expected.json within 1e-6 absolute tolerance per endpoint.
func TestKnownAnswer(t *testing.T) {
	tuples := []TestTuple{
		{
			Request: sim.Request{ID: "known-req-0"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-0", QueueDepth: 0},
				{ID: "ep-ka-1", QueueDepth: 50},
				{ID: "ep-ka-2", QueueDepth: 100},
			}},
		},
		{
			Request: sim.Request{ID: "known-req-1"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-3", QueueDepth: 25},
				{ID: "ep-ka-4", QueueDepth: 75},
			}},
		},
		{
			Request: sim.Request{ID: "known-req-2"},
			State: sim.RouterState{Snapshots: []sim.RoutingSnapshot{
				{ID: "ep-ka-5", QueueDepth: 33},
				{ID: "ep-ka-6", QueueDepth: 67},
			}},
		},
	}

	data, err := os.ReadFile("testdata/known_answer_expected.json")
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	var expected []map[string]float64
	if err := json.Unmarshal(data, &expected); err != nil {
		t.Fatalf("parse fixture: %v", err)
	}
	if len(expected) != len(tuples) {
		t.Fatalf("fixture has %d entries, want %d", len(expected), len(tuples))
	}

	alg := &syntheticAlgorithm{}
	results := RunTuples(alg, tuples)

	const tol = 1e-6
	for i, result := range results {
		if result.Error != nil {
			t.Errorf("tuple %d: unexpected error: %v", i, result.Error)
			continue
		}
		for epID, wantScore := range expected[i] {
			gotScore, ok := result.SimScores[epID]
			if !ok {
				t.Errorf("tuple %d: missing score for endpoint %q", i, epID)
				continue
			}
			if diff := math.Abs(gotScore - wantScore); diff > tol {
				t.Errorf("tuple %d endpoint %q: got %.10f, want %.10f (diff=%.2e > tol=1e-6)",
					i, epID, gotScore, wantScore, diff)
			}
		}
	}
}
