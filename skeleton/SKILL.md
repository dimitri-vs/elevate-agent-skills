---
name: skeleton-docs
description: Skeleton UI framework reference. Use when working on Svelte frontend components, debugging Skeleton styling issues, customizing components beyond basics, or iterating on UI implementation. Fetch latest docs for best practices.
---

# Skeleton Documentation

## How to Use This Skill

This index is the **source of truth** for Skeleton documentation. When invoked:

1. **Identify relevant docs** - Scan the index below to find 1-3 docs matching your current task (e.g., working on cards → `/docs/svelte/tailwind-components/cards.md`)
2. **Fetch the docs** - Use WebFetch to retrieve them from `https://www.skeleton.dev` + the path (e.g., `https://www.skeleton.dev/docs/svelte/tailwind-components/cards.md`)
3. **Apply current best practices** - The fetched markdown contains up-to-date patterns, variants, and examples

**Do NOT guess URLs** - always use this index to find the correct documentation paths.

## Common Gotchas

**v3→v4 migration**: The `-token` suffix utility classes (e.g., `bg-surface-100-800-token`) are a **v3 pattern that produces zero CSS in v4**. They silently render as transparent. In v4, use preset classes (`preset-filled-surface-100-900`) or Tailwind's `dark:` variant (`bg-surface-100 dark:bg-surface-800`).

**@tailwindcss/forms is required**: Skeleton v4 does NOT style form inputs by default. You MUST install and import the Tailwind Forms plugin, or `.input`/`.textarea`/`.select` will have no borders or backgrounds. Add `@plugin '@tailwindcss/forms';` to your CSS after the `@import 'tailwindcss'` line.

**Windows rendering fix**: On Windows browsers, form elements may render incorrectly. Add this CSS globally:
```css
.select, .input, .textarea, .input-group {
  background-color: var(--color-surface-50-950);
  color: var(--color-surface-950-50);
}
```

**Use preset classes for cards/backgrounds**: Don't hand-roll `bg-surface-*` with `dark:` variants for cards. Use Skeleton's built-in presets which handle light/dark internally: `preset-filled-surface-100-900` (background), `preset-outlined-surface-300-700` (border). See the [Cards docs](/docs/svelte/tailwind-components/cards.md).

---

## Svelte

### Get Started

- [Introduction](/docs/svelte/get-started/introduction.md)
- [Installation](/docs/svelte/get-started/installation.md)
- [Fundamentals](/docs/svelte/get-started/fundamentals.md)
- [Core API](/docs/svelte/get-started/core-api.md)
- [Migrate from v2](/docs/svelte/get-started/migrate-from-v2.md)
- [Migrate from v3](/docs/svelte/get-started/migrate-from-v3.md)

### Guides

- [Dark Mode](/docs/svelte/guides/mode.md)
- [Layouts](/docs/svelte/guides/layouts.md)
- [Cookbook](/docs/svelte/guides/cookbook.md)

### Design System

- [Themes](/docs/svelte/design/themes.md)
- [Colors](/docs/svelte/design/colors.md)
- [Presets](/docs/svelte/design/presets.md)
- [Typography](/docs/svelte/design/typography.md)
- [Spacing](/docs/svelte/design/spacing.md)
- [Iconography](/docs/svelte/design/iconography.md)

### Tailwind Components

- [Badges](/docs/svelte/tailwind-components/badges.md)
- [Buttons](/docs/svelte/tailwind-components/buttons.md)
- [Cards](/docs/svelte/tailwind-components/cards.md)
- [Chips](/docs/svelte/tailwind-components/chips.md)
- [Dividers](/docs/svelte/tailwind-components/dividers.md)
- [Forms and Inputs](/docs/svelte/tailwind-components/forms.md)
- [Placeholders](/docs/svelte/tailwind-components/placeholders.md)
- [Tables](/docs/svelte/tailwind-components/tables.md)

### Framework Components

- [Accordion](/docs/svelte/framework-components/accordion.md)
- [App Bar](/docs/svelte/framework-components/app-bar.md)
- [Avatar](/docs/svelte/framework-components/avatar.md)
- [Carousel](/docs/svelte/framework-components/carousel.md)
- [Collapsible](/docs/svelte/framework-components/collapsible.md)
- [Combobox](/docs/svelte/framework-components/combobox.md)
- [Date Picker](/docs/svelte/framework-components/date-picker.md)
- [Dialog](/docs/svelte/framework-components/dialog.md)
- [File Upload](/docs/svelte/framework-components/file-upload.md)
- [Floating Panel](/docs/svelte/framework-components/floating-panel.md)
- [Listbox](/docs/svelte/framework-components/listbox.md)
- [Menu](/docs/svelte/framework-components/menu.md)
- [Navigation](/docs/svelte/framework-components/navigation.md)
- [Pagination](/docs/svelte/framework-components/pagination.md)
- [Popover](/docs/svelte/framework-components/popover.md)
- [Portal](/docs/svelte/framework-components/portal.md)
- [Progress - Circular](/docs/svelte/framework-components/progress-circular.md)
- [Progress - Linear](/docs/svelte/framework-components/progress-linear.md)
- [Rating Group](/docs/svelte/framework-components/rating-group.md)
- [Segmented Control](/docs/svelte/framework-components/segmented-control.md)
- [Slider](/docs/svelte/framework-components/slider.md)
- [Steps](/docs/svelte/framework-components/steps.md)
- [Switch](/docs/svelte/framework-components/switch.md)
- [Tabs](/docs/svelte/framework-components/tabs.md)
- [Tags Input](/docs/svelte/framework-components/tags-input.md)
- [Toast](/docs/svelte/framework-components/toast.md)
- [Toggle Group](/docs/svelte/framework-components/toggle-group.md)
- [Tooltip](/docs/svelte/framework-components/tooltip.md)
- [Tree View](/docs/svelte/framework-components/tree-view.md)

### Integrations

- [Code Block](/docs/svelte/integrations/code-block.md)
- [Bits UI](/docs/svelte/integrations/bits-ui.md)
- [Melt UI](/docs/svelte/integrations/melt-ui.md)
- [Radix UI](/docs/svelte/integrations/radix-ui.md)

### Resources

- [Contribute](/docs/svelte/resources/contribute.md)
- [LLMs](/docs/svelte/resources/llms.md) (this doc)
