---
name: HireMeMaybe
description: A private local job-search workspace with clear actions and evidence-led records.
colors:
  action: "#3155d9"
  action-hover: "#2344b7"
  apricot: "#f6b089"
  paper: "#f5f7fc"
  surface: "#fff"
  ink: "#19233b"
  muted: "#626d83"
  rule: "#dfe5f0"
  selected: "#eaf0ff"
  secondary-hover-rule: "#a4b4e3"
  confirmed-ground: "#e9f3ed"
  confirmed-ink: "#337253"
  attention-ground: "#fbf0de"
  attention-ink: "#8d641f"
  danger: "#b73745"
typography:
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "18px"
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "-0.012em"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
  metadata:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
    lineHeight: 1.5
rounded:
  panel: "12px"
  control: "8px"
  record: "6px"
spacing:
  compact: "8px"
  control-gap: "12px"
  inset: "16px"
  section: "20px"
  group: "24px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 13px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 13px"
  button-secondary-hover:
    backgroundColor: "{colors.paper}"
  text-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  navigation-active:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.action}"
    rounded: "{rounded.control}"
    padding: "9px 10px"
  chip-confirmed:
    backgroundColor: "{colors.confirmed-ground}"
    textColor: "{colors.confirmed-ink}"
    rounded: "{rounded.record}"
    padding: "4px 8px"
  chip-attention:
    backgroundColor: "{colors.attention-ground}"
    textColor: "{colors.attention-ink}"
    rounded: "{rounded.record}"
    padding: "4px 8px"
  work-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "20px"
  form-group:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "24px"
  metric-group:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
---

# Design System: HireMeMaybe

## Overview

**Creative North Star: "HireMeMaybe"**

A quiet, practical job-search workspace with a little warmth. A pale blue canvas, white work surfaces and dark ink keep records readable; cobalt defines actions and selection, while apricot supplies a small warm accent. Compact typography, open space and separator rows give dense application evidence a clear hierarchy.

This records the finished framework-free dashboard in `hireme/static/style.css`, `index.html`, `app.js` and `workspace.js`, including the final CSS cascade. The user-approved replacement identity is HireMeMaybe with Cobalt + Apricot; the Daily brief composition is recorded in the [surface brief](.impeccable/surfaces/daily-brief.md). The [README screenshot provenance](docs/images/application-desk-demo.provenance.md) identifies the synthetic source and verification limits.

**Key Characteristics:**

- Cobalt actions and pale blue selection on white and cool paper.
- Compact utility typography with explicit operational labels.
- Soft panel corners, smaller controls and flat separator rows.
- A white wordmark header and persistent desktop navigation.
- Complete record identity, source provenance and visible uncertainty.

## Colors

Cool neutrals carry the workspace; cobalt is the strong action color, with apricot used sparingly in worker status.

### Primary

- **Cobalt Action:** Primary buttons, links, selected navigation, checkbox accents, caret and keyboard focus. Hover deepens the same blue.
- **Pale Blue Selection:** Active navigation and contextual section controls; also the read-only sample banner.

### Secondary

- **Apricot:** Warm worker-status accent. The built implementation uses this sparingly; it does not fill every attention row.

### Neutral

- **Cool Paper / White Surface:** Application ground and work panels, fields, header and secondary buttons.
- **Dark Ink / Muted Ink:** Reading hierarchy, metadata and supporting descriptions.
- **Quiet Rule:** Panel boundaries, field strokes and record separators.

Confirmed chips retain green; blocked, unknown and awaiting-verification chips retain amber. Danger marks errors and destructive actions. Written state labels carry meaning alongside color. The sidecar's synthesized OKLCH strips are preview ramps, not additional source tokens.

**The Action Hierarchy Rule.** Cobalt fill identifies primary actions; white bordered controls support them, and pale selection identifies location. Preserve written labels alongside semantic state color.

## Typography

**Body and heading font:** The native UI stack in the frontmatter. No imported display face is used. The text wordmark uses the same stack with a cobalt second half; it has no monogram in the current header. Command blocks retain native monospace.

### Hierarchy

