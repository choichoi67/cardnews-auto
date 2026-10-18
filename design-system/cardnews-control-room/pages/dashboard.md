# Dashboard Override

## Information architecture

1. Global newsroom navigation, followed by isolated magazine workspaces and connection state
2. Primary actions: channel settings and news collection
3. Persistent six-step automation flow
4. Four operational summary cards
5. A single-focus workspace tab bar: News candidates, Production & delivery, Outputs
6. Square editorial candidate grid with bold headline and regular-weight summary
7. Production controls and activity contained in the production tab
8. Post-level output view: one cover only, with offset layers and a slide-count badge

## Multi-magazine model

- The global newsroom is an aggregate operations view only: status, candidates, jobs, and artifacts.
- Editing RSS sources, schedules, prompts, modules, Telegram, and Drive destinations always happens inside the selected magazine workspace.
- Candidate and artifact identity is scoped by magazine so one channel can never overwrite another channel's records.
- Global candidate approval routes into the owning magazine before starting production.
- The global queue defaults to actionable candidates and provides title/summary search plus magazine and status filters.
- Built candidates remain queryable under the history status instead of being removed from the editorial log.
- Production & delivery starts with a channel-health summary, followed by recovery actions and an audit trail.
- Failed or interrupted jobs expose a labeled retry action; successful jobs never show it.
- Candidate cards keep approval primary and place Hold/Hide as subordinate actions. Held and hidden records remain recoverable through status filters.

## Channel settings

- Split the channel editor into five exclusive tabs: Basic, Sources & schedule, AI design, Caption, Delivery & image.
- Keep the caption prompt in its own task-focused panel with persistent helper text and a large editable field.
- Keep every preset, source, schedule, module, and destination scoped to the active channel.
- Preserve values in hidden tabs while saving the full channel form.
- Support arrow-key navigation and semantic tab roles.

## Responsive behavior

- 1120px and above: persistent 270px glass sidebar, two-column workspace.
- 761–1119px: compact 90px sidebar, single-column content, two-column right rail.
- 760px and below: horizontal channel rail, stacked top actions, single-column workspace.
- 420px and below: single-column metrics and full-width actions.

## Progressive disclosure

- Schedule time inputs appear only when scheduled collection is enabled.
- Drive folder ID appears only when Drive upload is enabled.
- Image prompt appears only when image transformation is enabled.
- PSD paths remain under advanced settings.

## Interaction feedback

- Async action buttons become disabled and show a spinner with an active verb.
- Toasts use `aria-live=polite`; errors stay visible longer than success messages.
- Data polling renders only when the overview payload changes to preserve focus.
