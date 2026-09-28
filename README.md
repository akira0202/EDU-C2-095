# EDU-C2-095 — SchoolGenAILearningActivityEvidenceCompletenessValidator

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: EDU

## Overview

This agent checks a teacher's proposed generative-AI learning activity against the evidence
checklist a school has approved, and returns the items that are missing or inconsistent. You give it
the activity record and the school's evidence schema; it verifies that each required item is present
and internally consistent — the stated learning objective, where and how the tool is used, the
teacher-oversight step, the guidance shown to learners, how personal information is handled, whether
guardians are notified, and which policy the activity is governed by. Each finding cites the part of
the record it came from. A language model is used only to word gaps that have already been
determined.

It is read-only and produces a checklist, not a decision: it does not judge educational quality,
approve or reject an activity, process learner data, grade anyone, or contact students or guardians.
Teacher and school-leader review remains required.

## What it returns

Every run completes and says what happened.

| Result | When |
|---|---|
| The completeness checklist | the request meets the input contract. Items that are missing or inconsistent are the **point** of the checklist, not a failure — the verdict is `correction_required` and each gap cites the schema or policy section it comes from |
| The checklist, without the reviewer summary | the optional summary could not be written or did not pass the output screen. The summary is non-authoritative wording, so losing it never withholds the checklist — the packet says `reviewer_summary_available: false` and why |
| A notice instead of a checklist, with a code | the request is one you can correct — not valid JSON, no `activity_record`, or an `evidence_schema` that is missing or unusable. The notice names what to send instead and never repeats what you sent |
| An error, with no output | the activity record carries learner-identifying data, or the run hits a defect in this agent. These are never reported as a completed request |

This agent checks against **your** school's approved evidence schema and never substitutes one of
its own. It produces a checklist for a person to read: it does not approve, reject, or grade
anything. The input contract, the codes and the full classification are in
[`docs/02_design.md`](docs/02_design.md).

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/` for the design specification and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

