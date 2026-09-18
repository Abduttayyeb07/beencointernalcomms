# Slack styling reference — live reconnaissance

## Access and scope

The user approved using the signed-in test workspace instead of Beenco, and
approved substituting Playwright for the skill's unavailable Chrome extension.
No Slack messages were submitted, files uploaded, or workspace settings changed.
Dark mode was triggered through browser color-scheme emulation.
The existing application's data and backend remain the implementation target.

## Evidence captured

Local, git-ignored `01-recon/` contains JSON computed-style archetypes, visible
control inventories, loaded font metadata, readable media conditions and
matching screenshots under `01-recon/screenshots/`.

- `home-desktop`: light-mode baseline.
- `home-dark-desktop`, `channel-desktop-restored`: dark channel layout.
- `channel-menu-desktop`: channel context menu, including destructive actions
  which were inspected but NOT invoked.
- `home-emoji-desktop`: visible emoji picker; no emoji selected or sent.
- `navigation-more-desktop`: More menu exposes Files and customization.
- `dm-desktop`: self-DM with Slack's automatically opened template side panel;
  no template was applied.
- `channel-tablet`, `channel-mobile`: resized desktop web interface at 768x1024
  and 375x667. These are not evidence of Slack's native mobile app design.

Measured at 1920x1080: search x=513.03125, y=6, width=885.984375,
height=28; workspace begins y=40; navigation rail width=70.
Loaded font families include Slack-Lato (400/700/900 and italics).
Channel header background is rgb(26,29,33); ordinary foreground is
rgb(209,210,211); sender names are 15px, rgb(248,248,248).
The current sidebar is user-resized to roughly 443px; the original screenshot's
narrower sidebar should remain the implementation reference for its default width.

## Coverage limitations — not a complete skill verification pass

- Channel-details capture is not verified: waiting for the first role=dialog
  resolved to Slack's hidden Huddle dialog. Its existing capture is preliminary.
- Search/composer captures may include a search overlay; require clean-state
  recapture before using them as isolated component assertions.
- Files navigation attempt did not resolve after the More menu closed; no Files
  view capture exists yet.
- Dedicated DM inbox, Activity, real thread replies, populated attachments and
  associated filters are not covered by this new workspace's current content.
- Sidebar navigation, nested menus and per-viewport interaction coverage remain
  incomplete. Do not describe reconnaissance or pixel matching as complete.
- Stylesheet access failures are counted in each capture; cross-origin CSS/font
  asset extraction has not yet been performed.

## Next stage

Review these limitations, finish the reachable chat interaction references,
then extract a design specification and assertions before modifying the UI.
The skill's stage review requirement is retained; screenshot estimates must be
distinguished from live measurements. Current app CSS/JS was not changed during
this reconnaissance pass.
