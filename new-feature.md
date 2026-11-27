# Mandatory Feature: Learning-Driven Adaptive Execution with JSON-Based Persistent Learning

Implement an intelligent, learning-driven adaptive retry engine for the Playwright-based UX testing agent.

The system must learn from every previous failed attempt and use that knowledge to make informed decisions during subsequent retries. It must not blindly repeat the same Playwright action, locator, or execution strategy.

**Important architectural change:** Use JSON files instead of SQLite for persistent experience storage. The agent must actively query and retrieve relevant experiences from JSON before proposing every retry strategy.

## 1. Execution Policy

- Maximum retries: 3 after the initial attempt.
- Maximum total attempts: 4.
- Every attempt must have a unique execution strategy ID and a recorded strategy description.
- Every retry must be informed by all previous attempts in the current run and relevant experiences from previous runs.
- Preserve the complete learning context throughout the execution lifecycle.

After every unsuccessful attempt, execute the following workflow:

1. Capture the browser screenshot.
2. Capture the DOM snapshot and accessibility tree.
3. Record the current URL and page title.
4. Capture relevant browser events, console errors, network requests and responses.
5. Record the complete Playwright action history.
6. Record the execution result and expected versus actual outcome.
7. Perform failure triage.
8. Identify the likely failure category.
9. Analyze why the previous execution strategy failed.
10. Query the current run's attempt history.
11. Query the persistent JSON learning database for relevant successful and unsuccessful experiences.
12. Provide the accumulated evidence and learning context to the LLM.
13. Ask the LLM to propose a different, evidence-supported execution strategy.
14. Validate the proposed strategy against the allowed Playwright action schema.
15. Execute the proposed strategy.
16. Verify whether the original journey objective has been achieved.

Do not skip the learning and retrieval stages between attempts.

## 2. JSON-Based Persistent Learning Architecture

Replace SQLite entirely with a JSON-based experience storage system.

Suggested directory structure:

```text
project/
│
├── learning/
│   ├── experiences/
│   │   ├── experiences.json
│   │   └── experience_index.json
│   │
│   ├── runs/
│   │   └── run_<run_id>.json
│   │
│   ├── strategies/
│   │   └── strategy_catalog.json
│   │
│   └── learning_manager.py
│
├── artifacts/
│   └── <run_id>/
│       ├── attempt_1/
│       │   ├── screenshot.png
│       │   ├── dom_snapshot.json
│       │   ├── accessibility_snapshot.json
│       │   └── browser_events.json
│       │
│       └── attempt_2/
│
└── adaptive_retry_engine.py
```

### JSON storage responsibilities

**experiences.json**

Store persistent execution experiences across all test runs.

Each experience must contain:

- experience_id
- run_id
- persona
- journey_objective
- attempt_number
- strategy_id
- strategy_description
- page_context
- application_context
- observed_state
- evidence_references
- previous_failure_analysis
- action_details
- execution_result
- failure_category
- root_cause
- learning_eligible
- outcome
- reward, if eligible
- confidence
- validation_status
- timestamp

**experience_index.json**

Maintain a lightweight searchable index to help retrieve relevant experiences without repeatedly scanning the entire experience database.

Index experiences using relevant attributes:

- Application or website identifier
- Page or component type
- User journey
- Action type
- Locator strategy
- Failure category
- Strategy ID
- Outcome
- Validation status

The index must remain synchronized with experiences.json.

**run_<run_id>.json**

Maintain the complete execution history for an individual test run.

Store all attempts, including:

- Initial execution.
- Retry decisions.
- Previous strategies.
- Failure analysis.
- Retrieved experiences.
- LLM strategy proposals.
- Validation results.
- Final execution result.

Do not overwrite previous attempts.

### JSON persistence requirements

- Implement a dedicated JSON Learning Manager.
- Provide functions for storing, retrieving, searching, validating and updating experiences.
- Use atomic file writes to prevent corrupted JSON.
- Implement file locking or equivalent concurrency protection.
- Handle missing, malformed and partially written JSON files safely.
- Maintain schema versions for future migrations.
- Avoid duplicate experience records.
- Preserve the original evidence references.
- Never delete historical attempts when updating an experience.
- Use efficient indexed retrieval instead of loading every historical artifact into the LLM context.

The JSON experience database is the source of persistent learning across test runs.

