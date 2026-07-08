# ADR-0007: Urgent-alert channel — harness push notification

- Status: Accepted
- Date: 2026-07-08

## Context

The three-behaviour cadence (ADR-0002) needs a way for a severe, high-confidence
event to reach a human before 08:30, working from the cloud-scheduled agent
context (ADR-0005).

## Decision

Use Claude Code's own push-notification capability to alert the operator from
the scheduled agent. No external service, no secrets, nothing to host.

## Consequences

- Zero infrastructure; works natively from the routine.
- Reaches the operator only — not a team broadcast.
- Migration path if it becomes a team tool: email (transactional API) or a
  Slack/Teams webhook (GDACS even publishes Teams AdaptiveCard payloads). Both
  add a secret and a recipient list/channel; deferred until needed.

## Alternatives considered

- **Email (transactional API)**: reaches any recipient incl. non-technical
  decision-makers; needs an API key + recipient list. Deferred.
- **Slack/Teams webhook**: team-visible ops channel; needs a webhook secret and
  a channel to own. Deferred.
