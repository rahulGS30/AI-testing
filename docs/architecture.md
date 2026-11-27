# Architecture

The project is being built in phases. The current foundation contains a FastAPI
service for health and persona discovery, typed persona/action models, and a
React + TypeScript frontend. Browser execution, LLM-driven decisions, evidence
collection, analysis, and report generation are not implemented yet.

The intended execution flow is:

1. A test request selects a persona and gives the agent a goal.
2. The browser observer captures the current page state.
3. Azure AI Foundry returns one validated action from the controlled action
   schema.
4. The Python Playwright controller executes that action and captures evidence.
5. Evidence-backed UX and axe-core accessibility analysis feed a human-reviewed
   report.

The LLM must never generate executable Playwright code. The first target is
Amazon's public storefront, restricted to searching for "iPhone 17" and adding
the selected result to the cart. The run must stop at the cart: no sign-in,
checkout, address entry, payment, or order placement. The local demo application
will remain a separate, repeatable target for later UX-analysis development.