## 3. Learning-Driven Retry Workflow

Every subsequent attempt must receive an evolving learning context.

The LLM must receive:

1. Original persona.
2. Original journey objective.
3. Current browser state.
4. Previous execution attempts.
5. Previous strategy descriptions and IDs.
6. Successful and unsuccessful actions.
7. Failure categories and root-cause analysis.
8. Evidence collected from previous attempts.
9. Relevant experiences retrieved from JSON.
10. Strategies already attempted.
11. Strategies that should be avoided.
12. Reasons why previous strategies failed.
13. Current page changes and newly observed conditions.

Do not reset this context between retries.

### Strategy selection

The LLM must analyze the available evidence and propose a suitable alternative.

For example:

If an accessible-role locator fails, possible alternatives include:

- Inspecting the accessibility tree and accessible names.
- Examining DOM structure and attributes.
- Discovering labels or placeholders.
- Identifying dynamically rendered elements.
- Investigating overlays and hidden menus.
- Correlating screenshot coordinates with DOM elements.
- Using keyboard navigation.
- Waiting for a specific page condition and observing again.

These are examples, not a mandatory fixed sequence.

The agent must choose the strategy based on the actual failure evidence.

### Strategy diversity enforcement

Maintain a list of previously attempted strategies.

Before executing a retry:

- Compare the proposed strategy with previous strategies.
- Detect identical actions and locator approaches.
- Reject strategies that simply repeat an unsuccessful approach without justification.
- Require meaningful changes in execution approach when evidence supports them.
- Permit strategy reuse only if new evidence or changed page conditions justify it.
- Record the justification whenever a strategy is reused.

Every strategy must include:

- strategy_id
- strategy_description
- reasoning
- supporting_evidence
- expected_result
- validation_conditions
- differences_from_previous_strategy

## 4. Failure Triage and Learning Eligibility

Classify every unsuccessful attempt into one of these categories:

1. AGENT_RELATED
2. APPLICATION_RELATED
3. SERVER_RELATED
4. NETWORK_RELATED
5. INFRASTRUCTURE_RELATED
6. UNKNOWN

Determine the root cause using available browser evidence, network responses, server responses, application errors and execution history.

### Learning eligibility rules

Only validated agent-related experiences may influence agent strategy rewards.

Do not update agent strategy rewards for:

- Server-side failures.
- HTTP 5xx application outages.
- Application crashes.
- Network connectivity failures.
- Browser crashes.
- Infrastructure failures.
- Unknown or uncertain root causes.

These experiences must still be saved in JSON for diagnostics and future analysis.

For application-related failures, distinguish between an actual application defect and an agent execution problem before assigning learning eligibility.

Do not penalize a strategy for failures outside the agent's control.

## 5. JSON Experience Retrieval

Before every retry, the agent must query the JSON learning database.

Implement retrieval methods such as:

```python
search_experiences(
    application_id=None,
    journey_objective=None,
    page_context=None,
    action_type=None,
    failure_category=None,
    strategy_id=None,
    outcome=None,
    validated_only=True
)
```

```python
get_similar_experiences(
    current_failure,
    current_page_context,
    current_action,
    limit=10
)
```

```python
get_strategy_history(
    application_id,
    strategy_id
)
```

```python
get_successful_recovery_strategies(
    application_id,
    journey_objective,
    page_context
)
```

The retrieval engine should prioritize:

- Experiences from the same application.
- Similar pages and UI components.
- Similar user journeys.
- Similar failure symptoms.
- Previously successful recovery strategies.
- Validated experiences with reliable evidence.

Unsuccessful experiences should also be retrieved because they help the agent avoid repeating ineffective approaches.

Do not retrieve experiences solely by keyword matching. Use a combination of structured filtering and contextual similarity.

If no relevant experiences exist, the LLM may generate a strategy using current browser evidence, but it must record that no historical experience was available.

## 6. Attempt Recording and Reward Management

Record every attempt, whether successful or unsuccessful.

Each attempt must include:

```json
{
  "attempt_number": 2,
  "strategy_id": "STRATEGY_DOM_INSPECTION_001",
  "strategy_description": "Inspect the DOM to identify the actual interactive element and its attributes.",
  "previous_failure_analysis": "The accessible-role locator did not match any visible element.",
  "failure_category": "AGENT_RELATED",
  "root_cause": "Incorrect accessible name assumption",
  "learning_eligible": true,
  "outcome": "SUCCESS",
  "reward": 1,
  "confidence": 0.92,
  "validation_status": "VALIDATED",
  "evidence_references": [
    "artifacts/run_123/attempt_2/dom_snapshot.json",
    "artifacts/run_123/attempt_2/screenshot.png"
  ]
}
```

