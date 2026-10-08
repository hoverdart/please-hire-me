---
name: Application desk
description: A private applicant operations workspace with compact records and actionable exceptions.
colors:
  action: "#17695c"
  action-hover: "#115247"
  paper: "#f5f6f5"
  surface: "#fff"
  ink: "#242c2a"
  muted: "#606d68"
  rule: "#dce2de"
  rail: "#edf1ee"
  selection: "#dce9e2"
  selection-ink: "#154d43"
  worker-ground: "#eaf0ec"
  worker-rule: "#d5e0d8"
  control-rule: "#cbd5ce"
  secondary-hover: "#f0f3f1"
  secondary-hover-rule: "#a8beb6"
  focus: "#51a794"
  confirmed-ground: "#e9f3ed"
  confirmed-ink: "#337253"
  attention-ground: "#fbf0de"
  attention-ink: "#8d641f"
  danger: "#ad3737"
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
  control: "5px"
  text-input: "8px"
  record: "6px"
  navigation: "4px"
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
    backgroundColor: "{colors.secondary-hover}"
  text-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.text-input}"
    padding: "10px 12px"
  navigation-active:
    backgroundColor: "{colors.selection}"
    textColor: "{colors.selection-ink}"
    rounded: "{rounded.navigation}"
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
    rounded: "{rounded.control}"
    padding: "20px"
  worker-strip:
    backgroundColor: "{colors.worker-ground}"
    rounded: "{rounded.control}"
    padding: "12px 16px"
---

# Design System: Application desk

## Overview

**Creative North Star: "Applicant operations workspace"**

The private local dashboard organizes progress and actionable exceptions on neutral paper. White work surfaces, graphite type and restrained teal actions keep the applicant's records and next decision prominent. This is a framework-free HTML/CSS/JavaScript interface served by the local Python application.

This document replaces the discarded blue direction with the implemented operations direction, as explicitly authorized in the redesign. Its authority is the final cascade in `hireme/static/style.css`, markup in `index.html`, and behavior in `app.js` and `workspace.js`; the approved textual direction is recorded in `.impeccable/surfaces/hireme-static-index-html.md`. No approved image comp or QUALITY BAR card exists, so this is a source-derived system, not a certified reproduction. Eight synthetic fixture captures under `.impeccable/review/` record Today, Connections, Jobs and Materials at desktop and mobile sizes; final captures include the flattening, SVG and spacing correction. They prove neither actual platform sign-in nor Pi operation nor native application submission. Connections start disabled. No generative raster asset was created. The shipping README screenshot comes from the synthetic desktop fixture and carries provenance in `docs/images/application-desk-demo.provenance.md`.

**Key Characteristics:**

- Compact utility typography and labeled operational states.
- Flat surfaces with quiet borders and separator disclosures.
- Persistent worker controls and a single paginated ledger.
- Adjacent desktop job detail and full-screen mobile detail.
- Read-only generated revisions separated from reusable sources.

## Colors

The palette combines neutral paper and graphite with one restrained teal action family; semantic green, amber and red communicate outcomes alongside text.

### Primary

- **Action Teal:** Primary buttons, links, checkbox accents and progress fills; its deeper hover shade signals the same action.
- **Selection Teal:** Pale navigation selection with darker teal text; active navigation remains quieter than the primary action.
- **Focus Teal:** The shared keyboard focus outline, distinct from selection.

### Neutral

- **Paper / White Surface:** Application ground outside panels; white panels, fields and secondary buttons.
- **Graphite Ink / Muted Graphite:** Headings and reading text; metadata and descriptions.
- **Quiet Rule / Control Rule:** Panel borders and record separators; a stronger field boundary.
- **Rail Ground / Worker Ground:** Subtle green neutrals separate navigation and worker status from white work surfaces.

Confirmed and attention chips use their semantic ground/ink pairs in the frontmatter. Danger is reserved for destructive or error states. These are operational meanings, not decorative accents. Sidecar tonal strips are synthesized OKLCH preview ramps, not additional CSS tokens.

**The Action Hierarchy Rule.** Use teal fill for the primary action and white bordered controls for supporting actions; preserve written status labels alongside state color.

## Typography

**Body and heading font:** The frontmatter's native UI stack. There is no imported display font or hero typography. The small serif monogram is a brand mark, not a heading system. Command blocks use native monospace.

The ramp is compact and functional: page titles lead, section titles organize work, and metadata yields space to records. The native UI face serves utility reading rather than decorative display.

### Hierarchy

- **Headline:** Page title; reduces to (25px) at the small-screen breakpoint.
- **Title:** Section heading; job-detail titles use the observed (20px) intermediate size.
- **Body:** Reading text; introductory paragraphs commonly use a (65ch) maximum.
- **Label:** Semibold buttons and record labels. Form labels commonly use weight (500); descriptions remain regular.
- **Metadata:** Counts, dates, helper text and chips. Metric figures use (32px), weight (500), tabular numerals and tracking (-0.03em), reducing to (26px) on small screens.

**The Task Label Rule.** Visible task titles establish hierarchy. Decorative eyebrows and the uppercase brand caption remain hidden; uppercase ledger column headings are functional labels, not a decorative heading pattern.

## Layout

The desktop shell has a sticky navigation rail (210px) and fluid content with horizontal padding (32px). The top bar precedes the page title and persistent worker strip. Repeated gaps and insets use the compact spacing steps in the frontmatter.

Today joins three totals in one bordered surface. Priorities and connection health use a two-column grid (1.6fr / 1fr) with a (24px) gap, followed by cycle information, the shared ledger and activity. The ledger moves between Today and Jobs as the same DOM nodes; search, status, sort, source/destination/fit filters, saved-filter disclosure and pagination describe one queue.

