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

## Design Iteration Loop

When actively building or refining a screen, use a screenshot → review → fix cycle to converge on quality. This is a visual QA loop, not a code review.

### How It Works

1. **Screenshot the screen** using Playwright CLI to a known path. Save to a `screenshots/` dir in the project for consistency.

   **Basic (viewport only):**
   ```bash
   npx playwright screenshot --viewport-size="393,852" --wait-for-timeout=5000 \
     "http://localhost:8081/route" "screenshots/screen-name.png"
   ```

   **Full-page (scrollable content):**
   ```bash
   npx playwright screenshot --full-page --viewport-size="393,852" --wait-for-timeout=5000 \
     "http://localhost:8081/route" "screenshots/screen-name-full.png"
   ```

   **Authenticated pages** (pages behind login):
   ```bash
   # Step 1 — save auth state once (opens a browser, you log in, close it):
   npx playwright codegen http://localhost:8081/sign-in --save-storage=screenshots/auth.json

   # Step 2 — capture authenticated pages using saved state:
   npx playwright screenshot --load-storage=screenshots/auth.json --full-page \
     --viewport-size="393,852" --wait-for-timeout=5000 \
     "http://localhost:8081/" "screenshots/home-full.png"
   ```
   The `auth.json` file contains cookies and localStorage — gitignore it.

   **Authenticated pages via Claude-in-Chrome** (when Playwright lacks auth state):

   Playwright opens its own browser with no session, so it redirects to the login page for authenticated routes. If the user has Claude-in-Chrome open and already authenticated, capture the page from there using html2canvas injected via the JavaScript tool, then return the base64 data and save server-side. This avoids browser download dialogs entirely.

   ```javascript
   // Step 1 — run via mcp__claude-in-chrome__javascript_tool:
   (async () => {
     const script = document.createElement('script');
     script.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
     document.head.appendChild(script);
     await new Promise(r => script.onload = r);
     const canvas = await html2canvas(document.body, { scale: 1, useCORS: true });
     return canvas.toDataURL('image/png');
   })()
   ```

   ```bash
   # Step 2 — decode the returned base64 data URL and save (Bash):
   echo "$DATA_URL" | sed 's/^data:image\/png;base64,//' | base64 -d > screenshots/screen-name.png
   ```

   ```powershell
   # Step 2 — decode (PowerShell):
   $base64 = $dataUrl -replace '^data:image/png;base64,',''
   [IO.File]::WriteAllBytes("screenshots\screen-name.png", [Convert]::FromBase64String($base64))
   ```

   **Important:** Do NOT use `a.download = 'file.png'; a.click()` to trigger a browser download — Chrome's "Ask where to save" setting causes a modal dialog that blocks the extension and requires manual user interaction. Always return the base64 through the JS tool and decode server-side.

   **Notes:**
   - The 5-second wait is needed for Expo/React apps to render after JS bundle load.
   - `--full-page` captures the entire scrollable height, not just the viewport.
   - For lazy-loaded content, you may need a Puppeteer script that scrolls before capturing.
   - Prefer Playwright for unauthenticated pages (simpler, reliable). Use the Chrome html2canvas approach only when auth is required and Playwright lacks session state.

2. **Send to a reviewer** for aesthetic feedback. Rotate between multiple reviewers to get a focus-group effect rather than one opinionated voice:

   | Reviewer | How to invoke | Best for |
   |----------|--------------|----------|
   | **Sonnet sub-agent** | `Agent` tool with `model: "sonnet"` | Quick score, describe the layout in the prompt |
   | **Gemini Pro** | `gemini-review.py` CLI (see below) | Vision-native review from the actual screenshot file |
   | **Codex** | `codex-review` skill | Deep code-aware review, but slower |

3. **Apply aesthetic fixes** from the review. Only spacing, proportion, hierarchy, typography, and visual impact — never change copy or business logic based on reviewer feedback.
4. **Re-screenshot and re-score.** Rotate to a different reviewer for fresh perspective.

### Reviewer: Sonnet Sub-Agent

Spawn with `model: "sonnet"`. Describe the screen layout in the prompt (the agent can't see the screenshot). Include:

- Path to `interface-design.md` to read
- Screen layout top-to-bottom with sizes, colors, spacing classes
- "Score 0.0-10.0 aesthetics ONLY. Do NOT suggest copy changes."
- Request 3–5 issues and 3–5 fixes

### Reviewer: Gemini Pro (Vision)

Uses the `gemini-review.py` CLI tool in the skill directory. Sends the actual screenshot image to Gemini for vision-based review — no need to describe the layout in text.

```bash
cd "<skill-directory>" && \
  uv run gemini-review.py "path/to/screenshot.png" \
    "Brief app description" \
    -d "path/to/interface-design.md"
```

Arguments:
- First positional: screenshot path (PNG/JPG)
- Second positional: brief app/screen context string
- `--design-doc` / `-d`: path to `interface-design.md` (attached as context)
- `--model` / `-m`: override model (default: `gemini-3.1-pro-preview`, env: `GEMINI_REVIEW_MODEL`)
- `--no-save`: skip saving to `~/reviews/`

**API key setup:** `GOOGLE_GENAI_API_KEY` is required. The script checks (in order): env var → `<skill-directory>/.env` → `~/.env` → `./.env`. The key lives in the skill directory's `.env` file alongside this script (matching the pattern used by other elevate-agent-skills like `slack-canvas/` and `web-research/`).

Results auto-save to `~/reviews/` with YAML frontmatter.

### Reviewer: Codex

Use the `codex-review` skill for deeper code-aware reviews. Best for checking design-system compliance across multiple files, not for quick aesthetic iteration.

### Why Rotate Reviewers

Each reviewer has a different personality:
- **Sonnet** tends to be harsh and opinionated about proportions and hierarchy
- **Gemini** focuses on visual weight, rhythm, and design-system consistency
- **Codex** catches code-level issues (wrong tokens, missing states)

Rotating prevents over-fitting to one reviewer's taste. When two reviewers converge on the same issue, it's real. When they disagree, it's subjective — use your judgment.

### Guardrails

The review agent is a **focus group, not a decision-maker.** Apply these rules:

- **Follow:** Spacing, padding, proportion, font size, opacity, visual hierarchy, layout structure suggestions.
- **Ignore:** Copy rewrites, button label changes, business logic opinions, feature suggestions, architectural recommendations.
- **Never change client-specified copy** based on reviewer feedback, even if the reviewer says it's wrong. Copy changes require explicit user instruction.
- **Use your judgment** on which fixes to apply. Not every suggestion is worth implementing — pick the ones that address real visual problems, skip the subjective preferences.

### When to Stop

- **Target score: 9.0/10.0** unless the user specifies otherwise.
- Keep iterating until the user says to stop or scores plateau (two consecutive reviews within 0.5 points across different reviewers).
- When reviewers converge on the same remaining issues, those are real — fix them. When they diverge, the screen is in taste territory — present the tradeoff to the user.

---

## Maintaining the Doc

After building significant UI, offer:

> "Want me to update `interface-design.md` with the patterns from this session?"

Add new patterns, refine existing ones, note exceptions. The doc should grow with the project — but stay high-level. It's a design philosophy readme, not a component API reference.