Reward rules:

- Successful, verified agent-related recovery: positive reward.
- Failed agent-related execution: negative reward, subject to confidence and evidence.
- Server, application outage, network or infrastructure failures: no agent strategy reward.
- Unknown root cause: no agent strategy reward.
- Unverified success: do not assign a positive reward.

Reward values should be configurable.

Do not treat raw execution success as proof that the journey objective was achieved.

## 7. Playwright Action Validation

The LLM must never directly execute unrestricted browser operations.

Introduce an action validation layer.

Validate:

- Supported Playwright action.
- Locator type.
- Locator parameters.
- Required arguments.
- Timeout limits.
- Allowed interaction types.
- Expected page conditions.
- Strategy identifier.
- Strategy difference from previous attempts.

Reject malformed, unsupported or unsafe actions.

Only validated actions may reach the Playwright execution layer.

## 8. Journey Objective Verification

After every execution attempt, verify the original persona journey objective.

Use appropriate evidence, such as:

- Expected URL.
- Expected page or component.
- Visible success message.
- Expected DOM state.
- Successful form submission.
- Expected navigation.
- Application-specific completion indicator.

Do not consider an action successful merely because Playwright did not throw an exception.

A journey is successful only when the expected objective is verified.

## 9. Adaptive Retry Orchestration

Implement the following execution lifecycle:

```text
START PERSONA JOURNEY
        |
        v
INITIAL PLAYWRIGHT EXECUTION
        |
        v
VERIFY JOURNEY OBJECTIVE
        |
   +----+----+
   |         |
 SUCCESS   FAILURE
   |         |
   v         v
 END     CAPTURE EVIDENCE
             |
             v
        FAILURE TRIAGE
             |
             v
       ANALYZE ROOT CAUSE
             |
             v
       UPDATE RUN HISTORY
             |
             v
       QUERY JSON DATABASE
             |
             v
       RETRIEVE RELEVANT
         EXPERIENCES
             |
             v
       BUILD LEARNING CONTEXT
             |
             v
       LLM PROPOSES NEW
          STRATEGY
             |
             v
       VALIDATE STRATEGY
             |
        +----+----+
        |         |
     VALID      INVALID
        |         |
        v         v
     EXECUTE    REJECT AND
     RETRY      REGENERATE
        |
        v
     VERIFY OBJECTIVE
        |
   +----+----+
   |         |
 SUCCESS   FAILURE
   |         |
   v         v
  END     SAVE EXPERIENCE
             |
             v
       CHECK RETRY LIMIT
             |
        +----+----+
        |         |
      RETRY     EXHAUSTED
        |         |
        v         v
   REPEAT WITH   REPORT
   FULL CONTEXT  FAILURE
```

Stop retrying immediately if the evidence establishes that the journey cannot progress because of an external application, server, network or infrastructure failure. Record the diagnostic result rather than wasting retries.

## 10. LLM Retry Decision Prompt

Use the following prompt template for every retry:

You are an adaptive Playwright UX testing agent.

Your task is to recover from the previous execution failure by analyzing the available evidence and learning from previous execution experiences.

You must not blindly repeat a previously unsuccessful execution strategy.

### Context

- Original persona: {persona}
- Journey objective: {journey_objective}
- Run ID: {run_id}
- Current attempt: {attempt_number}
- Maximum attempts: 4
- Current URL: {current_url}
- Current browser state: {browser_state}

### Previous attempts

{attempt_history}

### Previous strategy analysis

{strategy_failure_analysis}

### Retrieved JSON learning experiences

{retrieved_experiences}

### Strategies already attempted

{attempted_strategies}

### Strategies to avoid

{strategies_to_avoid}

### Current evidence

- Screenshot: {screenshot_reference}
- DOM snapshot: {dom_snapshot}
- Accessibility snapshot: {accessibility_snapshot}
- Browser events: {browser_events}
- Network events: {network_events}
- Action history: {action_history}

### Instructions

