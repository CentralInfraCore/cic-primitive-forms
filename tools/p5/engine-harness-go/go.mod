module engineharness

go 1.25

require github.com/CentralInfraCore/cic-primitive-engine/go v0.0.0

// P5-2 (ADR 0014, decision A): the engine is the oracle. CI puts the pinned engine checkout at upstream/cic-primitive-engine.
replace github.com/CentralInfraCore/cic-primitive-engine/go => ../../../upstream/cic-primitive-engine/go
