# P2.9 — Bounded Provider Retry

P2.9 adds a small provider-level transient retry wrapper. The default and hard maximum are exactly 2 total attempts (one initial attempt plus at most one retry). Retryable failures are PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE, and HTTP 429/502/503/504. Permanent provider failures and MODEL_INVALID_OUTPUT are never retried here. health() is never retried. Exhaustion raises ProviderRetryExhausted while preserving the final C1.8 FailureDescriptor. This layer does not own task state, NEEDS_HUMAN, Project State, sleeps/backoff, Model Manager policy, or UAV execution.
