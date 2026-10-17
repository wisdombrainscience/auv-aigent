# Longitudinal Evaluation Addendum — 4 h / 8 h / 10 h

## Objective

Extend the v1.6.1 longitudinal evaluation from the minimum >=4 h requirement to a
leader-requested continuous 10-hour campaign.

## Primary design

One continuous 10-hour execution.

Mandatory checkpoints:

```text
T0   = 0 min
T4   = 240 min
T8   = 480 min
T10  = 600 min
```

The simulator/evaluation process is not intentionally restarted between checkpoints.

## Metrics

At each checkpoint report, when available from the longitudinal evidence:

- RSS
- open file descriptors
- residual MAVSDK process count
- storage/evidence size
- simulator host-log size
- p95 API latency
- completed flights
- failed flights
- verifier outcome counts

## Failure preservation

An early termination is reported as incomplete at the actual duration.

A later retry is a separate replication and does not replace the failed/incomplete run.

## Alternative design

Independent 4 h, 8 h, and 10 h runs are allowed only when the study question is
cross-run reproducibility rather than one continuous 10-hour stability observation.

The three datasets must remain separate.
