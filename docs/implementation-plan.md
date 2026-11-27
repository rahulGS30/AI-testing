# Implementation plan

## Phase 1 — Foundation (complete)

- Establish backend configuration, health and persona APIs, typed models, and
  the React + TypeScript frontend.
- Keep Azure secrets in environment variables and provide an explicit
  connectivity check.
- Set Amazon's iPhone 17 cart journey as the first test objective.

## Phase 2 — Browser automation (implemented; verify with Azure settings)

- Implement the Playwright controller, page observer, and command-line runner.
- Restrict navigation to HTTPS on the JSON test case's public domain and block
  local/private network destinations.
- Record per-run screenshots, action history, and WebM video.
- Stop the Amazon journey once the cart is confirmed; stop on sign-in or bot
  checks without attempting to bypass them.

## Phase 3 — AI browser agent (initial decision loop implemented)

- Connect Azure AI Foundry to a structured, validated action loop (requires
  user-provided deployment settings).
- Enforce step/action limits; never execute generated code.
- Include selected persona and observed page/action history in decisions.

## Phase 4 — Repeatable local target

- Build the local e-commerce demo application and document intentional UX
  issues separately from the agent's observations.

## Phase 5+ — Evidence, analysis, reports, and dashboard

- Add evidence collection, axe-core, evidence-grounded UX findings, severity
  prioritization, reports, execution views, and human review.

Amazon's real storefront may present sign-in prompts, personalization, regional
variations, bot defenses, or changing product availability. The agent must not
bypass those controls, use credentials, or proceed to checkout. Use the local
demo app for repeatable automated UX tests.
