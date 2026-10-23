# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Python worker and loopback dashboard; framework-free HTML/CSS/JavaScript.

## Users
Individual applicants who want to focus on their work rather than repeatedly completing applications. Each runs a private local instance; no shared hosted tenant service.

## Product Purpose
Reduce the work of a private job search: discover and rank openings, submit supported applications using confirmed facts, and connect the evidence to reviewed recruiting feedback and the next action. Current discovery defaults focus on internship/new-grad SWE, ML and research-engineering roles.

## Operating Context
macOS/Linux computer or Pi 4 with 4 GB RAM, accessed locally through Raspberry Pi Connect. Explicit Claude/Codex subscription or paid API choice. Defaults run every six hours with configurable submission, attempt and model-request ceilings. Actual results depend on suitable jobs and access.

## Capabilities and Constraints

Recruiting Insights compares saved reviewed outcomes by company, exact role title, source and resume bytes with explicit response windows and evidence drill-downs. Reviewed resume-history import proposes fields from exact local PDF excerpts and requires applicant confirmation; unknown dates remain unknown, and existing or withdrawn entries are not overwritten. Neither feature makes model calls, syncs Gmail or changes submission policy.

Opt-in tailored resumes select exact experience lines from confirmed structured history for each posting, retain immutable PDF revisions and use them in supported browser applications. Optional Gmail feedback suggests recruiting categories and matching attempts for applicant review; reviewed milestones remain separate from submission confirmation. Today shows complete-history application outcomes and reviewed recruiting progress. Live employer acceptance and live mailbox matching require separate operational verification.
The operations workspace groups Today, Jobs, Materials, Profile and Connections with persistent worker controls. Optional Handshake and Work at a Startup connections share confirmed sources and application history; they start disabled and require independent authorized-access and live-flow evidence. Platform profile suggestions are review-and-copy only. Grounded narrative PDFs and introductions retain immutable revisions; official records and substantive samples require supplied originals. Routine supported applications require no individual approval. CAPTCHA/login/unknown facts and uncertain submissions go to a clickable daily queue. One-time confirmed onboarding and saved answers are reused. Employer account automation is optional and off by default; unsupported forms and unknown agreements stay held. No invented facts, duplicate submissions or CAPTCHA bypass. Remote ATS acceptance remains an external verification requirement.

## Product Principles
- The application, its exact materials, submission evidence and reviewed recruiting feedback belong to one account of the job search. Submission status and recruiting progress retain separate authority.
- Automation owns routine work; exceptions are specific and actionable.
- Unknown facts remain unknown until the user supplies them.
- Every submitted answer has provenance and a durable record.
- Throughput targets never override eligibility or application limits.
- The selected model recommends; deterministic code owns side effects.
