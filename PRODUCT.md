# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Python worker and loopback dashboard; framework-free HTML/CSS/JavaScript. The visible product name is HireMeMaybe; package, CLI, service and data-directory identifiers retain please-hire-me / hireme.

## Users
Individual applicants who want to focus on their work rather than repeatedly completing applications. Each runs a private local instance; no shared hosted tenant service.

## Product Purpose
Reduce the work of a private job search: discover and rank openings, submit supported applications using confirmed facts, and connect the evidence to reviewed recruiting feedback and the next action. Current discovery defaults focus on internship/new-grad SWE, ML and research-engineering roles.

## Operating Context
A private local instance on macOS or Linux uses the same frontend as the optional Raspberry Pi deployment. Pi Connect is an access option for a Pi, not a requirement for desktop use. A Pi 4 with 4 GB RAM remains a supported deployment context. Applicant documents and application history stay on the computer running the worker; model providers, job sites and optional Gmail connections remain external services. Explicit Claude/Codex subscription or paid API choice. Defaults run every six hours with configurable submission, attempt and model-request ceilings. Actual results depend on suitable jobs and access.

## Capabilities and Constraints

Recruiting Insights compares saved reviewed outcomes by company, exact role title, source and resume bytes with explicit response windows and evidence drill-downs. Reviewed resume-history import proposes fields from exact local PDF excerpts and requires applicant confirmation; unknown dates remain unknown, and existing or withdrawn entries are not overwritten. Neither feature makes model calls, syncs Gmail or changes submission policy.

Opt-in tailored resumes select exact experience lines from confirmed structured history for each posting, retain immutable PDF revisions and use them in supported browser applications. Optional Gmail feedback suggests recruiting categories and matching attempts for applicant review; reviewed milestones remain separate from submission confirmation. Applications shows complete-history application outcomes, reviewed recruiting progress, Recruiting Insights and full batch history. Home is a compact daily brief with truthful counts, actionable attention rows and recent activity; the complete ledger belongs in Jobs alongside posting import, preparation, coverage and source health. Live employer acceptance and live mailbox matching require separate operational verification.
The workspace navigation groups Home, Jobs, Applications, Needs you and Profile, with Settings as a utility. Profile owns confirmed facts and contextual Materials navigation; Settings owns search/automation preferences and contextual Connections and Setup navigation. Connections presents Gmail first, model access second and optional job platforms in a disclosure. Global worker status and persistent controls retain their actual availability. Viewport notifications provide non-disruptive feedback with session-only recent history; they do not replace durable application evidence. Optional Handshake and Work at a Startup connections share confirmed sources and application history; they start disabled and require independent authorized-access and live-flow evidence. Platform profile suggestions are review-and-copy only. Grounded narrative PDFs and introductions retain immutable revisions; official records and substantive samples require supplied originals. Routine supported applications require no individual approval. CAPTCHA/login/unknown facts and uncertain submissions go to a clickable daily queue. One-time confirmed onboarding and saved answers are reused. Employer account automation is optional and off by default; unsupported forms and unknown agreements stay held. No invented facts, duplicate submissions or CAPTCHA bypass. Remote ATS acceptance remains an external verification requirement.

## Product Principles
- The application, its exact materials, submission evidence and reviewed recruiting feedback belong to one account of the job search. Submission status and recruiting progress retain separate authority.
- Automation owns routine work; exceptions are specific and actionable.
- Unknown facts remain unknown until the user supplies them.
- Every submitted answer has provenance and a durable record.
- Throughput targets never override eligibility or application limits.
- The selected model recommends; deterministic code owns side effects.
