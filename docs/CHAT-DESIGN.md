# Chat interface reference and verification

The chat redesign uses the Slack dark-mode screenshots supplied by the user.
The clone-app-pat-pro workflow was inspected, then adapted with user approval:
the required authenticated Claude Chrome extension is unavailable in this session.
Playwright with local Chromium provides browser verification instead.
This is a screenshot-based implementation, not a measured copy of Slack's private DOM.

## Visual rules

- Workspace background: purple gradient, #391040 to #230727.
- Top search: 40px bar, muted purple #715375 search field.
- Navigation: 70px icon rail plus 245px conversation sidebar.
- Selected conversation: #7d3989; sidebar uses #221326 to #190e1d.
- Conversation background: #1a1d21; composer and cards: #222529.
- Dividers: #383b40; primary text: #e8e8e8; secondary: #c7c7c9.
- Message avatars: 36px; message text: 15px; timestamp: 11px.
- Conversation header and tabs: 51px and 36px.
- Thread/details panel: 360px, closed initially; overlay below 1180px.
- At 760px and below, use a drawer and bottom navigation.
- Use the system font stack. Private Slack user assets and screenshot message
  contents are not copied into the application.

## Working views

Home/channels, DM inbox, Activity, Files, Threads, search, group creation,
directory, and profile use the existing app data and API actions.
Ctrl/Cmd+K opens search. Escape closes the drawer/details panel.
The removed command center has no entry in the redesigned navigation.

## Browser checks

```powershell
.venv/Scripts/python.exe -m pip install -r apps/api/requirements-dev.txt
.venv/Scripts/python.exe -m playwright install chromium
.venv/Scripts/python.exe scripts/test-chat-browser.py
```

The test starts a temporary static server and intercepts API requests with
synthetic fixtures. It checks desktop/mobile layout, thread opening, search,
message submission and draft clearing, DMs, Activity, Files, and mobile
navigation, and fails on browser JavaScript errors. Screenshots are written to
`artifacts/chat/`. It does not alter the live PostgreSQL database or certify
backend persistence, file scanning, or voice hardware.
