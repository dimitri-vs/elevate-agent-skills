---
name: interface-design
description: Interface design guidance for utilitarian apps — dashboards, tools, admin panels, data-heavy UIs. Use when building new pages/views, adding major UI components, or reviewing frontend design consistency. Component-library-first approach.
---

# Interface Design

Utilitarian UI design for dashboards, tools, admin panels, and data-heavy applications.

**Not for:** Landing pages, marketing sites, campaigns.

## Philosophy

We use component libraries (Skeleton, shadcn/ui, Flowbite, Ant Design, etc.) and defer to their defaults. Design work means:

1. Choosing the right library and base theme
2. Applying a thin, deliberate customization layer on top
3. Documenting those choices so every session stays consistent

The skill's main job is creating and maintaining a per-project **design doc** (`interface-design.md`) that captures these decisions.

---

## Step 1: Find the Design Doc

Look for `interface-design.md` in this order:

1. Project root
2. `docs/`
3. `frontend/` or `src/` (if it's a monorepo with a frontend subdir)

**If found** → Read it, apply it. Skip to [Building with the Doc](#building-with-the-doc).

**If not found** → Run setup below.

---

## Step 2: Setup — Create the Design Doc

Walk the user through these questions. Don't dump them all at once — use 1-2 rounds of AskUserQuestion with follow-ups as needed.

### Round 1: Context

- **What is this project?** (Brief description — e.g., "internal inventory tracker", "client-facing analytics dashboard")
- **Who are the end users?** (Internal team? Developers? Non-technical business users? Kids? General public?)
- **What's the intent?** Quick internal prototype → keep it generic and minimal. Production app for a specific audience → more intentional choices.
- **Component library?** Which one, and which base theme/preset? (e.g., Skeleton + Cerberus, shadcn/ui default, Ant Design)

### Round 2: Customization Preferences

Based on Round 1 answers, ask about the thin customization layer:

- **Color palette** — Stick with library defaults? Desaturate? Specific brand colors to incorporate? Conventional mapping (primary=blue, success=green, error=red) or custom?
- **Density** — Default spacing, tighter, or more spacious?
- **Edges** — Border radius preference? (Sharp/technical, default, soft/rounded)
- **Depth** — Drop shadows or flat/borders-only?
- **Typography** — Custom font or library default? Any preference (system fonts, Inter, monospace-heavy for data)?
- **Dark/light mode** — One or both? Which is primary?

### Then Generate the Doc

Write `interface-design.md` at the project root using the template below. Keep it high-level and scannable.

```markdown
# Interface Design — [Project Name]

## Overview
- **Project:** [brief description]
- **Audience:** [who uses this]
- **Intent:** [rapid prototype / internal tool / production app / etc.]

## Component Library
- **Library:** [name + version if known]
- **Theme/Preset:** [e.g., Cerberus, default]
- **Docs:** [link to library docs if available]

Always defer to the component library for component structure, spacing, and interaction patterns. Customizations below are layered on top — not replacements.

## Customizations

### Color Palette
[e.g., "Desaturated variant of defaults. Primary: blue-600, secondary: slate-500. Semantic colors follow convention (success=green, warning=amber, error=red) but pulled back ~10% saturation."]

### Density & Spacing
[e.g., "Default library spacing. Slightly tighter in data-heavy views (tables, lists)."]

### Edges & Depth
[e.g., "Border radius: default (6px). No drop shadows — borders-only for card separation. Subtle dividers between sections."]

### Typography
[e.g., "System font stack. Monospace for data values, IDs, timestamps. tabular-nums on numeric columns."]

### Dark/Light Mode
[e.g., "Dark mode primary. Light mode supported but secondary."]

## Layout Patterns

### Primary Layout
[e.g., "Sidebar + content. Sidebar: collapsible, icon-only when collapsed. Fixed left, 240px expanded."]

### Page Structure
[e.g., "Page header with title + actions → content area. No breadcrumbs unless 3+ levels deep."]

### Data Views
[e.g., "Tables for structured data. Filter bar above table (inline, not modal). Pagination at bottom. Empty states: centered icon + message + action button."]

## Data Patterns

### Tables
[Preferences for tables — striped rows? Hover highlight? Sticky headers? Column alignment? Sortable indicators?]

### Filters & Search
[How filtering works — filter bar, sidebar filters, search placement?]

### Forms
[Form layout preferences — single column? Labels above or beside? Inline validation?]

### Loading & Empty States
[Skeleton loaders? Spinners? Empty state style?]

## Avoid
- [Project-specific anti-patterns, e.g., "No gradient backgrounds", "No card shadows", "No decorative icons"]

## Exceptions
[Page-specific overrides if any. e.g., "Dashboard overview: denser layout with metric cards, deviates from standard spacing."]
```

---

## Building with the Doc

When the design doc exists and you're building UI:

1. **Read `interface-design.md` first.** Every time. Don't assume you remember it.
2. **Use the component library.** Check its docs for the right component before building custom. The library's default behavior is correct unless the design doc says otherwise.
3. **Apply customizations from the doc.** Color palette, density, edges, typography — these override library defaults where specified.
4. **Follow layout patterns from the doc.** Don't invent new layouts when the doc defines standard ones.
5. **For new patterns not in the doc**, suggest adding them after building. Keep the doc growing.

### Craft Reminders

These are universal — they apply regardless of library or project:

**Subtle layering.** Surface elevation changes should be barely noticeable. Borders should disappear when you're not looking for them but be findable when needed. If borders are the first thing you see, they're too strong. Study Vercel, Linear, Supabase for reference.

**Hierarchy through contrast, not decoration.** Use the text hierarchy (primary → secondary → muted → faint) consistently. Don't add color or weight where opacity/contrast handles it.

**Monospace for data.** Numbers, IDs, codes, timestamps → monospace. Use `tabular-nums` for columnar alignment.

**States are not optional.** Every interactive element needs: default, hover, active, focus, disabled. Data views need: loading, empty, error. Missing states feel broken.

**Navigation grounds the page.** A data table floating in space feels like a component demo. Always show where the user is in the app — active nav state, page title, breadcrumbs if deep.

**Icons clarify, not decorate.** If removing an icon loses no meaning, remove it.

**One depth strategy.** Borders-only OR subtle shadows OR layered shadows. Don't mix. Whatever the design doc specifies — commit to it everywhere.

### Universal Avoid List

- Harsh borders (if they're the first thing you see, they're too strong)
- Dramatic surface/elevation jumps between adjacent areas
- Inconsistent spacing within a view
- Mixed depth strategies (shadows in some cards, borders in others)
- Missing interaction/data states
- Gradients or color used decoratively (color should mean something)
- Multiple accent colors competing for attention
- Pure white cards on colored backgrounds in dark mode
- Native `<select>` or `<input type="date">` when the component library provides styled alternatives

---

## Design Audit

When the user asks for a design review or audit, follow this process.

### 1. Determine Scope

Ask the user what they want audited:

| Scope | What gets checked | Strategy |
|-------|------------------|----------|
| **Component** | A single component or small set of related components | Read the files directly, check inline |
| **View/Page** | One route/page and everything it renders | Read the page + its imported components |
| **Full App** | Every view across the application | Use sub-agents (see below) |

### 2. Read the Design Doc

Always read `interface-design.md` first. The audit checks against what the project has decided, not abstract ideals. If no design doc exists, tell the user — you can't audit against nothing. Offer to create one first.

### 3. Run the Audit

**For component or view scope:** Check directly — no sub-agents needed.

**For full app audits:** Decompose by route/page. Launch parallel sub-agents (Task tool, subagent_type: "general-purpose"), one per page or logical section. Each agent gets:
- The contents of `interface-design.md`
- Its assigned files/routes to check
- The checklist below
- Instruction to return structured findings only (no fixes, no code changes)

Merge the sub-agent results into one consolidated report.

### 4. The Checklist

Each file/view is checked against these categories:

**Library compliance**
- Using library components or reinventing? (custom `<Select>` when library has one = violation)
- Using library utility classes/tokens or raw CSS values?

**Customization consistency**
- Colors match the documented palette? No rogue hex values?
- Spacing/density consistent with doc? Tighter or looser than specified?
- Border radius, depth strategy match doc? (e.g., shadows where doc says borders-only)
- Typography matches? (font family, monospace for data, weight hierarchy)

**Layout compliance**
- Page structure follows documented patterns? (sidebar, header, content areas)
- Responsive behavior consistent across views?

**Data patterns**
- Tables, filters, forms follow documented approach?
- Search placement and behavior consistent?

**State coverage**
- Interactive elements: hover, focus, disabled states present?
- Data views: loading, empty, error states present?
- Missing states flagged with specific component/line

**Hierarchy & clarity**
- Clear visual hierarchy? Can you tell what the primary action is?
- Text contrast levels used correctly? (primary/secondary/muted not random)
- Icons serving a purpose or just decorating?

### 5. The Report

Structure the output as:

```
## Design Audit — [scope]

### Summary
[1-2 sentence overall assessment: mostly consistent, major drift, etc.]

### Findings

#### [Category] — [number] issues
- **[file:line]** — [what's wrong] → [what the doc says it should be]
- **[file:line]** — [what's wrong] → [suggested fix]

#### ...

### Patterns Not in Doc
[Any recurring patterns found that aren't documented yet — suggest adding them]

### Recommended Actions
[Prioritized list: fix these first, these are minor, these are suggestions]
```

Keep findings specific — file paths, line numbers, concrete deviations. Don't give vague feedback like "spacing feels off." Say "`Dashboard.svelte:42` — padding-x is 20px, doc specifies default library spacing (16px)."

---

## Maintaining the Doc

After building significant UI, offer:

> "Want me to update `interface-design.md` with the patterns from this session?"

Add new patterns, refine existing ones, note exceptions. The doc should grow with the project — but stay high-level. It's a design philosophy readme, not a component API reference.