1. Analyze the original failure using the available evidence.
2. Determine whether the failure is agent-related or external.
3. Review relevant successful and unsuccessful experiences retrieved from JSON.
4. Identify why the previous strategy failed.
5. Propose a meaningfully different execution strategy when the evidence warrants it.
6. Do not repeat the same failed locator or action without new evidence and an explicit justification.
7. Explain how the proposed strategy differs from previous attempts.
8. Define the expected result and verification conditions.
9. Return only actions supported by the allowed Playwright action schema.
10. If the evidence indicates an external failure, recommend stopping rather than generating unnecessary retries.
11. Do not invent DOM elements, selectors, accessible names or page states.
12. If evidence is insufficient, request further observation instead of guessing.

### Required output

Return structured JSON containing:

- failure_assessment
- failure_category
- root_cause
- learning_eligibility
- retrieved_experience_ids
- strategy_id
- strategy_description
- reasoning
- supporting_evidence
- differences_from_previous_strategy
- proposed_actions
- expected_result
- verification_conditions
- confidence
- retry_recommendation

## 11. Cross-Run Learning

Experiences stored in JSON must be reusable across future persona test runs.

When a new run starts:

1. Load the relevant experience index.
2. Identify the application and journey context.
3. Retrieve validated historical experiences.
4. Identify successful strategies.
5. Identify previously unsuccessful strategies.
6. Provide relevant experiences to the LLM before execution.

A strategy that succeeded previously should be considered a candidate, not automatically assumed to work in every situation.

Revalidate the strategy against the current browser state.

Do not allow unvalidated, external-failure or uncertain experiences to influence agent strategy rewards.

## 12. Logging and Observability

Provide structured logs for:

- Attempt started.
- Strategy selected.
- Strategy validation.
- Playwright action execution.
- Screenshot and DOM capture.
- Failure classification.
- JSON experience retrieval.
- Retrieved experience IDs.
- LLM retry decision.
- Strategy diversity validation.
- Learning eligibility.
- Reward calculation.
- Experience persistence.
- Journey verification.
- Retry exhaustion.

Make the complete attempt history available for debugging and reporting.

## 13. Implementation Constraints

- Use Python and Playwright.
- Use JSON instead of SQLite for persistent learning.
- Implement a dedicated JSON Learning Manager.
- Keep retry orchestration separate from browser execution.
- Keep failure triage separate from strategy selection.
- Keep strategy validation separate from LLM generation.
- Maintain a single evolving run context across retries.
- Use atomic JSON persistence and concurrency protection.
- Make the retry limit configurable, defaulting to 3 retries.
- Ensure the LLM receives retrieved JSON experiences before every retry.
- Preserve backward compatibility with existing persona journey execution wherever possible.

## 14. Important Learning Architecture Clarification

Do not claim that the system implements reinforcement learning merely because it stores experiences and rewards.

The MVP must implement:

**Experience-Based Adaptive Strategy Selection**

The agent observes failures, retrieves relevant historical experiences, analyzes previous outcomes and chooses evidence-supported strategies.

Only after sufficient validated execution data is available should a contextual bandit or another learning policy be considered.

Do not introduce an artificial reinforcement-learning model just to label the feature as reinforcement learning.

## 15. Acceptance Criteria

The implementation is complete only when:

1. Maximum total attempts are limited to four.
2. Every attempt has a strategy ID and description.
3. Every failed attempt captures diagnostic evidence.
4. Every failure undergoes root-cause triage.
5. The JSON learning database is queried before every retry.
6. All previous attempts are available to subsequent LLM decisions.
7. Retries use evidence-supported strategy changes.
8. Identical failed strategies are rejected unless reuse is justified by new evidence.
9. Successful and unsuccessful experiences are persisted.
10. External failures do not influence agent strategy rewards.
11. Validated experiences are reused across future runs.
12. The original journey objective is verified before reporting success.
13. Every attempt remains available for inspection.
14. JSON data remains consistent and recoverable after interrupted execution.
15. The system can demonstrate that a retry decision was influenced by previous attempts and retrieved historical experiences.

**Final instruction:** Inspect the existing codebase before implementing. Identify the current persona execution flow, retry mechanism, Playwright action schema, LLM integration and persistence layer. Integrate the JSON-based adaptive learning architecture into the existing implementation rather than creating a disconnected demonstration or replacing working functionality unnecessarily.