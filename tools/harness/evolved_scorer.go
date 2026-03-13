package harness

import (
	"context"

	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"
)

const EvolvedScorerType = "evolved-scorer"

// compile-time interface assertion
var _ scheduling.Scorer = &EvolvedScorer{}

// EvolvedScorer adapts the harness Algorithm to the production scheduling.Scorer interface.
// PR3 provides the structural shim; PR5 wires in the actual scoring logic.
type EvolvedScorer struct {
	typedName plugin.TypedName
	alg       Algorithm
}

// NewEvolvedScorer creates an EvolvedScorer wrapping the given Algorithm.
// Panics if alg is nil — callers must provide a valid Algorithm.
func NewEvolvedScorer(alg Algorithm) *EvolvedScorer {
	if alg == nil {
		panic("NewEvolvedScorer: alg must not be nil")
	}
	return &EvolvedScorer{
		typedName: plugin.TypedName{Type: EvolvedScorerType},
		alg:       alg,
	}
}

// WithName sets the scorer's name.
func (s *EvolvedScorer) WithName(name string) *EvolvedScorer {
	s.typedName.Name = name
	return s
}

// TypedName returns the typed name of the plugin.
func (s *EvolvedScorer) TypedName() plugin.TypedName {
	return s.typedName
}

// Category returns Distribution — the evolved scorer distributes load across endpoints.
func (s *EvolvedScorer) Category() scheduling.ScorerCategory {
	return scheduling.Distribution
}

// Score scores endpoints by delegating to the wrapped Algorithm.
// PR3: returns uniform 0.5 scores (placeholder). PR5 maps Algorithm.Route() scores
// to the production endpoint scoring contract.
func (s *EvolvedScorer) Score(_ context.Context, _ *scheduling.CycleState, _ *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
	scores := make(map[scheduling.Endpoint]float64, len(endpoints))
	for _, ep := range endpoints {
		scores[ep] = 0.5
	}
	return scores
}