Jobs uses a list/detail grid (1.15fr / 0.85fr), a (20px) gap and a detail minimum of (350px). Desktop detail is sticky (20px from the top). Between (901px) and (1150px), the rail narrows to (180px), padding becomes (22px), and Jobs stacks with static detail.

At (900px) and below, navigation becomes a horizontal scrollable strip, content padding becomes (20px), Today stacks, and job details open as a viewport-filling modal without corner rounding. At (600px) and below, padding is (14px), totals become joined rows, forms become one column, table rows become labeled records, and worker controls share a compact row. The account-transfer grid independently stacks at (700px).

Reusable sources has top separation (32px) from generated materials. Generated text belongs to read-only revision rows; upload and source approval belong to the subsequent group. Long records wrap, and command blocks scroll horizontally with keyboard focus.

## Elevation & Depth

The operations workspace is flat at rest. White surfaces, toned grounds and one-pixel rules provide grouping. Totals share separators instead of floating cards; desktop detail has no shadow. Modal dialogs retain a backdrop and shadow, and the sticky preferences save bar retains a soft shadow for its pinned position.

### Shadow Vocabulary

- **Modal separation** (`0 24px 80px #00000030`): Modal job and tool dialogs; suppressed for embedded desktop detail.
- **Pinned save bar** (`0 6px 24px #0000001a`): Sticky preference actions.

**The Separator Disclosure Rule.** Within the job-detail boundary and the ledger's saved-filter disclosure, use headings, disclosures and top rules rather than another stack of rounded framed cards.

## Shapes

Buttons, selects, textareas and primary work panels use the small control radius. Text inputs retain the more-specific text-input radius in the actual cascade; this is not a universal radius change. Chips and the ledger enclosure use the record radius; active navigation uses the navigation radius. Record separators and flattened detail groups are square. Modal job dialogs use (7px) corners on desktop and no rounding in the full-screen mobile state.

## Components

### Buttons

Compact, explicit actions. Primary controls use action/surface colors; secondary controls use white, graphite and a quiet border. Hover deepens teal or lightly tones a secondary surface. The shared focus outline is (3px) with a (3px) offset. Disabled controls use opacity (0.5) and a not-allowed cursor. Base buttons have a (44px) minimum height; navigation, worker-strip and section actions have observed minima of (42px), (38px) and (36px). Do not describe all controls as uniformly 44px.

### Chips

Written outcomes with a small current-color dot, weight (600), metadata sizing and record corners. Confirmed records use green; blocked, unknown and awaiting-verification records share amber. Labels carry meaning independently of color. Neutral and discovery states remain implemented, but their incidental blue ink is not palette authority.

### Cards / Containers

White work panels use one-pixel quiet rules, small control corners and no resting shadow. Typical padding is the section inset; connections use the group inset, reducing to the smaller inset on mobile. The ledger is one enclosure with toolbar, row and pagination separators. Joined totals are flush within their common border.

### Inputs / Fields

White fields with a stronger rule, visible labels, (10px 12px) padding and a (44px) minimum height. Textareas resize vertically and use line height (1.6). Checkbox and radio accents are teal. Read-only materials use labeled textareas and revision metadata, distinct from upload and approval forms. Source guards retain dirty or focused connection forms and in-memory prompt drafts during polling; images are not independent runtime proof of these guards.

### Navigation

Today, Jobs, Needs you, Materials, Profile and Connections form the workspace group; Preferences and Setup are separated utilities. Active items use pale selection ground and darker teal ink with weight (600); inactive navigation is quiet graphite. Hover uses a lightly toned surface. Mobile items remain reachable in the horizontal strip. Authored SVG stroke icons are (18px), with stroke width (1.5), rounded caps and joins; search shares this system. Icons supplement written labels.

### Worker Strip and Connection Health

The strip stays visible across views, with written state, a small dot and Find opportunities / Resume or Pause / Run a batch controls. Its pale ground and rule separate operation from white tasks. Connection health uses separated text rows. Connections show switches, sign-in state and explicit automatic-search/native-application availability. A fixture's checked draft switch does not establish live capability.

### Job Detail and Evidence

Opening a job shows its route, held requirements, materials, notes and evidence beside the desktop queue or in the mobile dialog. Internal groups use separators and native disclosures. Generated rows preserve read-only content, revision metadata and explicit copy/download actions. Uncertain outcomes stay labeled and require verification; a local artifact must not imply accepted submission.

Motion is limited to control transitions (150ms, default CSS ease); no decorative animation is required. Reduced-motion preference disables transitions, animations and smooth scrolling.

## Do's and Don'ts

### Do:

- **Do** use the recorded paper, white, graphite and teal hierarchy for new workspace surfaces.
- **Do** pair every status color with a written outcome or availability label.
- **Do** retain one paginated queue and the desktop/mobile detail relationship.
- **Do** use separators inside job detail and the saved-filter disclosure.
- **Do** preserve drafts and reading focus when refreshing operational data.
- **Do** distinguish generated revisions from reusable upload and approval.
- **Do** preserve visible keyboard focus and reduced-motion behavior.

### Don't:

- **Don't** reintroduce the discarded blue action theme or decorative eyebrow hierarchy.
- **Don't** replace the authored SVG icon system with ornamental text glyphs.
- **Don't** promote nested card stacks or hard offset illustration shadows into reusable workspace patterns.
- **Don't** treat synthetic fixtures as proof of live connections, Pi operation or accepted submissions.

Not canonized: incidental blue company/status text, placeholder/hover colors, older pale-blue utility grounds, isolated setup illustration styling and its hard offset shadow. These are residual details or one-off decoration, not instructions for future surfaces. Removed glyphs and nested frames are also excluded; final source removes or flattens them. This documentation does not claim other residual details were fixed.