- **Headline:** Page titles; reduced to (25px) on small screens.
- **Title:** Section titles. Job-dialog headings use the observed intermediate size (20px).
- **Body:** Compact reading text. Introductory and job-reason paragraphs commonly cap at (65ch).
- **Label:** Semibold actions and record labels; form labels commonly use weight (500).
- **Metadata:** Dates, helper text, chips and compact count labels. Home count figures use (24px), weight (500), tabular numerals and tracking (-0.03em).

**The Task Label Rule.** Task titles and field labels establish hierarchy. Decorative eyebrows and brand captions remain hidden; uppercase ledger column headings remain functional labels.

## Layout

The desktop shell places a sticky white header above a white navigation rail (210px) and fluid content with horizontal padding (32px). Header minimum height is (77px); the rail starts below it. Between (901px) and (1150px), the rail becomes (180px), content padding becomes (22px), and Jobs detail stacks below its list.

At (900px) and below, the rail becomes a wrapping horizontal navigation group and content padding becomes (20px). At (600px) and below, content padding becomes (14px), the header wraps into two rows, and its search control becomes an independently sized icon button (44px). The wordmark retains its own space when the notification count appears. Forms stack, ledger rows become labeled records, and full-width contextual controls can grow to fit. The account-transfer grid has an independent breakpoint (700px).

Jobs uses an adjacent list/detail grid (1.15fr / 0.85fr), a gap (20px) and detail minimum width (350px). Desktop detail is sticky (20px from the top). At (900px) and below, the detail becomes a viewport-filling native modal with no corner rounding. Keep the same ledger and form nodes mounted so navigation preserves filters and drafts.

Recurring section padding and gaps use the frontmatter's spacing steps. Supporting connection panels use padding (24px), reducing to (16px) on mobile. Reusable sources have separation (32px) from generated materials. Long identities and command text remain accessible through wrapping or keyboard-reachable horizontal scrolling.

Outcome, recruiting-progress and batch-count groups use an auto-fitting definition-list grid with gaps (16px). Each cell can shrink below its usual minimum (120px) when its container is narrower: `minmax(min(100%, 120px), 1fr)`. Cells have zero minimum width, and labels and values wrap within their own cell. Standalone Applications section headings have top separation (32px), including Recent batches.

## Elevation & Depth

White surfaces, toned backgrounds and one-pixel rules do most grouping. Work panels are flat, and the activity list sits directly on the canvas. Joined counts and job-detail groups use separators instead of a card for every item. Floating feedback, native dialogs and the pinned preference save bar retain soft shadows for positional separation.

### Shadow Vocabulary

- **Notification lift** (`0 8px 32px #19233b26`): Viewport feedback above the current workspace or modal.
- **Modal separation** (`0 24px 80px #00000030`): Dialogs; suppressed for embedded desktop job detail.
- **Pinned save bar** (`0 6px 24px #0000001a`): Sticky preference actions.

## Shapes

Panels use the soft panel radius; controls use the smaller control radius. Status chips retain record corners. Separator rows are square. The existing desktop job-detail enclosure retains corners (5px), and its modal uses (7px) before becoming square and full-screen on mobile. Those detail exceptions are local implementation facts, not a second general panel scale.

## Components

### Buttons

Explicit, compact actions with cobalt primary and white bordered secondary variants. Primary hover deepens cobalt; secondary hover tones the surface and strengthens its border. Keyboard focus uses a cobalt outline (3px), offset (3px). Disabled controls use opacity (0.5) and a not-allowed cursor. Base buttons have minimum height (44px), navigation (42px), desktop worker actions (38px) and section actions (36px); mobile worker actions return to (44px).

### Chips

Written outcomes with a small current-color dot, metadata sizing and semibold weight. Confirmed uses green; uncertain and blocked states share amber. Color does not establish acceptance or capability. Neutral and discovery variants retain their observed source colors without becoming additional brand accents.

### Cards / Containers

White surfaces with a quiet border, soft panel corners and no resting shadow. Typical attention-panel padding is the section inset. The ledger is a single enclosure with toolbar, records and pagination separated by rules. Within job detail and saved-filter disclosures, headings and top rules replace nested framed cards.

### Inputs / Fields

White fields with a visible rule, labels, padding (10px 12px) and minimum height (44px). Textareas resize vertically with line height (1.6). Checkboxes and radio controls use cobalt accents. Read-only generated materials retain revision metadata and copy/download actions, separate from reusable-source upload and approval. Dirty/focused connection forms and in-memory prompt drafts are preserved during polling.

### Form Groups

Bordered settings and facts fieldsets keep their native fieldset/legend semantics. Their legends sit inside the panel padding rather than cutting across its border: full-width floated legends have zero padding, bottom margin (18px), and wrapping text; subsequent children clear the float. The same inset treatment covers generic section fieldsets, including Context and writing. The group heading identifies the controls without repeating a generic description.

### Navigation

The main workspace group contains Home, Jobs, Applications, Needs you and Profile; Settings follows a separator. Profile exposes Your facts and Materials through contextual navigation. Settings exposes Connections, Search & automation and Setup. Active locations use cobalt text on pale blue with weight (600). Authored SVG icons use stroke width (1.5), rounded joins and caps, and size (18px); labels remain visible. On mobile, the main group wraps rather than overflowing offscreen.

### Worker Controls and Connections

Worker state lives in the global header; Find opportunities, Resume/Pause and Run a batch remain available across views. Connections presents Gmail first, model access second and optional platforms inside a disclosure. Each platform keeps sign-in, enabled state and individual capability availability distinct. A sample switch or signed-in browser is not evidence of a validated application path.

### Notifications

Feedback is fixed to the viewport with a soft shadow, wrapping text and an explicit dismiss button. On desktop it sits at bottom right (24px); on small screens it spans the available width with insets (16px). The manual popover enters the top layer and moves under the active modal's ancestry so feedback remains available above native dialogs.

Ordinary messages dismiss after (6.5 seconds) of unpaused time; hover or keyboard focus pauses the timer. Errors remain until dismissed or replaced. The Notifications dialog retains the latest (20) messages in memory for the current page session, with timestamps. Showing feedback does not scroll or take focus; dismissing focused feedback restores its prior connected control with `preventScroll`. This history is not persisted to account storage.

### Batch Summary

Latest batch shows compact mode and status text, such as Applications · Paused. The Batch details disclosure retains the full Batch ID; its identifier can wrap. Unknown counters remain Not recorded, separate from zero, and prepared, confirmed, uncertain and blocked totals remain distinct. Avoid repeating the same paused explanation beside that status.

### Job Detail and Record Review

Desktop and mobile detail show route, held requirements, materials, notes and evidence. Immutable generated revisions remain read-only and independently inspectable. Uncertain submissions remain explicitly labeled for verification.

Before feedback confirmation, adjacent wrapping text repeats the selected company, role, attempt timestamp, recorded outcome and full identifier. It spans the review form, updates with selection and uses a polite live region. Exact identity remains available even when a native select clips its label. Confirmation and dismissal retain the primary/secondary hierarchy.

**The Visible Identity Rule.** A compact record picker exposes its complete selected identity in wrapping text before confirmation; a truncated option label is insufficient decision evidence.

Control transitions use (150ms) with default CSS ease. Reduced-motion preference disables transitions, animation and smooth scrolling.

## Do's and Don'ts

### Do:

- **Do** use cobalt actions, pale blue selection, cool paper and white surfaces for workspace extensions.
- **Do** pair status color with a written outcome or availability label.
- **Do** preserve complete record identity, source provenance and immutable revision boundaries.
- **Do** preserve drafts, reading focus and scroll position during operational refresh and feedback.
- **Do** keep visible keyboard focus and reduced-motion behavior.
- **Do** use contextual navigation for Materials and Connections within their current parent areas.

### Don't:

- **Don't** restore the superseded Application desk identity or teal action palette.
- **Don't** promote hidden decorative headings, legacy illustration shadows or ornamental text glyphs into reusable patterns.
- **Don't** replace authored SVG navigation icons with text glyphs.
- **Don't** treat sample records or screenshots as live Gmail, employer-acceptance or physical Pi proof.
- **Don't** turn generated revisions into editable reusable sources or silently retry uncertain applications.
