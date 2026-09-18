const MAX_FILE_SIZE = 15 * 1024 * 1024 * 1024;

const state = {
  ready: false,
  loading: true,
  authMode: "login",
  authError: "",
  serverHint: "",
  workspaceMode: "home",
  activeChannelId: null,
  activePanel: "thread",
  adminActiveTab: "home",
  adminTabs: ["home"],
  adminSearch: "",
  peopleSearch: "",
  selectedThreadId: null,
  editingMessageId: null,
  editDraft: "",
  sidebarFilter: "",
  searchQuery: "",
  recentSearches: loadRecentSearches(),
  composerDrafts: {},
  composerUrgent: false,
  lastUrgentPingTime: 0,
  uploads: [],
  voice: { recording: false, elapsed: 0, waveform: [] },
  calendarYear: new Date().getFullYear(),
  calendarMonth: new Date().getMonth(),
  selectedCalendarDate: null,
  showEventModal: false,
  showAddUserModal: false,
  eventModalDate: "",
  eventModal: null,
  aiReportSelectedUserId: null,
  aiReportSelectedReportId: null,
  aiReportGenerating: false,
  aiReportTargetUserId: null,
  calendarFilter: "all",
  messageScroll: { channelId: null, top: 0, nearBottom: true },
  viewScroll: {},
  sidebarScrollTop: 0,
  detailsScrollTop: 0,
  ui: { sidebarOpen: false, sidebarCollapsed: false, detailsOpen: false, toasts: [], collapsedNavSections: {} },
  previewFileId: null,
  previewFile: null,
  previewFileContent: null,
  previewFileLoading: false,
  pendingAttachments: []
};

let data = emptyData();
let mediaRecorder = null;
let recordingChunks = [];
let recordingTimer = null;
let pendingFocus = null;
let typingTimer = null;
let lastTypingPing = 0;
let refreshTimer = null;
let refreshInFlight = false;
let currentDayKey = localDayKey();
let liveEventSource = null;
let liveReconnectTimer = null;

const $ = (selector, root = document) => root.querySelector(selector);

function localDayKey(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function emptyData() {
  return {
    currentUser: null,
    users: [],
    groups: [],
    channels: [],
    messages: [],
    files: [],
    tasks: [],
    events: [],
    weeklyReports: [],
    adminWeeklyReports: [],
    announcements: [],
    notifications: [],
    xMentions: [],
    xMentionStatus: { configured: false, running: false, lastPoll: "", lastError: "", cooldownUntil: "", accountIntervalSeconds: 60, lastCheckedAccount: "", retentionDays: 90, monitoredAccounts: [] },
    remoteAssists: [],
    audit: [],
    admin: null,
    settings: { retentionGlobalDays: 365 },
    featureFlags: {},
    config: { auth: {}, files: {}, network: {}, maxFileSizeBytes: MAX_FILE_SIZE }
  };
}

// Sessions are carried by an httpOnly cookie the server sets on login; the page never
// touches the token itself, so it cannot be read or exfiltrated by injected/XSS script.
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
    body: options.body && !(options.body instanceof FormData) ? JSON.stringify(options.body) : options.body
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

async function apiForm(path, formData) {
  const response = await fetch(path, { method: "POST", credentials: "same-origin", body: formData });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

async function init() {
  try {
    const session = await api("/api/session");
    data.config = session.config || data.config;
    if (session.user) {
      await refresh();
      initLiveStream();
    } else {
      state.loading = false;
      state.ready = true;
      render();
    }
  } catch (error) {
    state.loading = false;
    state.ready = true;
    state.serverHint = "";
    render();
  }
}

async function refresh(options = {}) {
  if (refreshInFlight) return;
  refreshInFlight = true;
  const restore = options.preserveMessages === false ? null : captureMessageScroll();
  const previousMessageSignature = activeMessageSignature(data, state.activeChannelId);
  const previousBackgroundSignature = backgroundDataSignature(data);
  try {
    const payload = await api("/api/bootstrap");
    const nextData = { ...emptyData(), ...payload };
    const incomingNotes = payload.notifications || [];
    if (!state.seenNoteIds) {
      state.seenNoteIds = new Set(incomingNotes.map((n) => n.id));
    } else {
      const newNotes = incomingNotes.filter((n) => !state.seenNoteIds.has(n.id) && !n.readAt && !n.read_at);
      for (const note of newNotes) {
        state.seenNoteIds.add(note.id);
        const p = notificationPayload(note);
        const author = (payload.users || []).find((u) => u.id === p.authorId) || { displayName: p.authorName || "Someone", username: p.authorUsername || "user" };
        const chan = (payload.channels || []).find((c) => c.id === p.channelId);
        if (note.type === "x_mention") {
          playNotificationChime();
          void showDesktopNotification({
            title: `X mention: @${p.authorUsername || "user"}`,
            body: (p.snippet || "A monitored account was mentioned on X.").slice(0, 160),
            tag: `x-mention-${p.tweetId || note.id}`,
            workspaceMode: "xmentions",
            url: p.tweetUrl || ""
          });
        }
        if (note.type === "urgent" || p.isUrgent) {
          playUrgentPingChime();
          void showDesktopNotification({
            title: "Important message to read",
            body: `${author.displayName}: "${(p.snippet || p.body || "Important message").slice(0, 110)}"`,
            tag: `urgent-${p.messageId || note.id}`,
            channelId: p.channelId || null,
            messageId: p.messageId || null,
            urgent: true
          });
        } else if (note.type !== "x_mention") {
          const titles = {
            mention: `${author.displayName} mentioned you`,
            tag: `Tag alert: ${p.tag || (p.tags && p.tags[0]) || "#tag"}`,
            calendar_advance: `Upcoming: ${p.clientName || p.title || "Client meeting"}`,
            ai_report_admin: p.title || "AI weekly report ready"
          };
          void showDesktopNotification({
            title: titles[note.type] || `New ${String(note.type).replace(/_/g, " ")}`,
            body: String(p.snippet || p.body || "New activity").slice(0, 160),
            tag: `note-${note.id}`,
            channelId: p.channelId || null,
            messageId: p.messageId || null,
            workspaceMode: note.type === "calendar_advance" ? "calendar" : note.type === "ai_report_admin" ? "reports" : undefined
          });
        }
        showXToast({
          title: note.type === "x_mention" ? `X Mention: @${p.authorUsername || "user"}` : (note.type === "tag" ? `Tag Alert: ${p.tag || "#tag"}` : (note.type === "mention" ? `Mentioned by ${author.displayName}` : `Alert: ${note.type}`)),
          body: p.snippet || p.body || "New activity",
          authorName: author.displayName,
          authorHandle: author.username,
          tag: p.tag || (p.tags && p.tags[0]) || (note.type === "tag" ? "#tag" : null),
          isTag: note.type === "tag",
          channelName: chan?.name || p.channelName || "",
          channelId: p.channelId || null,
          messageId: p.messageId || null,
        });
      }
    }

    notifyNewConversationMessages(payload, incomingNotes);

    const nextBackgroundSignature = backgroundDataSignature(nextData);
    data = nextData;
    if (currentUser() && !liveEventSource && !liveReconnectTimer) {
      initLiveStream();
    }

    if (state.activeChannelId) {
      const active = data.channels.find((c) => c.id === state.activeChannelId);
      if (active) {
        active.unread = 0;
        active.mentions = 0;
      }
    }

    const unackUrgent = getUnacknowledgedUrgentMessages();
    if (unackUrgent.length > 0) {
      const now = Date.now();
      if (!state.lastUrgentPingTime || (now - state.lastUrgentPingTime) > 30 * 60 * 1000) {
        state.lastUrgentPingTime = now;
        playUrgentPingChime();
        const topUrgent = unackUrgent[0];
        const author = userById(topUrgent.authorId);
        void showDesktopNotification({
          title: "Important message needs acknowledgement",
          body: `${author.displayName}: "${topUrgent.body.slice(0, 110)}"`,
          tag: `urgent-${topUrgent.id}`,
          channelId: topUrgent.channelId,
          messageId: topUrgent.id,
          urgent: true
        });
        showXToast({
          title: "🚨 IMPORTANT MESSAGE TO READ",
          body: `${author.displayName}: "${topUrgent.body.slice(0, 90)}" — Please read and acknowledge!`,
          authorName: author.displayName,
          authorHandle: author.username || "user",
          tag: "#URGENT",
          isTag: true,
          channelId: topUrgent.channelId,
          messageId: topUrgent.id,
        });
      }
    }

    if (isSuperAdmin(data.currentUser)) {
      const staleItems = getStaleItems(state.stalePingThresholdMins || 60);
      const now = Date.now();
      if (staleItems.length > 0 && (!state.dismissedPendingPingUntil || now >= state.dismissedPendingPingUntil)) {
        if (!state.lastStalePingTime || (now - state.lastStalePingTime) > 60 * 60 * 1000) {
          state.lastStalePingTime = now;
          playUrgentPingChime();
          void showDesktopNotification({
            title: "Super Admin pending review",
            body: `${staleItems.length} item(s) pending review for more than 1 hour.`,
            tag: "super-admin-pending-review",
            channelId: staleItems[0]?.channelId || null,
            messageId: staleItems[0]?.messageId || null,
            urgent: true
          });
          showXToast({
            title: "Super Admin Alert",
            body: `You have ${staleItems.length} item(s) pending review for > 1 hour!`,
            authorName: "System",
            authorHandle: "beenco",
            tag: "#URGENT",
            isTag: true,
          });
        }
      }
    }

    if (!state.activeChannelId || !data.channels.some((channel) => channel.id === state.activeChannelId)) {
      state.activeChannelId = data.channels.find((channel) => channel.slug === "general")?.id || data.channels[0]?.id || null;
    }
    if (!state.selectedThreadId) {
      state.selectedThreadId = data.messages.find((message) => message.channelId === state.activeChannelId && !message.parentId)?.id || null;
    }
    if (!state.workspaceMode) state.workspaceMode = "home";
    const nextMessageSignature = activeMessageSignature(data, state.activeChannelId);
    if (restore && restore.channelId === state.activeChannelId) {
      state.messageScroll.pendingRestore = {
        ...restore,
        mode: options.pinToBottom || (restore.nearBottom && previousMessageSignature !== nextMessageSignature) ? "bottom" : "exact"
      };
    } else if (options.pinToBottom) {
      state.messageScroll.pendingRestore = { channelId: state.activeChannelId, top: 0, nearBottom: true, mode: "bottom" };
    }
    state.loading = false;
    state.ready = true;
    if (options.background && nextBackgroundSignature === previousBackgroundSignature) {
      return;
    }
    render({ background: Boolean(options.background) });
  } finally {
    refreshInFlight = false;
  }
}

function esc(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function initials(name = "?") {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => esc(part[0]?.toUpperCase() || ""))
    .join("") || "?";
}

function formatTime(value) {
  return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function formatBytes(bytes = 0) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatRelative(value) {
  if (!value) return "never";
  const seconds = Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function uid(prefix) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function captureCurrentScrolls() {
  const scroll = $("#messageScroll");
  if (scroll) {
    const mode = state.workspaceMode || "home";
    if (["activity", "files", "threads", "calendar", "reports", "briefing", "xmentions"].includes(mode)) {
      if (!state.viewScroll) state.viewScroll = {};
      state.viewScroll[mode] = scroll.scrollTop;
    } else {
      state.messageScroll.top = scroll.scrollTop;
      state.messageScroll.nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96;
      state.messageScroll.channelId = state.activeChannelId;
    }
  }
  const sidebar = $(".sidebar-scroll");
  if (sidebar) state.sidebarScrollTop = sidebar.scrollTop;
  const details = $(".details-body");
  if (details) state.detailsScrollTop = details.scrollTop;
}

function captureMessageScroll() {
  const scroll = $("#messageScroll");
  if (!scroll) return null;
  const mode = state.workspaceMode || "home";
  if (["activity", "files", "threads", "calendar", "reports", "briefing", "xmentions"].includes(mode)) {
    if (!state.viewScroll) state.viewScroll = {};
    state.viewScroll[mode] = scroll.scrollTop;
  }
  return {
    channelId: state.activeChannelId,
    top: scroll.scrollTop,
    bottom: Math.max(0, scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight),
    nearBottom: scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96
  };
}

function activeMessageSignature(source = data, channelId = state.activeChannelId) {
  if (!source?.messages?.length || !channelId) return "";
  return source.messages
    .filter((message) => message.channelId === channelId && !message.parentId)
    .map((message) => `${message.id}:${message.editedAt || ""}:${message.deletedAt || ""}:${message.attachments?.join(",") || ""}:${Object.entries(message.reactions || {}).map(([emoji, users]) => `${emoji}-${users.length}`).join(",")}`)
    .join("|");
}

function backgroundDataSignature(source = data) {
  const channelId = state.activeChannelId || "";
  const activeMessages = (source.messages || [])
    .filter((message) => message.channelId === channelId && !message.parentId)
    .map((message) => `${message.id}:${message.editedAt || ""}:${message.deletedAt || ""}:${message.readBy?.length || 0}:${Object.values(message.reactions || {}).map((users) => users.length).join(",")}`)
    .join("|");
  const notifications = (source.notifications || [])
    .map((note) => `${note.id}:${note.readAt || note.read_at || ""}`)
    .join("|");
  const xMentions = state.workspaceMode === "xmentions"
    ? (source.xMentions || []).map((mention) => `${mention.tweetId}:${mention.discoveredAt || ""}`).join("|")
    : "";
  const modeMeta = ["briefing", "calendar", "reports", "files", "activity", "threads"].includes(state.workspaceMode)
    ? `${state.workspaceMode}:${(source.files || []).length}:${(source.events || []).length}:${(source.weeklyReports || []).length}:${(source.adminWeeklyReports || []).length}`
    : "";
  return `${channelId}::${activeMessages}::${notifications}::${xMentions}::${modeMeta}`;
}

function currentUser() {
  return data.currentUser;
}

function onlineUsers() {
  return data.users.filter((user) => user.isOnline || user.presence === "online");
}

function activeChannelMembers() {
  // userById() falls back to a synthetic "System" placeholder for any id it can't resolve, and
  // that placeholder still has an .id — so a plain `.filter(user => user && user.id)` here never
  // actually excluded anything. A channel whose members list still referenced deleted/deactivated
  // accounts (data.users only holds active ones) would count every stale id as a real member.
  const activeIds = new Set(data.users.map((user) => user.id));
  return (activeChannel().members || []).filter((id) => activeIds.has(id)).map(userById);
}

// Presence is fully automatic: online means active in the app within the last 5 minutes
// (see is_user_online on the server), with no away/dnd/offline states to pick manually.
function presenceLabel(user) {
  if (user.isOnline) return "Online now";
  return user.lastSeenAt ? `Last seen ${formatRelative(user.lastSeenAt)}` : "Offline";
}

function userById(id) {
  return data.users.find((user) => user.id === id) || {
    id,
    displayName: "System",
    username: "system",
    email: "",
    role: "system",
    presence: "offline",
    title: "Internal service",
    department: "Platform",
    lastSeenAt: "",
    isOnline: false,
    groups: []
  };
}

function fileById(id) {
  if (!id) return null;
  const found = (data.files || []).find((file) => file.id === id);
  if (found) return found;
  if (state.previewFile && state.previewFile.id === id) return state.previewFile;
  for (const msg of (data.messages || [])) {
    if (msg.attachmentFiles && Array.isArray(msg.attachmentFiles)) {
      const match = msg.attachmentFiles.find((f) => f.id === id);
      if (match) return match;
    }
  }
  return null;
}

function activeChannel() {
  return data.channels.find((channel) => channel.id === state.activeChannelId) || data.channels[0] || {
    id: null,
    name: "No channel",
    slug: "none",
    type: "public",
    topic: "Create or join a channel",
    description: "",
    members: [],
    pinned: [],
    locked: false
  };
}

function peopleCandidates() {
  const channelMemberIds = new Set(activeChannelMembers().map((user) => user.id));
  return data.users
    .filter((user) => user.id !== currentUser()?.id && user.isActive !== false)
    .sort((a, b) => {
      const aInChannel = channelMemberIds.has(a.id) ? 1 : 0;
      const bInChannel = channelMemberIds.has(b.id) ? 1 : 0;
      return bInChannel - aInChannel || Number(b.isOnline) - Number(a.isOnline) || a.displayName.localeCompare(b.displayName);
    });
}

function filePolicy() {
  return data.config.files || { allowedExtensions: [], blockedExtensions: [], maxFileSizeBytes: data.config.maxFileSizeBytes || MAX_FILE_SIZE };
}

function safeExternalUrl(value = "") {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return "";
    return url.href;
  } catch {
    return "";
  }
}

function collectSharedLinks({ channelId = null } = {}) {
  const seen = new Set();
  const links = [];
  for (const message of data.messages || []) {
    if (message.deletedAt || !message.body) continue;
    if (channelId && message.channelId !== channelId) continue;
    const matches = String(message.body).match(/https?:\/\/[^\s<>)\]}"]+/g) || [];
    for (const raw of matches) {
      const clean = raw.replace(/[.,!?;:]+$/, "");
      const href = safeExternalUrl(clean);
      if (!href || seen.has(href)) continue;
      seen.add(href);
      links.push({
        href,
        label: clean,
        channelId: message.channelId,
        messageId: message.id,
        authorId: message.authorId,
        createdAt: message.createdAt
      });
    }
  }
  return links.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}

function fileExtension(name = "") {
  const clean = String(name).trim().toLowerCase();
  const index = clean.lastIndexOf(".");
  return index > -1 ? clean.slice(index) : "";
}

function isUploadAllowed(file) {
  const policy = filePolicy();
  const allowed = new Set(policy.allowedExtensions || []);
  const blocked = new Set(policy.blockedExtensions || []);
  const extension = fileExtension(file.name);
  if (!extension || blocked.has(extension) || (allowed.size && !allowed.has(extension))) {
    return { ok: false, message: `${extension || "This file"} is not allowed. Use approved document, image, audio, or video types.` };
  }
  const maxBytes = Math.min(policy.directUploadMaxBytes || policy.maxFileSizeBytes || MAX_FILE_SIZE, policy.maxFileSizeBytes || data.config.maxFileSizeBytes || MAX_FILE_SIZE);
  if (file.size > maxBytes) return { ok: false, message: `${file.name} exceeds ${formatBytes(maxBytes)}.` };
  return { ok: true };
}

function remoteAssistPeer(session) {
  const userId = session?.requesterId === currentUser()?.id ? session.targetUserId : session?.requesterId;
  return userById(userId);
}

function remoteAssistStatusLabel(session) {
  if (!session) return "No request";
  if (session.status === "pending") return session.targetUserId === currentUser()?.id ? "Needs your approval" : "Waiting for approval";
  if (session.status === "accepted") return "Approved";
  if (session.status === "cancelled") return "Cancelled";
  if (session.status === "rejected") return "Rejected";
  return "Ended";
}

function safeFilename(value = "remote-assist") {
  return String(value).replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 80) || "remote-assist";
}

function rdpFileContent(session) {
  const host = session?.targetHost || "";
  return [
    "screen mode id:i:2",
    "use multimon:i:1",
    "desktopwidth:i:1920",
    "desktopheight:i:1080",
    "session bpp:i:32",
    `full address:s:${host}`,
    "prompt for credentials:i:1",
    "authentication level:i:2",
    "enablecredsspsupport:i:1",
    "redirectclipboard:i:1",
    "audiomode:i:0"
  ].join("\r\n");
}

function isSuperAdmin(user = currentUser()) {
  return Boolean(user && ["super_admin", "admin"].includes(user.role));
}

function isAdmin(user = currentUser()) {
  return Boolean(user && ["super_admin", "admin", "moderator", "it_admin"].includes(user.role));
}

// AI weekly reports contain DM snippets, so only admins/super admins may see or generate them.
function isReportAdmin(user = currentUser()) {
  return Boolean(user && ["super_admin", "admin"].includes(user.role));
}

function isChannelOwner(channel = activeChannel(), user = currentUser()) {
  if (!user || !channel) return false;
  return Boolean(isAdmin(user) || isSuperAdmin(user) || (channel.createdBy && channel.createdBy === user.id) || (channel.ownerIds || []).includes(user.id));
}

function canDeleteChannel(channel = activeChannel(), user = currentUser()) {
  if (!user || !channel) return false;
  const slug = (channel.slug || channel.name || "").trim().toLowerCase();
  if (slug === "general" || slug === "announcements") return false;
  if (channel.type === "dm") return false;
  return isChannelOwner(channel, user);
}

function canUseCommandCenter(user = currentUser()) {
  return Boolean(user && ["super_admin", "admin", "moderator", "it_admin", "security_analyst", "hr_admin", "hr_viewer"].includes(user.role));
}

function canUsePortal(portalId, user = currentUser()) {
  if (!user) return false;
  const role = user.role;
  if (portalId === "intranet-admin") return ["super_admin", "admin", "moderator", "it_admin"].includes(role);
  if (portalId === "shuffle") return ["super_admin", "admin", "moderator", "it_admin", "security_analyst"].includes(role);
  if (portalId === "hr") return ["super_admin", "admin", "hr_admin", "hr_viewer"].includes(role);
  return false;
}

function canWriteHr(user = currentUser()) {
  return Boolean(user && ["super_admin", "admin", "hr_admin"].includes(user.role));
}

function isChannelParticipant(channel = activeChannel(), user = currentUser()) {
  return Boolean(user && (channel.members || []).includes(user.id));
}

// Admins can open DMs they are not part of, but only to read them.
function isViewOnlyDm(channel = activeChannel()) {
  return ["dm", "group_dm"].includes(channel.type) && !isChannelParticipant(channel);
}

function channelName(channel = activeChannel()) {
  if (channel.type !== "dm") return channel.type === "private" ? `lock ${channel.name}` : `#${channel.name}`;
  const members = (channel.members || []).map(userById).filter(Boolean);
  if (!isChannelParticipant(channel)) {
    return members.length ? members.map((user) => user.displayName).join(" ↔ ") : channel.name;
  }
  const other = members.find((user) => user.id !== currentUser()?.id);
  return other ? `@${other.displayName}` : channel.name;
}

function toast(title, body = "") {
  state.ui.toasts.push({ id: uid("toast"), title, body });
  renderToasts();
  setTimeout(() => {
    state.ui.toasts = state.ui.toasts.filter((item) => item.title !== title || item.body !== body);
    renderToasts();
  }, 3800);
}

function playNotificationChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const now = ctx.currentTime;
    const osc1 = ctx.createOscillator();
    const gain1 = ctx.createGain();
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(587.33, now);
    gain1.gain.setValueAtTime(0.15, now);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.3);
    osc1.connect(gain1);
    gain1.connect(ctx.destination);
    osc1.start(now);
    osc1.stop(now + 0.3);

    const osc2 = ctx.createOscillator();
    const gain2 = ctx.createGain();
    osc2.type = "sine";
    osc2.frequency.setValueAtTime(880, now + 0.12);
    gain2.gain.setValueAtTime(0.2, now + 0.12);
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
    osc2.connect(gain2);
    gain2.connect(ctx.destination);
    osc2.start(now + 0.12);
    osc2.stop(now + 0.5);
  } catch (err) {}
}

function playUrgentPingChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const now = ctx.currentTime;
    [0, 0.18, 0.36].forEach((offset, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(idx === 2 ? 987.77 : 783.99, now + offset);
      gain.gain.setValueAtTime(0.22, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.22);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + offset);
      osc.stop(now + offset + 0.22);
    });
  } catch (err) {}
}

// Browsers only allow notifications on HTTPS or localhost; a LAN http:// address is not a secure context.
function canUseDesktopNotifications() {
  return "Notification" in window && location.protocol !== "file:" && window.isSecureContext;
}

function showDesktopAlertsPrompt() {
  return "Notification" in window && !(canUseDesktopNotifications() && Notification.permission === "granted");
}

async function showDesktopNotification({ title, body, tag, channelId, messageId, workspaceMode, url, urgent = false }) {
  // Permission is only requested from a click (see requestDesktopNotificationPermission);
  // browsers ignore prompts triggered by background polling.
  if (!canUseDesktopNotifications() || Notification.permission !== "granted") return false;
  try {
    const notificationOptions = {
      body,
      tag: tag || messageId || title,
      renotify: true,
      requireInteraction: urgent,
      icon: "./icons/icon-192.svg",
      badge: "./icons/icon-192.svg",
      data: { channelId, messageId, workspaceMode, url }
    };
    if ("serviceWorker" in navigator) {
      // serviceWorker.ready never resolves without an active worker, so don't wait on it.
      const registration = await navigator.serviceWorker.getRegistration().catch(() => null);
      if (registration?.active && registration.showNotification) {
        try {
          await registration.showNotification(title, notificationOptions);
          state.lastDesktopNotification = { via: "service worker", error: "" };
          return true;
        } catch (err) {
          // Fall through to the page-level Notification API.
          state.lastDesktopNotification = { via: "service worker", error: err?.message || String(err) };
        }
      }
    }
    const { badge, data: _data, ...pageOptions } = notificationOptions;
    const note = new Notification(title, pageOptions);
    state.lastDesktopNotification = { via: "page", error: "" };
    note.onclick = () => {
      window.focus();
      if (workspaceMode) state.workspaceMode = workspaceMode;
      if (channelId) state.activeChannelId = channelId;
      if (messageId) {
        state.selectedThreadId = messageId;
      }
      render();
      setTimeout(() => scrollMessageIntoView(messageId), 50);
      note.close();
    };
    return true;
  } catch (err) {
    state.lastDesktopNotification = { via: "page", error: err?.message || String(err) };
    console.warn("[Desktop notification failed]", err);
    return false;
  }
}

async function requestDesktopNotificationPermission({ test = false } = {}) {
  if (!canUseDesktopNotifications()) {
    toast(
      "Desktop notifications unavailable",
      window.isSecureContext === false
        ? `Browsers block notifications on ${location.origin}. Open the app via https:// or http://localhost:4173.`
        : "This browser does not support desktop notifications."
    );
    return false;
  }
  let permission = Notification.permission;
  if (permission === "default") {
    permission = await Notification.requestPermission();
  }
  if (permission !== "granted") {
    toast("Desktop notifications blocked", "Click the icon left of the address bar → Site settings → Notifications → Allow, then reload.");
    return false;
  }
  if (!test) {
    toast("Desktop notifications enabled", "Browser permission is granted.");
    return true;
  }
  const testTag = `beenco-test-${Date.now()}`;
  const shown = await showDesktopNotification({
    title: "Beenco notification test",
    body: "Desktop notifications are working.",
    tag: testTag,
    workspaceMode: state.workspaceMode || "home",
    urgent: true
  });
  const result = state.lastDesktopNotification || {};
  if (!shown) {
    toast("Browser could not create the notification", result.error || "Unknown error. Check the browser console (F12).");
    return false;
  }
  // Confirm the browser really holds the notification. If it does but nothing appears on screen,
  // the operating system is hiding it (Windows notification settings / Do not disturb).
  let confirmed = result.via === "page";
  if (result.via === "service worker") {
    const registration = await navigator.serviceWorker.getRegistration().catch(() => null);
    const active = await registration?.getNotifications({ tag: testTag }).catch(() => []);
    confirmed = Boolean(active?.length);
  }
  toast(
    confirmed ? "Test notification sent by the browser" : "Test notification may not have been created",
    confirmed
      ? "Nothing on screen? Windows is hiding it: Settings → System → Notifications → turn on notifications and Google Chrome (or your browser), and turn off Do not disturb."
      : `Created via ${result.via || "unknown"}, but the browser does not list it. ${result.error || ""}`
  );
  return true;
}

// Desktop alerts for new DM / group messages. These have no server notification row,
// so detect them by diffing message ids between refreshes.
function notifyNewConversationMessages(payload, incomingNotes) {
  const me = payload.currentUser?.id;
  const messages = payload.messages || [];
  if (!state.seenMessageIds) {
    state.seenMessageIds = new Set(messages.map((m) => m.id));
    return;
  }
  const fresh = messages.filter((m) => !state.seenMessageIds.has(m.id));
  fresh.forEach((m) => state.seenMessageIds.add(m.id));
  // Mentions and urgent messages already get their own alert from the notification loop.
  const alreadyAlerted = new Set(incomingNotes.map((n) => notificationPayload(n).messageId).filter(Boolean));
  const pageFocused = !document.hidden && document.hasFocus();
  for (const message of fresh.slice(-5)) {
    if (message.authorId === me || message.deletedAt || alreadyAlerted.has(message.id)) continue;
    const channel = (payload.channels || []).find((c) => c.id === message.channelId);
    // Only conversations the user is actually in; admin read-only DMs and busy public channels stay quiet.
    if (!channel || !["dm", "group_dm", "private"].includes(channel.type) || !(channel.members || []).includes(me)) continue;
    if (pageFocused && state.activeChannelId === message.channelId) continue;
    const author = (payload.users || []).find((u) => u.id === message.authorId);
    const authorName = author?.displayName || "Someone";
    void showDesktopNotification({
      title: channel.type === "private" ? `${authorName} in ${channel.name}` : authorName,
      body: String(message.body || (message.attachments?.length ? "Sent an attachment" : "New message")).slice(0, 160),
      tag: `message-${message.id}`,
      channelId: message.channelId,
      messageId: message.id
    });
  }
}

function scrollMessageIntoView(messageId) {
  if (!messageId) return;
  const element = document.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (!element) return;
  element.scrollIntoView({ block: "center", behavior: "smooth" });
}

function showXToast({ title, body, authorName, authorHandle, tag, isTag, isUrgent, channelName, channelId, messageId }) {
  const id = uid("toast");
  state.ui.toasts.push({
    id,
    title,
    body,
    authorName,
    authorHandle,
    tag,
    isTag,
    isUrgent: Boolean(isUrgent),
    channelName,
    channelId,
    messageId,
    isXStyle: true,
  });
  renderToasts();
  playNotificationChime();
  setTimeout(() => {
    state.ui.toasts = state.ui.toasts.filter((t) => t.id !== id);
    renderToasts();
  }, isUrgent ? 25000 : 9000);
}

function unreadNotificationsCount() {
  return (data.notifications || []).filter((n) => !n.readAt && !n.read_at).length;
}

function getUnacknowledgedUrgentMessages() {
  if (!data?.currentUser) return [];
  const currentUserId = data.currentUser.id;
  return (data.messages || []).filter((msg) => {
    if (!msg.isUrgent || msg.deletedAt) return false;
    if (msg.acknowledgedAt) return false;
    if (msg.authorId === currentUserId) return false;
    const channel = (data.channels || []).find((c) => c.id === msg.channelId);
    if (!channel) return false;
    return true;
  });
}

function getStaleItems(thresholdMinutes = 60) {
  const cutoff = Date.now() - thresholdMinutes * 60 * 1000;
  return getUnacknowledgedUrgentMessages()
    .filter((message) => new Date(message.createdAt || message.created_at).getTime() <= cutoff)
    .map((message) => {
      const author = userById(message.authorId);
      return {
        id: message.id,
        type: "urgent_message",
        subType: "urgent",
        title: `Important to Read from ${author.displayName}`,
        detail: message.body || "Important message awaiting acknowledgement",
        createdAt: message.createdAt || message.created_at,
        channelId: message.channelId,
        messageId: message.id,
      };
    });
}

function renderStalePingBanner() {
  if (!isSuperAdmin()) return "";
  const urgentMessages = getUnacknowledgedUrgentMessages();
  if (urgentMessages.length > 0) {
    const urgent = urgentMessages[0];
    const author = userById(urgent.authorId);
    return `
      <div class="stale-ping-banner urgent-alert-banner" role="alert">
        <div class="stale-ping-content">
          <span class="stale-ping-pulse">🚨</span>
          <div class="stale-ping-info">
            <strong>URGENT MESSAGE (${urgentMessages.length} pending): ${esc(author.displayName)} marked a message as Important!</strong>
            <span>"${esc(urgent.body.slice(0, 110))}" &mdash; Awaiting your acknowledgment</span>
          </div>
        </div>
        <div class="stale-ping-actions">
          <button class="btn-press-ack urgent-ack-btn" data-action="acknowledge-message" data-message-id="${urgent.id}"><span class="ack-check-icon">✓</span> PRESS TO ACKNOWLEDGE</button>
          <button class="ghost-btn small-btn" data-action="jump-message" data-channel-id="${urgent.channelId}" data-message-id="${urgent.id}">Jump →</button>
        </div>
      </div>
    `;
  }
  if (state.dismissedPendingPingUntil && Date.now() < state.dismissedPendingPingUntil) return "";
  const staleItems = getStaleItems(state.stalePingThresholdMins || 60);
  if (!staleItems.length && !state.simulateStalePing) return "";
  const count = staleItems.length || 1;
  const firstItem = staleItems[0] || { title: "Review Item / #oro", detail: "Awaiting review" };
  const targetChannel = firstItem.channelId ? data.channels.find((c) => c.id === firstItem.channelId) : null;
  return `
    <div class="stale-ping-banner" role="alert">
      <div class="stale-ping-content">
        <span class="stale-ping-icon">🔔</span>
        <div class="stale-ping-info">
          <strong>Pending Review (>1h): ${count} alert${count > 1 ? "s" : ""} awaiting review</strong>
          <span>${esc(firstItem.title)} &mdash; ${esc(firstItem.detail)}</span>
        </div>
      </div>
      <div class="stale-ping-actions">
        ${targetChannel ? `
          <button class="primary-btn small-btn" data-action="jump-message" data-channel-id="${firstItem.channelId}" data-message-id="${firstItem.messageId || ""}" data-note-id="${firstItem.id || ""}">
            View #${esc(channelName(targetChannel))} &rarr;
          </button>
        ` : `
          <button class="primary-btn small-btn" data-action="review-stale-pings">Review Items</button>
        `}
        <button class="btn-press-ack small-btn" data-action="mark-all-stale-reviewed">
          <span class="ack-check-icon">✓</span> Mark Reviewed
        </button>
        <button class="icon-btn-micro dismiss-btn" data-action="dismiss-stale-ping" title="Dismiss this alert" aria-label="Dismiss">✕</button>
      </div>
    </div>
  `;
}

function renderUrgentTopbarButton() {
  if (!isSuperAdmin()) return "";
  const unack = getUnacknowledgedUrgentMessages();
  if (!unack.length) return "";
  const topMsg = unack[0];
  const author = userById(topMsg.authorId);
  return `
    <button class="topbar-urgent-banner-btn" data-action="acknowledge-message" data-message-id="${topMsg.id}" title="Click to acknowledge urgent message from ${esc(author.displayName)}">
      <span class="pulse-siren">🚨</span>
      <strong>URGENT (${unack.length}):</strong>
      <span>${esc(author.displayName)}: "${esc(topMsg.body.slice(0, 30))}"</span>
      <span class="topbar-ack-action-badge">✓ PRESS TO ACKNOWLEDGE</span>
    </button>
  `;
}

const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isEditableElement(element) {
  return Boolean(element && EDITABLE_TAGS.has(element.tagName));
}

function elementPath(element) {
  const parts = [];
  let node = element;
  let depth = 0;
  while (node && node !== document.body && depth < 8) {
    const parent = node.parentElement;
    if (!parent) break;
    const index = Array.prototype.indexOf.call(parent.children, node);
    parts.unshift(`${node.tagName}:${index}`);
    node = parent;
    depth += 1;
  }
  return parts.join(">");
}

function captureFocusState() {
  const active = document.activeElement;
  if (!isEditableElement(active)) return null;
  const form = active.closest("form");
  const formFields = [];
  if (form?.id) {
    form.querySelectorAll("input[name], textarea[name]").forEach((field) => {
      if (field.value) formFields.push({ name: field.name, value: field.value });
    });
  }
  return {
    path: elementPath(active),
    action: active.dataset?.action || null,
    messageId: active.dataset?.messageId || null,
    formId: form?.id || null,
    formFields,
    name: active.name || null,
    value: active.value,
    selectionStart: active.selectionStart ?? null,
    selectionEnd: active.selectionEnd ?? null,
    hasUnsavedText: (active.tagName !== "SELECT" && active.value !== "") || formFields.length > 0
  };
}

function findByPath(path) {
  if (!path) return null;
  let node = $("#app");
  for (const segment of path.split(">")) {
    if (!node) return null;
    const [tag, indexStr] = segment.split(":");
    const index = Number(indexStr);
    const candidate = node.children[index];
    if (!candidate || candidate.tagName !== tag) return null;
    node = candidate;
  }
  return node;
}

function findFocusTarget(focusState) {
  if (focusState.action) {
    return $(focusState.messageId ? `[data-action="${focusState.action}"][data-message-id="${focusState.messageId}"]` : `[data-action="${focusState.action}"]`);
  }
  if (focusState.formId && focusState.name) {
    const scoped = $(`#${focusState.formId} [name="${focusState.name}"]`);
    if (scoped) return scoped;
  }
  const byPath = findByPath(focusState.path);
  if (byPath && (!focusState.name || byPath.name === focusState.name)) return byPath;
  if (focusState.name) {
    const candidates = document.querySelectorAll(`[name="${focusState.name}"]`);
    if (candidates.length === 1) return candidates[0];
  }
  return null;
}

function restoreFocusState(focusState) {
  if (!focusState) return;
  if (focusState.formId) {
    const form = document.getElementById(focusState.formId);
    if (form) {
      for (const field of focusState.formFields) {
        const target = form.querySelector(`[name="${field.name}"]`);
        if (target && isEditableElement(target) && target.tagName !== "SELECT" && !target.value) {
          target.value = field.value;
        }
      }
    }
  }
  if (focusState.action === "composer-draft") {
    const element = findFocusTarget(focusState);
    if (element) {
      element.value = state.composerDrafts[state.activeChannelId] || "";
      element.focus();
      const len = element.value.length;
      if (typeof element.selectionStart === "number") {
        element.setSelectionRange(len, len);
      }
    }
    return;
  }
  if (focusState.formId === "threadReplyForm") {
    const element = findFocusTarget(focusState);
    if (element) element.focus();
    return;
  }
  const element = findFocusTarget(focusState);
  if (!element) return;
  if (isEditableElement(element) && element.tagName !== "SELECT" && element.value !== focusState.value) {
    element.value = focusState.value;
  }
  element.focus();
  if (typeof element.selectionStart === "number" && focusState.selectionStart !== null) {
    element.setSelectionRange(focusState.selectionStart, focusState.selectionEnd);
  }
}

function render(options = {}) {
  const app = $("#app");
  if (!app) return;
  if (state.loading) {
    app.innerHTML = `<main class="loading-view"><span class="brand-mark">B</span><strong>Loading Beenco Connect</strong></main>`;
    return;
  }
  const focusState = captureFocusState();
  if (options.background && focusState?.hasUnsavedText) {
    return;
  }
  captureCurrentScrolls();
  app.innerHTML = currentUser() ? renderShell() : renderLogin();
  afterRender();
  restoreFocusState(focusState);
}

function renderLogin() {
  const isSignup = state.authMode === "signup";
  return `
    <div class="app login-view">
      <div class="gemini-ambient" aria-hidden="true">
        <div class="gemini-glow glow-1"></div>
        <div class="gemini-glow glow-2"></div>
        <div class="gemini-glow glow-3"></div>
      </div>
      <section class="login-art" aria-label="Beenco Connect">
        <div class="brand-lockup">
          <span class="brand-mark gemini-mark">
            <svg class="gemini-spark" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 2L14.4 9.6L22 12L14.4 14.4L12 22L9.6 14.4L2 12L9.6 9.6L12 2Z" fill="url(#geminiGrad)" />
              <defs>
                <linearGradient id="geminiGrad" x1="2" y1="2" x2="22" y2="22" gradientUnits="userSpaceOnUse">
                  <stop stop-color="#38bdf8"/>
                  <stop offset="0.5" stop-color="#818cf8"/>
                  <stop offset="1" stop-color="#c084fc"/>
                </linearGradient>
              </defs>
            </svg>
          </span>
          <span class="brand-title">Beenco Connect</span>
          <span class="gemini-version-pill">v2.0</span>
        </div>
        <div class="login-copy">
          <div class="login-pill-badge">
            <span class="spark-icon">✦</span>
            <span>Intelligent Enterprise Comms</span>
          </div>
          <h1>Next-generation communication, secured inside your infrastructure.</h1>
          <p>A unified, high-speed workspace for real-time collaboration, encrypted channels, automated team intelligence, and mission-critical workflows.</p>
        </div>
        <div class="system-strip">
          <div class="system-pill">
            <div class="system-pill-title">
              <span class="system-pill-dot dot-cyan"></span>
              <strong>Active Directory</strong>
            </div>
            <span>Enterprise Keycloak SSO & LDAP identity sync</span>
          </div>
          <div class="system-pill">
            <div class="system-pill-title">
              <span class="system-pill-dot dot-indigo"></span>
              <strong>Encrypted Vault</strong>
            </div>
            <span>Zero-leak transactional storage with automated retention</span>
          </div>
          <div class="system-pill">
            <div class="system-pill-title">
              <span class="system-pill-dot dot-purple"></span>
              <strong>Autonomous RBAC</strong>
            </div>
            <span>Multi-tier permissions & Super Admin authority</span>
          </div>
        </div>
      </section>
      <section class="login-panel">
        <div class="auth-box gemini-card">
          <header>
            <div class="auth-card-eyebrow">
              <span class="security-chip">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                ${isSignup ? "New Registration" : "Secured Portal"}
              </span>
            </div>
            <h2>${isSignup ? "Create workspace profile" : "Welcome back"}</h2>
            <p class="muted">${isSignup ? "Set up your verified company credentials." : "Sign in to access your channels and team discussions."}</p>
          </header>
          <div class="auth-tabs" role="tablist">
            <button class="tab-btn ${!isSignup ? "is-active" : ""}" data-action="auth-mode" data-mode="login">Sign in</button>
            <button class="tab-btn ${isSignup ? "is-active" : ""}" data-action="auth-mode" data-mode="signup">Sign up</button>
          </div>
          ${state.authError ? `<div class="error-banner">${esc(state.authError)}</div>` : ""}
          <form class="auth-form" id="${isSignup ? "signupForm" : "loginForm"}">
            ${isSignup ? `
              <label class="field">
                <span>Full name</span>
                <input name="displayName" autocomplete="name" required placeholder="e.g. Alex Morgan" />
              </label>
            ` : ""}
            <label class="field">
              <span>Company email</span>
              <input name="email" type="email" autocomplete="email" required placeholder="name@beenco.local" />
            </label>
            <label class="field">
              <span>Password</span>
              <input name="password" type="password" autocomplete="${isSignup ? "new-password" : "current-password"}" required ${isSignup ? 'minlength="10" placeholder="Min 10 characters, upper + lower case and a number"' : 'placeholder="Enter password"'} />
            </label>
            <button class="primary-btn gemini-btn" type="submit">
              <span>${isSignup ? "Initialize Account" : "Access Workspace"}</span>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </button>
          </form>
          <div class="auth-footer-shield">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>
            <span>End-to-end audit logging & role-based isolation</span>
          </div>
        </div>
      </section>
    </div>
  `;
}

function adminData() {
  return data.admin || { portals: [], activity: [], intranet: {}, hr: {}, shuffle: {} };
}

function portalById(portalId) {
  return (adminData().portals || []).find((portal) => portal.id === portalId);
}

function portalName(portalId) {
  if (portalId === "home") return "Home";
  return portalById(portalId)?.name || portalId;
}

function renderCommandCenter() {
  const admin = adminData();
  const tabs = state.adminTabs.filter((tab) => tab === "home" || portalById(tab));
  if (!tabs.includes(state.adminActiveTab)) state.adminActiveTab = "home";
  return `
    <div class="app command-center">
      ${renderStalePingBanner()}
      <header class="command-topbar">
        <div class="workspace-title">
          <span class="brand-mark">B</span>
          <div><h1>Command Center</h1><p>${esc(currentUser().displayName)} - ${esc(currentUser().role)}</p></div>
        </div>
        <label class="command-search">
          <span class="sr-only">Global search</span>
          <input data-action="admin-search" value="${esc(state.adminSearch)}" placeholder="Search portals, people, assets, audit" />
        </label>
        <div class="header-actions">
          ${renderUrgentTopbarButton()}
          <span class="connection-state">${admin.portals?.length || 0} portals</span>
          <button class="ghost-btn small-btn" data-action="switch-chat">Switch to Chat</button>
          <button class="ghost-btn small-btn" data-action="logout">Logout</button>
        </div>
      </header>
      <nav class="command-tabs" aria-label="Open portals">
        ${tabs.map((tab) => `
          <div class="command-tab ${state.adminActiveTab === tab ? "is-active" : ""}">
            <button class="tab-btn" data-action="switch-admin-tab" data-tab-id="${tab}">${esc(portalName(tab))}</button>
            ${tab !== "home" ? `<button class="tab-close" data-action="close-admin-tab" data-tab-id="${tab}" title="Close ${esc(portalName(tab))}" aria-label="Close ${esc(portalName(tab))}">x</button>` : ""}
          </div>
        `).join("")}
      </nav>
      <main class="command-main">${state.adminActiveTab === "home" ? renderCommandLauncher() : renderPortalApp(state.adminActiveTab)}</main>
      <div class="toast-region" id="toasts"></div>
    </div>
  `;
}

function renderCommandLauncher() {
  const admin = adminData();
  const query = state.adminSearch.trim().toLowerCase();
  const localResults = query ? [
    ...(admin.portals || []).filter((portal) => `${portal.name} ${portal.description}`.toLowerCase().includes(query)).map((portal) => ({ type: "portal", title: portal.name, subtitle: portal.description, portalId: portal.id })),
    ...(admin.hr?.employees || []).filter((employee) => `${employee.fullName} ${employee.email} ${employee.department} ${employee.designation}`.toLowerCase().includes(query)).slice(0, 8).map((employee) => ({ type: "employee", title: employee.fullName, subtitle: `${employee.designation} - ${employee.department}`, portalId: "hr" })),
    ...(admin.hr?.assets || []).filter((asset) => `${asset.assetTag} ${asset.category} ${asset.model} ${asset.serial}`.toLowerCase().includes(query)).slice(0, 8).map((asset) => ({ type: "asset", title: asset.assetTag, subtitle: `${asset.category} - ${asset.status}`, portalId: "hr" }))
  ] : [];
  return `
    <section class="brand-hero" aria-label="Beenco Labs">
      <div class="brand-hero-copy">
        <span class="status available">Beenco Command Center</span>
        <h2>Internal systems, people, security, and automation in one owned workspace.</h2>
        <p>Admin operations stay connected to the employee portal, stored in the local database, and ready for production identity, SIEM, and SOAR integrations.</p>
      </div>
      <img src="./assets/beenco-command-hero.svg" alt="Beenco Labs visual" loading="eager" />
    </section>
    <section class="launcher-grid">
      ${(admin.portals || []).map((portal) => `
        <article class="portal-tile">
          <span class="portal-icon">${esc(portal.icon)}</span>
          <div><h2>${esc(portal.name)}</h2><p>${esc(portal.description)}</p></div>
          <div class="inline-actions"><span class="status available">${esc(portal.badge)}</span><span class="status">${esc(portal.status)}</span></div>
          <button class="primary-btn" data-action="open-portal" data-portal-id="${portal.id}">Open</button>
        </article>
      `).join("")}
    </section>
    ${query ? `<section class="command-section"><h2>Search Results</h2>${localResults.map((result) => `<button class="activity-row" data-action="open-portal" data-portal-id="${result.portalId}"><strong>${esc(result.title)}</strong><span>${esc(result.type)} - ${esc(result.subtitle)}</span></button>`).join("") || `<div class="empty-state">No local matches.</div>`}</section>` : ""}
    <section class="command-section">
      <div class="section-heading"><span>Global Activity</span><span>${admin.activity?.length || 0}</span></div>
      <div class="activity-feed">${(admin.activity || []).slice(0, 16).map((event) => `<div class="audit-row"><code>${esc(event.action)}</code><span>${formatDateTime(event.createdAt)} - ${esc(userById(event.actorId).displayName)} - ${esc(event.targetType)}:${esc(event.targetId)}</span></div>`).join("") || `<div class="empty-state">No audit activity yet.</div>`}</div>
    </section>
  `;
}

function renderPortalApp(portalId) {
  if (portalId === "intranet-admin") return renderIntranetAdminPortal();
  if (portalId === "shuffle") return renderShufflePortal();
  if (portalId === "hr") return renderHrPortal();
  return `<div class="empty-state">Portal is not available.</div>`;
}

function renderIntranetAdminPortal() {
  const intranet = adminData().intranet || {};
  const users = intranet.users || data.users;
  const channels = intranet.channels || data.channels;
  const storage = intranet.storage || {};
  const recentMessages = [...data.messages].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt)).slice(0, 12);
  return `
    <div class="portal-app">
      <header class="portal-header"><div><h2>Intranet Admin</h2><p>Control users, channels, moderation, retention, storage, feature flags, and audit.</p></div><button class="ghost-btn" data-action="switch-chat">Open Chat</button></header>
      <section class="metric-grid">
        <div><strong>${users.filter((user) => user.isActive).length}</strong><span>active users</span></div>
        <div><strong>${channels.length}</strong><span>channels</span></div>
        <div><strong>${formatBytes(storage.totalBytes || 0)}</strong><span>stored files</span></div>
        <div><strong>${data.audit.length}</strong><span>visible audit events</span></div>
      </section>
      <section class="portal-columns">
        <div class="portal-panel"><h3>Users & RBAC</h3>${users.map((user) => `
          <div class="admin-row">
            <div><strong>${esc(user.displayName)}</strong><span>${esc(user.email)} - ${esc(user.provider)} - ${user.isActive ? "active" : "suspended"}</span></div>
            <select data-action="role-change" data-user-id="${user.id}">${["super_admin", "admin", "moderator", "it_admin", "security_analyst", "hr_admin", "hr_viewer", "member", "guest"].map((role) => `<option value="${role}" ${user.role === role ? "selected" : ""}>${role}</option>`).join("")}</select>
            <button class="ghost-btn small-btn" data-action="toggle-user-active" data-user-id="${user.id}" data-active="${user.isActive ? "false" : "true"}">${user.isActive ? "Suspend" : "Enable"}</button>
            ${currentUser()?.role === "super_admin" && user.id !== currentUser()?.id ? `
              <button class="ghost-btn small-btn danger-btn" data-action="remove-workspace-member" data-user-id="${user.id}" data-user-name="${esc(user.displayName)}" title="Permanently delete user">Delete</button>
            ` : ""}
          </div>`).join("")}</div>
        <div class="portal-panel"><h3>Channels</h3>${channels.map((channel) => `
          <div class="admin-row"><div><strong>${esc(channelName(channel))}</strong><span>${channel.members?.length || 0} members - ${channel.locked ? "locked" : "open"} - retention ${channel.retentionDays || data.settings.retentionGlobalDays || 365}d</span></div><button class="ghost-btn small-btn" data-action="lock-channel-id" data-channel-id="${channel.id}">${channel.locked ? "Unlock" : "Lock"}</button></div>
        `).join("")}</div>
      </section>
      <section class="portal-columns">
        <form class="portal-panel" id="retentionForm"><h3>Retention & Legal Hold</h3><label class="field"><span>Global days</span><input name="globalDays" type="number" min="1" value="${data.settings.retentionGlobalDays || 365}" /></label><label class="field"><span>Active channel legal hold</span><select name="legalHold"><option value="false" ${!activeChannel().legalHold ? "selected" : ""}>off</option><option value="true" ${activeChannel().legalHold ? "selected" : ""}>on</option></select></label><button class="primary-btn" type="submit">Save retention</button></form>
        <form class="portal-panel" id="announcementForm"><h3>Announcement</h3><label class="field"><span>Title</span><input name="title" required /></label><label class="field"><span>Body</span><textarea name="body" required></textarea></label><button class="primary-btn" type="submit">Post announcement</button></form>
      </section>
      <section class="portal-columns">
        <div class="portal-panel"><h3>Feature Flags</h3>${Object.entries(data.featureFlags || {}).map(([key, value]) => `<label class="admin-row"><span><strong>${esc(key)}</strong><span>${value ? "enabled" : "disabled"}</span></span><input type="checkbox" data-action="flag-change" data-flag="${esc(key)}" ${value ? "checked" : ""} /></label>`).join("")}</div>
        <div class="portal-panel"><h3>Moderation</h3>${recentMessages.map((message) => `<div class="admin-row"><div><strong>${esc(userById(message.authorId).displayName)}</strong><span>${esc(message.body).slice(0, 90)} ${message.deletedAt ? "- deleted" : ""}</span></div>${!message.deletedAt ? `<button class="danger-btn small-btn" data-action="delete-message" data-message-id="${message.id}">Delete</button>` : ""}</div>`).join("")}</div>
      </section>
      <section class="portal-columns">
        <div class="portal-panel">
          <div class="section-heading">
            <span>X-Style Tag Alerts & Notifications</span>
            <span class="status available">${(data.notifications || []).filter((n) => n.type === "tag").length} tag alerts</span>
          </div>
          <p class="muted">Live stream of hashtags used across all channels (#oro, etc.) alerting Super Admin.</p>
          <div class="x-admin-notifs-list">
            ${(data.notifications || []).slice(0, 6).map(renderXNotificationCard).join("") || `<p class="muted">No notifications recorded yet.</p>`}
          </div>
        </div>
        <div class="portal-panel">
          <div class="section-heading">
            <span>Inactivity & 1-Hour Pending Review</span>
            <span class="status ${getStaleItems(60).length ? "pending" : "available"}">${getStaleItems(60).length} stale items</span>
          </div>
          <p class="muted">Super Admin is pinged with visual alerts and audio chimes if review items (leave, remote assist, unread notifications) are pending &gt; 1 hour.</p>
          <div class="inline-actions" style="margin-top:14px;gap:8px;flex-wrap:wrap;">
            <button class="primary-btn small-btn" data-action="simulate-stale-ping">${state.simulateStalePing ? "Stop Stale Ping Simulation" : "Simulate 1h Ping Alert (Test)"}</button>
            <button class="ghost-btn small-btn" data-action="test-notification-chime">Test Audio Chime</button>
            <button class="ghost-btn small-btn" data-action="open-panel" data-panel="notifications">Open Alerts Drawer</button>
          </div>
        </div>
      </section>
      <section class="portal-panel"><h3>Audit Export</h3><div class="inline-actions"><span class="status">${data.audit.length} loaded</span><button class="ghost-btn" data-action="export-audit">Export audit</button><button class="ghost-btn" data-action="export-channel">Export active channel</button></div></section>
    </div>
  `;
}

function renderShufflePortal() {
  const shuffle = adminData().shuffle || {};
  const configured = Boolean(shuffle.configured);
  return `
    <div class="portal-app">
      <header class="portal-header"><div><h2>Shuffle SOAR Automation</h2><p>${configured ? "Connected to Shuffle API." : "Local workflow catalog. Configure SHUFFLE_* env vars for live execution."}</p></div>${configured ? `<button class="ghost-btn" data-action="open-external" data-url="${esc(shuffle.editorUrl || "")}">Open Editor</button>` : `<button class="ghost-btn" data-action="copy-integration-env" data-template="SHUFFLE_API_URL&#10;SHUFFLE_EDITOR_URL&#10;SHUFFLE_API_KEY">Copy setup env</button>`}</header>
      ${!configured ? `<section class="integration-setup"><div><h3>Connect live Shuffle</h3><p class="secondary">The portal will not open shuffle.beenco.local until the real editor/API endpoint is configured or DNS points that host to a running Shuffle instance.</p></div><div class="env-grid"><code>SHUFFLE_API_URL</code><code>SHUFFLE_EDITOR_URL</code><code>SHUFFLE_API_KEY</code></div></section>` : ""}
      <section class="portal-columns">
        <div class="portal-panel"><h3>Workflows</h3>${(shuffle.workflows || []).map((workflow) => `<div class="admin-row"><div><strong>${esc(workflow.name)}</strong><span>${workflow.enabled ? "enabled" : "disabled"} - ${esc(workflow.status)} - success ${workflow.successRate}%</span></div><button class="primary-btn small-btn" data-action="shuffle-run-workflow" data-workflow-id="${esc(workflow.id)}">Run now</button></div>`).join("")}</div>
        <div class="portal-panel"><h3>Executions</h3>${(shuffle.executions || []).map((execution) => `<div class="admin-row"><div><strong>${esc(execution.id)}</strong><span>${esc(execution.status)} - ${execution.durationMs}ms - ${esc(execution.triggeredBy)}</span></div><span class="status available">${formatRelative(execution.createdAt)}</span></div>`).join("")}<h3>Connectors</h3>${(shuffle.apps || []).map((app) => `<div class="admin-row"><div><strong>${esc(app.name)}</strong><span>${esc(app.health)}</span></div></div>`).join("")}</div>
      </section>
    </div>
  `;
}

function renderHrPortal() {
  const hr = adminData().hr || {};
  const employees = hr.employees || [];
  const assets = hr.assets || [];
  const assignments = hr.assignments || [];
  const leave = hr.leaveRequests || [];
  return `
    <div class="portal-app">
      <header class="portal-header"><div><h2>HR, Attendance & Assets</h2><p>People records, assigned equipment, attendance corrections, leave, onboarding, and reports.</p></div><span class="status available">${hr.summary?.headcount || 0} active employees</span></header>
      <section class="metric-grid"><div><strong>${hr.summary?.headcount || 0}</strong><span>headcount</span></div><div><strong>${hr.summary?.assetsAssigned || 0}</strong><span>assigned assets</span></div><div><strong>${hr.summary?.pendingLeave || 0}</strong><span>pending leave</span></div><div><strong>${hr.summary?.openTasks || 0}</strong><span>open tasks</span></div></section>
      <section class="portal-columns">
        <div class="portal-panel"><h3>Employees</h3>${employees.map((employee) => `<div class="people-row"><div><strong>${esc(employee.fullName)}</strong><span>${esc(employee.designation)} - ${esc(employee.department)} - joined ${esc(employee.joiningDate)}</span></div><span class="status available">${esc(employee.status)}</span></div>`).join("") || `<div class="empty-state">No employees yet.</div>`}</div>
        <form class="portal-panel" id="employeeForm"><h3>Add Employee</h3><label class="field"><span>Full name</span><input name="fullName" required /></label><label class="field"><span>Email</span><input name="email" type="email" /></label><label class="field"><span>Department</span><input name="department" /></label><label class="field"><span>Designation</span><input name="designation" /></label><label class="field"><span>Joining date</span><input name="joiningDate" type="date" /></label><button class="primary-btn" type="submit" ${!canWriteHr() ? "disabled" : ""}>Create employee</button></form>
      </section>
      <section class="portal-columns">
        <div class="portal-panel"><h3>Assets</h3>${assets.map((asset) => `<div class="admin-row"><div><strong>${esc(asset.assetTag)}</strong><span>${esc(asset.category)} - ${esc(asset.model)} - ${esc(asset.status)}</span></div></div>`).join("") || `<div class="empty-state">No assets yet.</div>`}</div>
        <form class="portal-panel" id="assetForm"><h3>Add Asset</h3><label class="field"><span>Asset tag</span><input name="assetTag" required /></label><label class="field"><span>Category</span><input name="category" required placeholder="Laptop, phone, monitor" /></label><label class="field"><span>Model</span><input name="model" /></label><label class="field"><span>Serial</span><input name="serial" /></label><button class="primary-btn" type="submit" ${!canWriteHr() ? "disabled" : ""}>Create asset</button></form>
      </section>
      <section class="portal-columns">
        <form class="portal-panel" id="assignAssetForm"><h3>Assign Asset</h3><label class="field"><span>Asset</span><select name="assetId">${assets.map((asset) => `<option value="${asset.id}">${esc(asset.assetTag)} - ${esc(asset.status)}</option>`).join("")}</select></label><label class="field"><span>Employee</span><select name="employeeId">${employees.map((employee) => `<option value="${employee.id}">${esc(employee.fullName)}</option>`).join("")}</select></label><label class="field"><span>Expected return</span><input name="expectedReturn" type="date" /></label><button class="primary-btn" type="submit" ${!canWriteHr() ? "disabled" : ""}>Assign</button></form>
        <div class="portal-panel"><h3>Assignment History</h3>${assignments.slice(0, 10).map((assignment) => `<div class="admin-row"><div><strong>${esc(assets.find((asset) => asset.id === assignment.assetId)?.assetTag || assignment.assetId)}</strong><span>${esc(employees.find((employee) => employee.id === assignment.employeeId)?.fullName || assignment.employeeId)} - ${esc(assignment.assignedAt)}</span></div></div>`).join("") || `<p class="muted">No assignments yet.</p>`}</div>
      </section>
      <section class="portal-columns">
        <form class="portal-panel" id="leaveRequestForm"><h3>Leave Request</h3><label class="field"><span>Employee</span><select name="employeeId">${employees.map((employee) => `<option value="${employee.id}">${esc(employee.fullName)}</option>`).join("")}</select></label><label class="field"><span>Type</span><input name="leaveType" value="annual" /></label><label class="field"><span>Start</span><input name="startDate" type="date" required /></label><label class="field"><span>End</span><input name="endDate" type="date" required /></label><label class="field"><span>Days</span><input name="days" type="number" min="0.5" step="0.5" value="1" /></label><button class="primary-btn" type="submit">Request leave</button></form>
        <div class="portal-panel"><h3>Leave Queue</h3>${leave.map((request) => `<div class="admin-row"><div><strong>${esc(employees.find((employee) => employee.id === request.employeeId)?.fullName || request.employeeId)}</strong><span>${esc(request.leaveType)} - ${esc(request.startDate)} to ${esc(request.endDate)} - ${esc(request.status)}</span></div>${request.status === "pending" && canWriteHr() ? `<div class="inline-actions"><button class="ghost-btn small-btn" data-action="decide-leave" data-leave-id="${request.id}" data-status="approved">Approve</button><button class="danger-btn small-btn" data-action="decide-leave" data-leave-id="${request.id}" data-status="rejected">Reject</button></div>` : ""}</div>`).join("") || `<p class="muted">No leave requests.</p>`}</div>
      </section>
      <section class="portal-columns">
        <form class="portal-panel" id="attendanceCorrectionForm"><h3>Attendance Correction</h3><label class="field"><span>Employee</span><select name="employeeId">${employees.map((employee) => `<option value="${employee.id}">${esc(employee.fullName)}</option>`).join("")}</select></label><label class="field"><span>Date</span><input name="date" type="date" required /></label><label class="field"><span>Status</span><select name="status"><option>present</option><option>late</option><option>absent</option><option>on-leave</option></select></label><label class="field"><span>Reason</span><input name="reason" required /></label><button class="primary-btn" type="submit" ${!canWriteHr() ? "disabled" : ""}>Save correction</button></form>
        <div class="portal-panel"><h3>Attendance</h3>${(hr.attendance || []).slice(0, 12).map((day) => `<div class="admin-row"><div><strong>${esc(employees.find((employee) => employee.id === day.employeeId)?.fullName || day.employeeId)}</strong><span>${esc(day.date)} - ${esc(day.status)} - ${day.workedMinutes || 0} min</span></div></div>`).join("") || `<p class="muted">No attendance rows yet.</p>`}</div>
      </section>
    </div>
  `;
}

function chatIcon(name) {
  const paths = {
    home: '<path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 21v-8a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v8"/>',
    dm: '<path d="M20 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 18 0Z"/>',
    activity: '<path d="M6 9a6 6 0 0 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z"/><path d="M10 21h4"/>',
    files: '<rect x="7" y="7" width="14" height="14" rx="3"/><path d="M16 7V3H3v13h4"/>',
    people: '<circle cx="9" cy="7" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3M17 4a4 4 0 0 1 0 8m2 3a5 5 0 0 1 3 5"/>',
    search: '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/>',
    thread: '<path d="M21 11a8 8 0 0 1-8 8H5l-3 3V4h19Z"/><path d="M6 8h11M6 12h8"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
    send: '<path d="m3 3 19 9-19 9 4-9-4-9Zm4 9h15"/>',
    edit: '<path d="m15 4 5 5M4 20l5-1L21 7l-5-5L4 14v6Z"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    sparkles: '<path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/><path d="M19 3v4M21 5h-4M5 19v2M6 20H4"/>',
    terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>'
  };
  return '<svg class="chat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.dm) + '</svg>';
}

function renderShell() {
  const channel = activeChannel();
  const mode = state.workspaceMode || "home";
  const fullView = ["activity", "files", "threads", "calendar", "reports", "briefing", "xmentions"].includes(mode);
  const shellClasses = ["app", "shell", "slack-shell"];
  if (state.ui.sidebarCollapsed) shellClasses.push("sidebar-closed");
  if (!state.ui.detailsOpen) shellClasses.push("details-closed");
  if (state.ui.detailsOpen && state.activePanel === "search") shellClasses.push("search-open");
  const modes = [
    ["home", "Home", "home"],
    ["dms", "DMs", "dm"],
    ["calendar", "Calendar", "calendar"],
    ["reports", "AI Reports", "sparkles"],
    ["briefing", "Radar", "terminal"],
    ["activity", "Activity", "activity"],
    ["files", "Files", "files"]
  ];
  if (currentUser()?.role === "super_admin") {
    modes.splice(6, 0, ["xmentions", "X Trends", "activity"]);
  }
  if (!isReportAdmin()) {
    modes.splice(modes.findIndex(([id]) => id === "reports"), 1);
  }
  return `
    <div class="${shellClasses.join(" ")}">
      ${renderStalePingBanner()}
      <header class="workspace-bar">
        <span class="workspace-caption">Beenco Connect</span>
        ${renderUrgentTopbarButton()}
        <button class="workspace-search" data-action="open-panel" data-panel="search">${chatIcon("search")}<span>Search Beenco</span><kbd>Ctrl K</kbd></button>
        ${!showDesktopAlertsPrompt() ? "" : `
          <button class="ghost-btn small-btn" data-action="test-desktop-notification" style="margin-left:auto;white-space:nowrap;" title="${canUseDesktopNotifications() ? (Notification.permission === "denied" ? "Notifications are blocked for this site in browser settings" : "Allow desktop notifications") : "Needs HTTPS or localhost"}">
            🔔 ${canUseDesktopNotifications() && Notification.permission === "denied" ? "Desktop alerts blocked" : "Enable desktop alerts"}
          </button>
        `}
        <button class="icon-btn notif-top-btn" data-action="open-panel" data-panel="notifications" aria-label="Notifications" title="X Notifications" style="position:relative;margin-left:${showDesktopAlertsPrompt() ? "8px" : "auto"};margin-right:8px;">
          ${chatIcon("activity")}
          ${unreadNotificationsCount() > 0 ? `<span class="badge warn">${unreadNotificationsCount()}</span>` : ""}
        </button>
        <button class="top-profile" data-action="open-panel" data-panel="you" aria-label="Your profile">${initials(currentUser().displayName)}</button>
      </header>
      <nav class="workspace-rail" aria-label="Main navigation">
        <button class="rail-brand" data-action="workspace-view" data-view="home" aria-label="Beenco home">B</button>
        ${modes.map(([id, label, icon]) => `<button class="rail-item ${mode === id ? "is-active" : ""}" data-action="workspace-view" data-view="${id}" aria-label="${label}"><span>${chatIcon(icon)}</span><small>${label}</small></button>`).join("")}
        <div class="rail-bottom"><button class="rail-item" data-action="open-panel" data-panel="group" aria-label="Create a group"><span>${chatIcon("plus")}</span></button><button class="top-profile" data-action="open-panel" data-panel="you" aria-label="Profile and status">${initials(currentUser().displayName)}</button></div>
      </nav>
      ${renderSidebar()}
      <main class="main ${fullView ? "collection-main" : ""}">
        <header class="main-header">
          <button class="icon-btn mobile-menu-btn" data-action="toggle-sidebar" aria-label="Channels">☰</button>
          <div class="channel-title"><h2>${esc(fullView ? ({activity: "Activity", files: "All files", threads: "Threads", calendar: "Personal Monthly Calendar", reports: "AI Weekly Reports", briefing: "Blockchain Intelligence Radar", xmentions: "X Mentions & Trends"}[mode]) : channelName(channel))}</h2>
          <p>${esc(fullView ? ({
            activity: "Your workspace, all in one place",
            files: "All files",
            threads: "Threads",
            calendar: "Personal schedule with 3-4 day daily advance alerts for client meetings",
            reports: "Automated executive synthesis of 1-on-1 chats and group channels",
            briefing: "Live multi-source crypto intelligence · Automated daily generation @ 09:00 AM",
            xmentions: "Mentions of monitored company and ecosystem accounts"
          }[mode] || "Your workspace, all in one place") : channel.topic || channel.description || "Start a conversation")}</p></div>
          <div class="header-actions">
            ${canDeleteChannel(channel) ? `
              <button class="icon-btn danger-icon-btn" data-action="delete-channel" data-channel-id="${esc(channel.id)}" title="Delete #${esc(channel.name)}" aria-label="Delete channel">🗑️</button>
            ` : ""}
            ${channel.type === "private" && isChannelOwner(channel) ? `
              <button class="ghost-btn small-btn" data-action="open-panel" data-panel="people" title="Manage group members">+ Add Member</button>
            ` : ""}
            <button class="icon-btn" data-action="open-panel" data-panel="people" aria-label="Conversation members">${chatIcon("people")}</button>
            <button class="icon-btn" data-action="open-panel" data-panel="search" aria-label="Search conversations">${chatIcon("search")}</button>
            <button class="icon-btn" data-action="open-panel" data-panel="you" aria-label="Your profile">⋯</button>
          </div>
        </header>
        <nav class="conversation-tabs" aria-label="Conversation tabs">
          <button class="is-active" data-action="workspace-view" data-view="${fullView ? mode : "home"}">${chatIcon(fullView ? mode === "files" ? "files" : mode === "calendar" ? "calendar" : mode === "reports" ? "sparkles" : mode === "briefing" ? "terminal" : "activity" : "dm")}${fullView ? "All" : "Messages"}</button>
          ${!fullView ? `<button data-action="open-panel" data-panel="files">${chatIcon("files")}Files</button><button data-action="open-panel" data-panel="people">${chatIcon("people")}Members</button>` : ""}
        </nav>
        <section class="message-scroll ${fullView ? "collection-content" : ""}" id="messageScroll" aria-label="${fullView ? "Workspace content" : "Messages"}">${mode === "activity" ? renderActivityPanel() : mode === "files" ? renderFilesPanel() : mode === "threads" ? renderThreadsView() : mode === "calendar" ? renderMonthlyCalendarView() : mode === "reports" ? renderAiReportsView() : mode === "briefing" ? renderBriefingView() : mode === "xmentions" ? renderXMentionsView() : renderMessages()}</section>
        ${fullView ? "" : renderComposer()}
      </main>
      ${renderDetails()}
      ${renderMobileTabs()}
      ${renderEventModal()}
      ${renderAddUserModal()}
      ${renderAttachmentPreviewModal()}
      <input class="hidden" id="fileInput" type="file" multiple />
      <div class="toast-region" id="toasts"></div>
    </div>`;
}

function renderNavSection(sectionId, label, addPanelId, addLabel, bodyHtml) {
  const collapsed = Boolean(state.ui.collapsedNavSections[sectionId]);
  return `
    <section class="sidebar-section">
      <h2 class="section-heading">
        <button type="button" class="section-heading-label" data-action="toggle-nav-section" data-section="${sectionId}" aria-expanded="${!collapsed}">
          <span class="workspace-chevron ${collapsed ? "is-collapsed" : ""}">${chatIcon("chevronDown")}</span>${esc(label)}
        </button>
        <button data-action="open-panel" data-panel="${addPanelId}" aria-label="${esc(addLabel)}">+</button>
      </h2>
      ${collapsed ? "" : bodyHtml}
    </section>
  `;
}

function renderSidebar() {
  const mode = state.workspaceMode || "home";
  const filter = state.sidebarFilter.trim().toLowerCase();
  const visible = data.channels.filter(channel => !filter || channelName(channel).toLowerCase().includes(filter));
  const channels = visible.filter(channel => ["public", "private"].includes(channel.type));
  const dms = visible.filter(channel => ["dm", "group_dm"].includes(channel.type));
  return `
    <aside class="sidebar ${state.ui.sidebarOpen ? "is-open" : ""}" aria-label="Workspace navigation">
      <div class="sidebar-top"><div class="workspace-title"><h1><span class="workspace-chevron">${chatIcon("chevronDown")}</span>${mode === "dms" ? "Direct messages" : "Beenco"}</h1></div><button class="icon-btn" data-action="open-panel" data-panel="people" aria-label="New message">${chatIcon("edit")}</button></div>
      <div class="sidebar-scroll">
        <div class="quick-search"><input data-action="filter-sidebar" value="${esc(state.sidebarFilter)}" placeholder="${mode === "dms" ? "Find a DM…" : "Find a conversation…"}" aria-label="Find a conversation" /></div>
        <nav class="sidebar-shortcuts" aria-label="Chat shortcuts">
          <button data-action="workspace-view" data-view="calendar">${chatIcon("calendar")}Personal Calendar</button>
          ${isReportAdmin() ? `<button data-action="workspace-view" data-view="reports">${chatIcon("sparkles")}AI Weekly Reports</button>` : ""}
          <button data-action="workspace-view" data-view="briefing">${chatIcon("terminal")}⚡ Blockchain Radar</button>
          ${currentUser()?.role === "super_admin" ? `<button data-action="workspace-view" data-view="xmentions">${chatIcon("activity")}X Mentions & Trends</button>` : ""}
          <button data-action="workspace-view" data-view="threads">${chatIcon("thread")}Threads</button>
          <button data-action="open-panel" data-panel="people">${chatIcon("people")}Directories</button>
          <button data-action="workspace-view" data-view="activity">${chatIcon("activity")}Mentions & activity</button>
        </nav>
        ${mode !== "dms" ? renderNavSection("channels", "Channels", "group", "Create a group", `<div class="nav-list">${channels.map(renderChannelButton).join("") || '<p class="side-note">No matching channels</p>'}</div>`) : ""}
        ${renderNavSection("dms", "Direct messages", "people", "Start a direct message", `<div class="nav-list ${mode === "dms" ? "dm-inbox" : ""}">${dms.map(channel => mode === "dms" ? renderDmPreview(channel) : renderChannelButton(channel)).join("") || '<p class="side-note">Start a conversation from Directories.</p>'}</div>`)}
        <button class="sidebar-add" data-action="open-panel" data-panel="people">${chatIcon("plus")}Add a conversation</button>
      </div>
      <button class="profile-strip" data-action="open-panel" data-panel="you"><span class="avatar">${initials(currentUser().displayName)}</span><span class="user-meta"><strong>${esc(currentUser().displayName)}</strong><span>${esc(currentUser().statusText || "Set a status")}</span></span><span class="presence-dot ${esc(currentUser().presence)}"></span></button>
    </aside>`;
}

function renderDmPreview(channel) {
  const latest = data.messages.filter(m => m.channelId === channel.id && !m.parentId && !m.deletedAt).sort((a,b) => new Date(b.createdAt) - new Date(a.createdAt))[0];
  return `<button class="dm-preview ${channel.id === state.activeChannelId ? "is-active" : ""}" data-action="select-channel" data-channel-id="${channel.id}"><span class="avatar">${initials(channelName(channel).replace("@", ""))}</span><span><strong>${esc(channelName(channel).replace("@", ""))}</strong><small>${esc(latest?.body || "Start a conversation")}</small></span></button>`;
}

function renderThreadsView() {
  const parents = data.messages.filter(message => !message.parentId && !message.deletedAt && data.messages.some(reply => reply.parentId === message.id && !reply.deletedAt));
  return parents.length ? parents.map(message => `<section class="thread-summary"><h3>${esc(channelName(data.channels.find(c => c.id === message.channelId)))}</h3>${renderMessage(message)}</section>`).join("") : '<div class="empty-state">Your threads will appear here. Reply to a message to start a thread.</div>';
}

function renderChannelButton(channel) {
  const isCurrentlyActive = channel.id === state.activeChannelId;
  const active = isCurrentlyActive ? "is-active" : "";
  const prefix = channel.type === "private" ? chatIcon("lock") : channel.type === "dm" ? '<span class="avatar tiny">' + initials(channelName(channel).replace("@", "")) + '</span>' : '<span class="channel-hash" aria-hidden="true">#</span>';
  const hasUnread = !isCurrentlyActive && (channel.unread || channel.mentions);
  const owned = channel.type === "private" && (channel.ownerIds || []).includes(currentUser()?.id);
  const showMentions = !isCurrentlyActive && channel.mentions;
  const showUnread = !isCurrentlyActive && channel.unread;
  const badge = showMentions ? `<span class="badge warn">${channel.mentions}</span>` : showUnread ? `<span class="badge">${channel.unread}</span>` : channel.onlineMemberCount ? `<span class="mini-meta">${channel.onlineMemberCount} on</span>` : "";
  return `
    <button class="nav-item ${active} ${hasUnread ? "has-unread" : ""}" data-action="select-channel" data-channel-id="${channel.id}">
      <span>${prefix}</span>
      <span class="nav-label">${esc(channel.type === "dm" ? channelName(channel).replace("@", "") : channel.name)}</span>
      ${owned ? `<span class="mini-meta">owner</span>` : ""}
      ${channel.locked ? `<span class="status pending">locked</span>` : badge}
    </button>
  `;
}

function renderAnnouncement() {
  const announcement = data.announcements
    .filter((item) => !item.pinnedUntil || new Date(item.pinnedUntil) > new Date())
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))[0];
  if (!announcement) return "";
  return `
    <section class="announcement-banner" aria-label="Announcement">
      <div><strong>${esc(announcement.title)}</strong><span>${esc(announcement.body)}</span></div>
      <button class="ghost-btn small-btn" data-action="open-panel" data-panel="calendar">Events</button>
    </section>
  `;
}

function renderMessages() {
  const channel = activeChannel();
  const messages = data.messages
    .filter((message) => message.channelId === channel.id && !message.parentId && !message.deletedAt)
    .sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
  const typingUsers = (channel.typingUserIds || []).map(userById).filter((user) => user.id !== currentUser()?.id);
  const typingLine = typingUsers.length ? `<div class="typing-line">${typingUsers.map((user) => esc(user.displayName)).join(", ")} ${typingUsers.length === 1 ? "is" : "are"} typing<span></span><span></span><span></span></div>` : "";
  if (!messages.length) {
    return `<div class="empty-state">No messages yet. Start the conversation in ${esc(channelName(channel))}.</div>${typingLine}`;
  }
  let previousDay = "";
  return messages.map(message => {
    const date = new Date(message.createdAt);
    const day = date.toLocaleDateString();
    const divider = day !== previousDay ? `<div class="day-divider"><span>${esc(date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }))}</span></div>` : "";
    previousDay = day;
    return divider + renderMessage(message);
  }).join("") + typingLine;
}

function renderMessage(message, compact = false) {
  const author = userById(message.authorId);
  const replies = data.messages.filter((item) => item.parentId === message.id && !item.deletedAt);
  const deleted = Boolean(message.deletedAt);
  const isUrgent = Boolean(message.isUrgent);
  const ackUser = message.acknowledgedBy ? userById(message.acknowledgedBy) : null;
  return `
    <article class="message-row ${deleted ? "is-deleted" : ""} ${isUrgent ? "is-urgent-row" : ""}" data-message-id="${message.id}">
      <span class="avatar small">${initials(author.displayName)}</span>
      <div>
        <div class="message-head">
          <strong>${esc(author.displayName)}</strong>
          <time>${formatTime(message.createdAt)}</time>
          ${message.editedAt ? "<span>edited</span>" : ""}
        </div>
        ${isUrgent ? `
          <div class="urgent-message-card-banner ${message.acknowledgedAt ? "is-acknowledged" : "is-pending"}">
            <div class="urgent-card-left">
              <span class="urgent-badge-pill">🚨 IMPORTANT TO READ</span>
              ${message.acknowledgedAt ? `
                <span class="ack-confirmed-text">✓ Acknowledged by <strong>${esc(ackUser?.displayName || "Admin")}</strong> at ${formatTime(message.acknowledgedAt)}</span>
              ` : `
                <span class="ack-waiting-text">⚠️ High priority &mdash; requires Super Admin acknowledgment</span>
              `}
            </div>
            ${(!message.acknowledgedAt && isAdmin()) ? `
              <button class="btn-press-ack" data-action="acknowledge-message" data-message-id="${message.id}">
                <span class="ack-check-icon">✓</span> PRESS TO ACKNOWLEDGE
              </button>
            ` : ""}
          </div>
        ` : ""}
        ${state.editingMessageId === message.id ? renderInlineEditor(message) : `
          <div class="message-body">${deleted ? "<p>Message deleted.</p>" : renderMarkdown(message.body)}</div>
          ${deleted ? "" : renderAttachments(message.attachments)}
          ${deleted ? "" : renderReactions(message)}
          ${!compact && replies.length ? `<button class="reply-link" data-action="open-thread" data-message-id="${message.id}">${replies.length} ${replies.length === 1 ? "reply" : "replies"}</button>` : ""}
          ${compact ? "" : renderMessageActions(message, replies.length)}
        `}
      </div>
    </article>
  `;
}

function renderInlineEditor(message) {
  return `
    <div class="inline-editor">
      <textarea data-action="edit-draft" data-message-id="${message.id}" aria-label="Edit message">${esc(state.editDraft || message.body)}</textarea>
      <div class="inline-actions">
        <button class="primary-btn small-btn" data-action="save-edit" data-message-id="${message.id}">Save</button>
        <button class="ghost-btn small-btn" data-action="cancel-edit" data-message-id="${message.id}">Cancel</button>
      </div>
    </div>
  `;
}

function inlineMarkdown(text) {
  return esc(text)
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|\s)@([a-zA-Z][\w.-]*)/g, '$1<span class="status available">@$2</span>')
    .replace(/(^|\s)(#[a-zA-Z0-9_\-]+)/g, '$1<span class="chat-hashtag">$2</span>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, (match, lead, url) => `${lead}<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);
}

function renderMarkdown(raw) {
  if (!raw) return "";
  let text = String(raw).trim();

  // 1. Unwrap outer ```markdown or ```md or generic ``` if wrapping whole document
  if (text.startsWith("```markdown") || text.startsWith("```md")) {
    text = text.replace(/^```(?:markdown|md)\r?\n([\s\S]*?)\r?\n```$/g, "$1").trim();
  } else if (text.startsWith("```") && text.endsWith("```") && (text.includes("#") || text.includes("|") || text.includes("---"))) {
    text = text.replace(/^```[a-zA-Z0-9_-]*\r?\n([\s\S]*?)\r?\n```$/g, "$1").trim();
  }

  // 2. Extract authentic code blocks
  const codeBlocks = [];
  text = text.replace(/```([a-zA-Z0-9_-]*)\r?\n([\s\S]*?)\r?\n```/g, (_, lang, code) => {
    if (lang === "markdown" || lang === "md") {
      return code;
    }
    const token = `@@CODE${codeBlocks.length}@@`;
    codeBlocks.push(`<pre class="code-block"><code class="language-${esc(lang)}">${esc(code.trim())}</code></pre>`);
    return token;
  });

  // 3. Process markdown tables
  const lines = text.split(/\r?\n/);
  const processedLines = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    const pipeCount = (line.match(/\|/g) || []).length;
    if (pipeCount >= 2 && (line.startsWith("|") || line.includes("|"))) {
      let isTable = false;
      let hasSep = false;
      if (i + 1 < lines.length) {
        const nextLine = lines[i + 1].trim();
        if (/^\s*\|?\s*[-:]+[-| :]*\|?\s*$/.test(nextLine)) {
          isTable = true;
          hasSep = true;
        } else if ((nextLine.match(/\|/g) || []).length >= 2) {
          isTable = true;
        }
      }
      if (isTable) {
        const headerCells = line.split("|").map(c => c.trim()).filter((c, idx, arr) => {
          if ((idx === 0 || idx === arr.length - 1) && c === "") return false;
          return true;
        });
        i += hasSep ? 2 : 1;
        const bodyRows = [];
        while (i < lines.length) {
          const rowLine = lines[i].trim();
          const rPipes = (rowLine.match(/\|/g) || []).length;
          if (rPipes < 2 && !rowLine.startsWith("|")) break;
          if (/^\s*\|?\s*[-:]+[-| :]*\|?\s*$/.test(rowLine)) {
            i++;
            continue;
          }
          const cells = rowLine.split("|").map(c => c.trim()).filter((c, idx, arr) => {
            if ((idx === 0 || idx === arr.length - 1) && c === "") return false;
            return true;
          });
          if (cells.length > 0) {
            bodyRows.push(cells);
          }
          i++;
        }
        let tableHtml = `<div class="table-responsive"><table class="report-table"><thead><tr>`;
        headerCells.forEach(h => {
          tableHtml += `<th>${inlineMarkdown(h)}</th>`;
        });
        tableHtml += `</tr></thead><tbody>`;
        bodyRows.forEach(row => {
          tableHtml += `<tr>`;
          headerCells.forEach((_, colIdx) => {
            const cellVal = row[colIdx] !== undefined ? row[colIdx] : "";
            tableHtml += `<td>${inlineMarkdown(cellVal)}</td>`;
          });
          tableHtml += `</tr>`;
        });
        tableHtml += `</tbody></table></div>`;
        processedLines.push(tableHtml);
        continue;
      }
    }
    processedLines.push(lines[i]);
    i++;
  }

  // 4. Process line-by-line block elements (Headings, HR, List items, Blockquotes, Paragraphs)
  const blocks = [];
  let currentList = null;

  function flushList() {
    if (currentList) {
      const tag = currentList.type;
      blocks.push(`<${tag}>${currentList.items.map(it => `<li>${inlineMarkdown(it)}</li>`).join("")}</${tag}>`);
      currentList = null;
    }
  }

  for (let idx = 0; idx < processedLines.length; idx++) {
    const rawLine = processedLines[idx];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      flushList();
      continue;
    }

    // Pass through pre-rendered HTML (tables, code tokens)
    if (trimmed.startsWith("<div class=\"table-responsive\"") || trimmed.startsWith("@@CODE")) {
      flushList();
      blocks.push(trimmed);
      continue;
    }

    // Horizontal rules (---, ***, ___ )
    if (/^([-*_]\s*){3,}$/.test(trimmed)) {
      flushList();
      blocks.push('<hr class="report-divider" />');
      continue;
    }

    // Headings
    if (/^#### (.*$)/.test(trimmed)) {
      flushList();
      blocks.push(`<h5>${inlineMarkdown(trimmed.replace(/^#### /, ""))}</h5>`);
      continue;
    }
    if (/^### (.*$)/.test(trimmed)) {
      flushList();
      blocks.push(`<h4>${inlineMarkdown(trimmed.replace(/^### /, ""))}</h4>`);
      continue;
    }
    if (/^## (.*$)/.test(trimmed)) {
      flushList();
      blocks.push(`<h3>${inlineMarkdown(trimmed.replace(/^## /, ""))}</h3>`);
      continue;
    }
    if (/^# (.*$)/.test(trimmed)) {
      flushList();
      blocks.push(`<h2>${inlineMarkdown(trimmed.replace(/^# /, ""))}</h2>`);
      continue;
    }

    // Blockquotes
    if (trimmed.startsWith("> ")) {
      flushList();
      blocks.push(`<blockquote>${inlineMarkdown(trimmed.slice(2))}</blockquote>`);
      continue;
    }

    // Unordered List (- or *)
    const ulMatch = trimmed.match(/^[-*]\s+(.*$)/);
    if (ulMatch) {
      if (!currentList || currentList.type !== "ul") {
        flushList();
        currentList = { type: "ul", items: [] };
      }
      currentList.items.push(ulMatch[1]);
      continue;
    }

    // Ordered List (1. 2.)
    const olMatch = trimmed.match(/^\d+\.\s+(.*$)/);
    if (olMatch) {
      if (!currentList || currentList.type !== "ol") {
        flushList();
        currentList = { type: "ol", items: [] };
      }
      currentList.items.push(olMatch[1]);
      continue;
    }

    // Regular paragraph
    flushList();
    blocks.push(`<p>${inlineMarkdown(trimmed)}</p>`);
  }
  flushList();

  let html = blocks.join("\n");
  codeBlocks.forEach((b, i) => {
    html = html.replace(`@@CODE${i}@@`, b);
  });
  return html;
}

function renderAttachments(ids = []) {
  if (!ids.length) return "";
  return `<div class="file-grid">${ids.map((id) => renderAttachment(fileById(id))).join("")}</div>`;
}

function canDeleteFile(file) {
  if (!file || !currentUser()) return false;
  if (isAdmin()) return true;
  if (file.uploaderId === currentUser().id) return true;
  const channel = data.channels.find((c) => c.id === file.channelId);
  if (channel && isChannelOwner(channel)) return true;
  return false;
}

function renderAttachment(file) {
  if (!file) return "";
  const deletable = canDeleteFile(file);
  if (file.kind === "voice") {
    const waveform = (file.waveform || [])
      .slice(0, 40)
      .map((level) => `<span style="height:${Math.max(8, level)}px"></span>`)
      .join("");
    return `
      <div class="file-card">
        <span class="file-icon">AUD</span>
        <div class="file-meta">
          <strong>${esc(file.originalName)}</strong>
          <span>${file.duration || 1}s - voice message</span>
          <div class="voice-waveform" aria-hidden="true">${waveform}</div>
          ${file.previewUrl ? `<audio class="voice-player" controls preload="metadata" src="${esc(file.previewUrl)}"></audio>` : `<span class="status pending">audio unavailable</span>`}
        </div>
        <div class="file-actions">
          <span class="status ${file.status}">${esc(file.status)}</span>
          ${deletable ? `
            <button class="danger-btn small-btn" data-action="delete-file" data-file-id="${esc(file.id)}" title="Delete voice message">Delete</button>
          ` : ""}
        </div>
      </div>
    `;
  }
  const ext = file.originalName.includes(".") ? file.originalName.split(".").pop().slice(0, 4).toUpperCase() : "FIL";
  const isImage = String(file.mime || "").startsWith("image/") && file.previewUrl;
  return `
    <div class="file-card file-card-clickable" data-action="preview-attachment" data-file-id="${esc(file.id)}">
      ${isImage ? `<button class="image-thumb" data-action="preview-attachment" data-file-id="${esc(file.id)}" aria-label="Open image preview"><img src="${esc(file.previewUrl)}" alt="${esc(file.originalName)}" loading="lazy" /></button>` : `<span class="file-icon" data-action="preview-attachment" data-file-id="${esc(file.id)}">${esc(ext)}</span>`}
      <div class="file-meta">
        <strong data-action="preview-attachment" data-file-id="${esc(file.id)}" title="Click to view file in browser">${esc(file.originalName)}</strong>
        <span>${formatBytes(file.sizeBytes)} · ${esc(file.mime || "File")}</span>
      </div>
      <div class="file-actions">
        ${file.status === "available" ? `
          <button type="button" class="primary-btn small-btn" data-action="preview-attachment" data-file-id="${esc(file.id)}" title="View attachment in browser">View</button>
        ` : ""}
        <button type="button" class="ghost-btn small-btn" data-action="download-file" data-file-id="${file.id}" ${file.status !== "available" ? "disabled" : ""}>
          ${file.status === "available" ? "Download" : esc(file.status)}
        </button>
        ${deletable ? `
          <button type="button" class="danger-btn small-btn" data-action="delete-file" data-file-id="${esc(file.id)}" title="Delete file">Delete</button>
        ` : ""}
      </div>
    </div>
  `;
}

function renderReactions(message) {
  const reactions = Object.entries(message.reactions || {});
  if (!reactions.length) return "";
  return `<div class="reaction-row">${reactions.map(([emoji, users]) => `
    <button class="reaction ${users.includes(currentUser().id) ? "is-mine" : ""}" data-action="react" data-message-id="${message.id}" data-emoji="${esc(emoji)}">${esc(emoji)} ${users.length}</button>
  `).join("")}</div>`;
}

function renderMessageActions(message, replyCount) {
  const canChange = message.authorId === currentUser().id || isAdmin();
  return `
    <div class="message-actions">
      <button class="chip" data-action="react" data-message-id="${message.id}" data-emoji="👍" aria-label="React with thumbs up">👍</button>
      <button class="chip" data-action="react" data-message-id="${message.id}" data-emoji="🔥" aria-label="React with fire">🔥</button>
      <button class="chip" data-action="open-thread" data-message-id="${message.id}">Thread ${replyCount ? `(${replyCount})` : ""}</button>
      ${canChange && !message.deletedAt ? `<button class="chip" data-action="edit-message" data-message-id="${message.id}">Edit</button>` : ""}
      ${canChange && !message.deletedAt ? `<button class="chip" data-action="delete-message" data-message-id="${message.id}">Delete</button>` : ""}
    </div>
  `;
}

function renderPendingAttachments() {
  const channelId = state.activeChannelId;
  const pending = (state.pendingAttachments || []).filter((a) => a.channelId === channelId);
  if (!pending.length) return "";
  return `
    <div class="composer-pending-attachments">
      ${pending.map((item) => {
        const ext = item.name && item.name.includes(".") ? item.name.split(".").pop().slice(0, 4).toUpperCase() : "FIL";
        const isReady = item.status === "available";
        const isFailed = item.status === "failed";
        const isScanning = item.status === "scan pending";
        const statusLabel = isReady ? "✓ Ready" : (isFailed ? "Failed" : (isScanning ? "Scanning..." : `Loading ${item.progress}%`));
        const statusClass = isReady ? "ready" : (isFailed ? "failed" : "loading");
        return `
          <div class="pending-attachment-chip is-${statusClass}">
            <span class="pending-chip-icon">${esc(ext)}</span>
            <div class="pending-chip-info">
              <span class="pending-chip-name" title="${esc(item.name)}">${esc(item.name)}</span>
              <span class="pending-chip-meta">${formatBytes(item.size)} · <strong class="chip-status-${statusClass}">${statusLabel}</strong></span>
            </div>
            <button type="button" class="pending-chip-remove" data-action="remove-pending-attachment" data-temp-id="${esc(item.tempId)}" title="Remove attachment" aria-label="Remove attachment">✕</button>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderComposer() {
  const channel = activeChannel();
  if (channel.id && isViewOnlyDm(channel)) {
    return `
      <footer class="composer">
        <div class="composer-box"><p class="muted" style="margin:0;padding:10px 12px;">👁 Admin view: you can read this conversation, but only its participants can send messages.</p></div>
      </footer>`;
  }
  const disabled = !channel.id || (channel.locked && !isAdmin());
  const draft = state.composerDrafts[channel.id] || "";
  return `
    <footer class="composer">
      <div class="composer-box ${state.composerUrgent ? "is-urgent-composer" : ""}">
        ${renderPendingAttachments()}
        <textarea data-action="composer-draft" placeholder="${disabled ? "Channel is locked" : esc(state.composerUrgent ? `⚠️ IMPORTANT message to ${channelName(channel)} (will ping Super Admin)` : `Message ${channelName(channel)}`)}" ${disabled ? "disabled" : ""}>${esc(draft)}</textarea>
        <div class="composer-actions">
          <button class="icon-btn" data-action="attach-file" title="Attach files" aria-label="Attach files" ${disabled ? "disabled" : ""}>+</button>
          <button class="icon-btn" data-action="format-composer" data-format="bold" title="Bold" aria-label="Bold" ${disabled ? "disabled" : ""}><strong>B</strong></button>
          <button class="icon-btn" data-action="format-composer" data-format="italic" title="Italic" aria-label="Italic" ${disabled ? "disabled" : ""}><em>I</em></button>
          <button class="icon-btn" data-action="format-composer" data-format="code" title="Code" aria-label="Code" ${disabled ? "disabled" : ""}>&lt;/&gt;</button>
          <button class="icon-btn" data-action="format-composer" data-format="emoji" title="Insert smile" aria-label="Insert smile" ${disabled ? "disabled" : ""}>☺</button>
          <button class="icon-btn" data-action="toggle-voice" title="Voice message" aria-label="Voice message" ${disabled ? "disabled" : ""}>${state.voice.recording ? "Stop" : chatIcon("mic")}</button>
          <button class="urgent-toggle-btn ${state.composerUrgent ? "is-urgent-active" : ""}" data-action="toggle-urgent" title="Mark message as Important to Read (members ping every 30 minutes)" aria-label="Important to Read" ${disabled ? "disabled" : ""}>⚡ ${state.composerUrgent ? "Urgent / Important" : "Important"}</button>
          <button class="primary-btn" data-action="send-message" aria-label="Send message" ${disabled ? "disabled" : ""}>${chatIcon("send")}</button>
        </div>
      </div>
      <div class="composer-tools">
        ${state.composerUrgent ? `<span class="urgent-draft-pill">⚠️ Marked Important to Read &mdash; pings channel members every 30 minutes until acknowledged</span>` : `<span>Enter to send · Shift + Enter for a new line · Markdown supported</span>`}
        ${state.voice.recording ? `<span class="status pending">Recording ${state.voice.elapsed}s</span>` : ""}
      </div>
      ${state.voice.recording ? renderWaveform(state.voice.waveform) : ""}
      ${renderUploads()}
    </footer>
  `;
}

function renderWaveform(values) {
  return `<div class="upload-row"><div class="inline-actions">${values.slice(-34).map((level) => `<span class="progress waveform-bar"><span style="height:${Math.max(10, level)}px;width:100%"></span></span>`).join("")}</div></div>`;
}

function renderUploads() {
  const uploads = state.uploads.filter((upload) => upload.channelId === state.activeChannelId);
  if (!uploads.length) return "";
  return `<div class="upload-list">${uploads.map((upload) => `
    <div class="upload-row">
      <div class="admin-row">
        <div><strong>${esc(upload.name)}</strong><span>${formatBytes(upload.size)} - ${esc(upload.status)}</span></div>
        <button class="ghost-btn small-btn" data-action="${upload.paused ? "resume-upload" : "pause-upload"}" data-upload-id="${upload.id}">${upload.paused ? "Resume" : "Pause"}</button>
      </div>
      <div class="progress"><span style="width:${Math.min(100, upload.progress)}%"></span></div>
    </div>
  `).join("")}</div>`;
}

function renderDetails() {
  const unreadCount = unreadNotificationsCount();
  // "New group" isn't a detail view like the others (it opens a form, not something to look
  // at) and is already reachable from the sidebar's own + buttons, so it doesn't belong here —
  // it was crowding this row out to 7 tabs, pushing Search and You off the edge with the
  // scrollbar hidden and no indication there was more to scroll to.
  const tabs = [
    ["thread", "Thread"],
    ["notifications", `Alerts${unreadCount > 0 ? ` (${unreadCount})` : ""}`],
    ["files", "Files"],
    ["people", "People"],
    ["search", "Search"],
    ["you", "You"]
  ];
  const searchOpen = state.activePanel === "search";
  return `
    <aside class="details-panel ${state.ui.detailsOpen ? "is-open" : ""} ${searchOpen ? "is-search" : ""}" aria-label="${searchOpen ? "Search" : "Details"}">
      <header class="details-header">
        <div class="details-title"><h2>${esc(tabs.find(([id]) => id === state.activePanel)?.[1] || "Details")}</h2><p>${esc(channelName(activeChannel()))}</p></div>
        <button class="icon-btn" data-action="close-details" title="Close details" aria-label="Close details">✕</button>
      </header>
      ${searchOpen ? "" : `<nav class="details-tabs" aria-label="Detail views">${tabs.map(([id, label]) => `<button class="tab-btn ${state.activePanel === id ? "is-active" : ""}" data-action="open-panel" data-panel="${id}">${label}</button>`).join("")}</nav>`}
      <div class="details-body">${renderPanel()}</div>
    </aside>
  `;
}

function renderPanel() {
  if (state.activePanel === "thread") return renderThreadPanel();
  if (state.activePanel === "activity") return renderActivityPanel();
  if (state.activePanel === "search") return renderSearchPanel();
  if (state.activePanel === "files") return renderFilesPanel();
  if (state.activePanel === "tasks") return renderTasksPanel();
  if (state.activePanel === "notifications") return renderNotificationsPanel();
  if (state.activePanel === "group") return renderGroupPanel();
  if (state.activePanel === "remote") return renderRemoteAssistPanel();
  if (state.activePanel === "calendar") return renderCalendarPanel();
  if (state.activePanel === "people") return renderPeoplePanel();
  if (state.activePanel === "you") return renderYouPanel();
  if (state.activePanel === "admin") return renderAdminPanel();
  if (state.activePanel === "audit") return renderAuditPanel();
  return renderThreadPanel();
}

function notificationPayload(notification) {
  return jsonSafe(notification.payload, {});
}

function jsonSafe(value, fallback) {
  if (!value) return fallback;
  try {
    return typeof value === "string" ? JSON.parse(value) : value;
  } catch {
    return fallback;
  }
}

function renderXNotificationCard(notification) {
  const payload = notificationPayload(notification);
  const isRead = Boolean(notification.readAt || notification.read_at);
  const author = userById(payload.authorId) || { displayName: payload.authorName || "Someone", username: payload.authorUsername || "user" };
  const isTag = notification.type === "tag";
  const isUrgent = notification.type === "urgent" || payload.isUrgent;
  const tags = payload.tags || (payload.tag ? [payload.tag] : []);
  const channel = data.channels.find((c) => c.id === payload.channelId) || { name: payload.channelName || "channel" };
  const createdAt = notification.createdAt || notification.created_at;
  const isStale = isUrgent && !isRead && (Date.now() - new Date(createdAt).getTime()) >= 60 * 60 * 1000;

  return `
    <article class="x-notif-card ${!isRead ? "is-unread" : ""} ${isStale ? "is-stale" : ""} ${isUrgent ? "is-urgent-notif" : ""}" data-note-id="${esc(notification.id)}">
      <div class="x-notif-card-left">
        <span class="avatar small">${initials(author.displayName)}</span>
      </div>
      <div class="x-notif-card-body">
        <div class="x-notif-top">
          <div class="x-notif-author-wrap">
            <strong class="x-notif-name">${esc(author.displayName)}</strong>
            <span class="x-notif-handle">@${esc(author.username || "user")}</span>
            <span class="x-notif-dot">·</span>
            <time class="x-notif-time">${formatRelative(createdAt)}</time>
          </div>
          <div class="x-notif-actions">
            ${!isRead ? `<button class="icon-btn-micro" data-action="mark-notification-read" data-note-id="${esc(notification.id)}" title="Mark as read" aria-label="Mark read">✓</button>` : `<span class="x-read-check">✓</span>`}
          </div>
        </div>

        <div class="x-notif-headline">
          ${isUrgent ? `
            <span class="urgent-indicator-badge">⚠️ URGENT TO READ</span> <span class="x-action-desc">marked important in</span> <strong class="x-chan-link" data-action="select-channel" data-channel-id="${esc(payload.channelId)}">#${esc(channel.name)}</strong>
          ` : isTag ? `
            <span class="x-action-desc">used tag in</span> <strong class="x-chan-link" data-action="select-channel" data-channel-id="${esc(payload.channelId)}">#${esc(channel.name)}</strong>:
            <span class="x-tag-list">${tags.map((t) => `<span class="x-tag-badge">${esc(t)}</span>`).join(" ")}</span>
          ` : notification.type === "x_mention" ? `
            <span class="x-external-badge">X MENTION</span> <span class="x-action-desc">@${esc(payload.authorUsername || "user")} mentioned</span> <strong>${(payload.monitoredAccounts || []).map((name) => `@${esc(name)}`).join(", ")}</strong>
          ` : notification.type === "mention" ? `
            <span class="x-action-desc">mentioned you in</span> <strong class="x-chan-link" data-action="select-channel" data-channel-id="${esc(payload.channelId)}">#${esc(channel.name)}</strong>
          ` : notification.type === "calendar_advance" ? `
            <span class="calendar-advance-badge">📅 CLIENT SCHEDULE ALERT</span> <span class="x-action-desc">Advance Notice:</span> <strong>${esc(payload.clientName || payload.title || "Client Meeting")}</strong>
          ` : (notification.type === "ai_report" || notification.type === "ai_report_admin") ? `
            <span class="ai-report-badge">✨ AI WEEKLY REPORT</span> <span class="x-action-desc">Weekly Activity Report ready for <strong>${esc(payload.userName || "team member")}</strong></span>
          ` : `
            <span class="x-action-desc">${esc(notification.type || "notification")}</span>
          `}
        </div>

        ${payload.snippet ? `
          <div class="x-notif-snippet">
            ${esc(payload.snippet)}
          </div>
        ` : ""}

        <div class="x-notif-card-footer">
          ${payload.messageId ? `
            <button class="ghost-btn small-btn x-jump-btn" data-action="jump-message" data-channel-id="${esc(payload.channelId || "")}" data-message-id="${esc(payload.messageId)}" data-note-id="${esc(notification.id)}">
              Jump to message &rarr;
            </button>
          ` : ""}
          ${notification.type === "calendar_advance" ? `
            <button class="ghost-btn small-btn x-jump-btn" data-action="workspace-view" data-view="calendar">
              View in Calendar &rarr;
            </button>
          ` : notification.type === "x_mention" && payload.tweetUrl ? `
            <button class="ghost-btn small-btn x-jump-btn" data-action="open-external" data-url="${esc(safeExternalUrl(payload.tweetUrl))}">
              Open tweet &rarr;
            </button>
          ` : (notification.type === "ai_report" || notification.type === "ai_report_admin") ? `
            <button class="ghost-btn small-btn x-jump-btn" data-action="open-ai-report" data-report-id="${esc(payload.reportId || "")}">
              Read Report &rarr;
            </button>
          ` : ""}
          ${!isRead && !isUrgent ? `
            <button class="ghost-btn small-btn notif-read-btn" data-action="mark-notification-read" data-note-id="${esc(notification.id)}">
              <span class="ack-check-icon">✓</span> Mark Reviewed
            </button>
          ` : ""}
          ${isUrgent && payload.messageId && !isRead ? `
            <button class="btn-acknowledge" data-action="acknowledge-message" data-message-id="${esc(payload.messageId)}">✓ Acknowledge</button>
          ` : ""}
          ${isStale ? `<span class="status danger" title="Over 1 hour without review">⏳ 1h+ pending</span>` : (!isRead ? `<span class="unread-glow-dot"></span>` : "")}
        </div>
      </div>
    </article>
  `;
}

function renderNotificationsPanel() {
  const notifications = data.notifications || [];
  const notificationPermission = canUseDesktopNotifications() ? Notification.permission : "unsupported";
  let filter = state.notifFilter || "all";
  if ((!isSuperAdmin(data.currentUser) && (filter === "tags" || filter === "stale")) || (data.currentUser?.role !== "super_admin" && filter === "xmentions")) {
    filter = "all";
  }
  let filtered = notifications;
  if (filter === "urgent") {
    filtered = notifications.filter((n) => n.type === "urgent");
  } else if (filter === "tags") {
    filtered = notifications.filter((n) => n.type === "tag");
  } else if (filter === "mentions") {
    filtered = notifications.filter((n) => n.type === "mention");
  } else if (filter === "xmentions") {
    filtered = notifications.filter((n) => n.type === "x_mention");
  } else if (filter === "stale") {
    const cutoff = Date.now() - 60 * 60 * 1000;
    filtered = notifications.filter((n) => n.type === "urgent" && !n.readAt && !n.read_at && new Date(n.createdAt || n.created_at).getTime() <= cutoff);
  }
  const unreadCount = unreadNotificationsCount();
  const staleItems = isSuperAdmin(data.currentUser) ? getStaleItems(60) : [];

  return `
    <div class="panel-stack x-notifs-panel">
      <div class="x-notifs-toolbar">
        <div>
          <h3>Notifications & Alerts</h3>
          <p class="muted">${unreadCount > 0 ? `${unreadCount} unread alert${unreadCount > 1 ? "s" : ""}` : "All caught up"}</p>
        </div>
        <div class="inline-actions">
          <span class="status ${notificationPermission === "granted" ? "available" : notificationPermission === "denied" ? "danger" : ""}">Desktop: ${notificationPermission === "granted" ? "On" : notificationPermission === "denied" ? "Blocked" : notificationPermission === "default" ? "Ask" : "Unavailable"}</span>
          <button class="ghost-btn small-btn" data-action="test-desktop-notification">Test desktop</button>
          ${unreadCount > 0 ? `<button class="primary-btn small-btn" data-action="mark-all-notifications-read"><span class="ack-check-icon">✓</span> Mark all reviewed</button>` : ""}
        </div>
      </div>

      ${isSuperAdmin(data.currentUser) && staleItems.length > 0 ? `
        <div class="stale-alert-drawer-card">
          <div class="stale-drawer-info">
            <strong>🔔 ${staleItems.length} important message(s) pending review (>1h)</strong>
            <p class="muted">Only messages marked Important to Read appear here.</p>
          </div>
          <button class="btn-press-ack small-btn" data-action="mark-all-stale-reviewed">
            <span class="ack-check-icon">✓</span> Mark All Reviewed
          </button>
        </div>
      ` : ""}

      <div class="x-notif-filter-chips">
        <button class="chip ${filter === "all" ? "is-active" : ""}" data-action="filter-notifs" data-filter="all">All (${notifications.length})</button>
        <button class="chip ${filter === "urgent" ? "is-active danger-chip" : ""}" data-action="filter-notifs" data-filter="urgent">⚠️ Urgent (${notifications.filter((n) => n.type === "urgent").length})</button>
        ${isSuperAdmin(data.currentUser) ? `
          <button class="chip ${filter === "tags" ? "is-active" : ""}" data-action="filter-notifs" data-filter="tags">Tags / #oro (${notifications.filter((n) => n.type === "tag").length})</button>
        ` : ""}
        ${data.currentUser?.role === "super_admin" ? `<button class="chip ${filter === "xmentions" ? "is-active" : ""}" data-action="filter-notifs" data-filter="xmentions">X Mentions (${notifications.filter((n) => n.type === "x_mention").length})</button>` : ""}
        <button class="chip ${filter === "mentions" ? "is-active" : ""}" data-action="filter-notifs" data-filter="mentions">Mentions (${notifications.filter((n) => n.type === "mention").length})</button>
        ${isSuperAdmin(data.currentUser) ? `
          <button class="chip ${filter === "stale" ? "is-active warn-chip" : ""}" data-action="filter-notifs" data-filter="stale">Important 1h+ (${staleItems.length})</button>
        ` : ""}
      </div>

      <div class="x-notifs-feed">
        ${filtered.length ? filtered.map(renderXNotificationCard).join("") : `
          <div class="empty-state">
            <span class="empty-icon">✓</span>
            <p><strong>All caught up!</strong></p>
            <span class="muted">No notifications matching this filter.</span>
          </div>
        `}
      </div>
    </div>
  `;
}

function renderNotification(notification) {
  return renderXNotificationCard(notification);
}

function renderActivityPanel() {
  const activityNotifications = (data.notifications || []).filter((notification) => notification.type !== "x_mention");
  const unreadChannels = data.channels
    .filter((channel) => (channel.unread || channel.mentions || 0) > 0)
    .sort((a, b) => (b.mentions || 0) - (a.mentions || 0) || (b.unread || 0) - (a.unread || 0));
  const recentMessages = data.messages
    .filter((message) => !message.deletedAt && message.authorId !== currentUser().id)
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
    .slice(0, 8);
  return `
    <div class="panel-stack">
      <section class="presence-overview">
        <div><strong>${onlineUsers().length}</strong><span>online</span></div>
        <div><strong>${data.users.length - onlineUsers().length}</strong><span>offline</span></div>
        <div><strong>${data.users.length}</strong><span>total</span></div>
      </section>
      <div class="panel-band">
        <h3>Unread channels</h3>
        ${unreadChannels.map((channel) => `<button class="activity-row" data-action="select-channel" data-channel-id="${channel.id}"><strong>${esc(channelName(channel))}</strong><span>${channel.mentions ? `${channel.mentions} mentions - ` : ""}${channel.unread || 0} unread</span></button>`).join("") || `<p class="muted">No unread channels.</p>`}
      </div>
      <div class="panel-band">
        <h3>Notifications</h3>
        ${activityNotifications.slice(0, 10).map(renderNotification).join("") || `<p class="muted">No notifications yet.</p>`}
      </div>
      <div class="panel-band">
        <h3>Recent activity</h3>
        ${recentMessages.map((message) => `<button class="activity-row" data-action="jump-message" data-message-id="${message.id}"><strong>${esc(userById(message.authorId).displayName)} in ${esc(channelName(data.channels.find((channel) => channel.id === message.channelId)))}</strong><span>${esc(message.body).slice(0, 120)}</span></button>`).join("") || `<p class="muted">No recent activity.</p>`}
      </div>
    </div>
  `;
}

function renderThreadPanel() {
  const root = data.messages.find((message) => message.id === state.selectedThreadId);
  if (!root || root.deletedAt) return `<div class="empty-state">Select a message thread.</div>`;
  const replies = data.messages.filter((message) => message.parentId === root.id && !message.deletedAt).sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
  return `
    <div class="panel-stack">
      ${renderMessage(root, true)}
      <div class="panel-band"><h3>${replies.length} replies</h3>${replies.map((reply) => `<div class="thread-reply">${renderMessage(reply, true)}</div>`).join("") || `<p class="muted">No replies yet.</p>`}</div>
      <form class="panel-band" id="threadReplyForm">
        <label class="field"><span>Reply</span><textarea name="reply" placeholder="Add a threaded reply"></textarea></label>
        <button class="primary-btn" type="submit">Reply</button>
      </form>
    </div>
  `;
}

function loadRecentSearches() {
  try {
    const saved = JSON.parse(localStorage.getItem("beenco-recent-searches") || "[]");
    return Array.isArray(saved) ? saved.filter((item) => typeof item === "string").slice(0, 6) : [];
  } catch (_) {
    return [];
  }
}

function saveRecentSearch(query) {
  const clean = String(query || "").trim();
  if (!clean) return;
  state.recentSearches = [clean, ...(state.recentSearches || []).filter((item) => item.toLowerCase() !== clean.toLowerCase())].slice(0, 6);
  try {
    localStorage.setItem("beenco-recent-searches", JSON.stringify(state.recentSearches));
  } catch (_) {}
}

function renderSearchPanel() {
  const rawQuery = state.searchQuery.trim();
  const query = rawQuery.toLowerCase();
  const messageResults = query ? data.messages.filter((message) => !message.deletedAt && `${message.body} ${channelName(data.channels.find((channel) => channel.id === message.channelId))} ${userById(message.authorId).displayName}`.toLowerCase().includes(query)).slice(0, 12) : [];
  const fileResults = query ? data.files.filter((file) => `${file.originalName} ${file.extractedText || ""}`.toLowerCase().includes(query)).slice(0, 8) : [];
  const peopleResults = query ? data.users.filter((user) => `${user.displayName || ""} ${user.email || ""} ${user.title || ""}`.toLowerCase().includes(query)).slice(0, 6) : [];
  const channelResults = query ? data.channels.filter((channel) => `${channelName(channel)} ${channel.topic || ""} ${channel.description || ""}`.toLowerCase().includes(query)).slice(0, 6) : [];
  const resultCount = messageResults.length + fileResults.length + peopleResults.length + channelResults.length;
  return `
    <div class="slack-search-panel">
      <div class="slack-search-input-row">
        ${chatIcon("search")}
        <input data-action="global-search" value="${esc(state.searchQuery)}" placeholder="Search across people, channels, files, workflows, and more" autocomplete="off" aria-label="Search Beenco" />
        <button class="search-filter-btn" type="button" title="Search filters" aria-label="Search filters">≡</button>
        ${rawQuery ? `<button class="search-clear-btn" type="button" data-action="clear-global-search" title="Clear search" aria-label="Clear search">✕</button>` : ""}
        <button class="search-clear-btn" type="button" data-action="close-details" title="Close search" aria-label="Close search">✕</button>
      </div>
      <div class="slack-search-scope">${chatIcon("search")}<span>Search in</span><strong>${esc(channelName(activeChannel()))}</strong><kbd>Enter</kbd></div>
      ${!query ? `
        <div class="recent-searches">
          <p>Recent searches</p>
          ${(state.recentSearches || []).map((item) => `<button type="button" data-action="use-recent-search" data-query="${esc(item)}"><span class="recent-clock">◷</span><span>${esc(item)}</span></button>`).join("") || `<div class="search-empty-copy">Your recent searches will appear here.</div>`}
        </div>
      ` : `
        <div class="search-results-head"><strong>${resultCount} result${resultCount === 1 ? "" : "s"}</strong><span>Messages, people, channels and files</span></div>
        <div class="slack-search-results">
          ${peopleResults.map((user) => `<button class="slack-search-result" data-action="start-dm" data-user-id="${esc(user.id)}"><span class="search-avatar">${initials(user.displayName)}</span><span><strong>${esc(user.displayName)}</strong><small>${esc(user.title || user.email || "Person")}</small></span><em>Person</em></button>`).join("")}
          ${channelResults.map((channel) => `<button class="slack-search-result" data-action="select-channel" data-channel-id="${esc(channel.id)}"><span class="search-kind">#</span><span><strong>${esc(channelName(channel))}</strong><small>${esc(channel.topic || channel.description || "Channel")}</small></span><em>Channel</em></button>`).join("")}
          ${messageResults.map((message) => `<button class="slack-search-result" data-action="jump-message" data-message-id="${esc(message.id)}"><span class="search-avatar">${initials(userById(message.authorId).displayName)}</span><span><strong>${esc(userById(message.authorId).displayName)} in ${esc(channelName(data.channels.find((channel) => channel.id === message.channelId)))}</strong><small>${esc(message.body).slice(0, 180)}</small></span><em>Message</em></button>`).join("")}
          ${fileResults.map((file) => `<button class="slack-search-result" data-action="select-channel" data-channel-id="${esc(file.channelId)}"><span class="search-kind">▧</span><span><strong>${esc(file.originalName)}</strong><small>${formatBytes(file.sizeBytes)} · ${esc(file.status)}</small></span><em>File</em></button>`).join("")}
          ${!resultCount ? `<div class="search-empty-copy">No results for “${esc(rawQuery)}”.</div>` : ""}
        </div>
      `}
      <footer class="slack-search-footer"><span>↑</span><span>↓</span><span>Select</span><a href="#" tabindex="-1">Search tips</a></footer>
    </div>
  `;
}

function compactMetric(value) {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function renderXMentionsView() {
  if (currentUser()?.role !== "super_admin") {
    return `<div class="empty-state">Super Admin access required.</div>`;
  }
  const status = data.xMentionStatus || {};
  const accounts = status.monitoredAccounts || [];
  const selected = state.xMentionAccountFilter || "all";
  const mentionCounts = new Map(accounts.map((account) => [account.toLowerCase(), 0]));
  for (const mention of data.xMentions || []) {
    for (const name of mention.monitoredUsernames || []) {
      const key = String(name).toLowerCase();
      mentionCounts.set(key, (mentionCounts.get(key) || 0) + 1);
    }
  }
  const mentions = (data.xMentions || []).filter((mention) => selected === "all" || (mention.monitoredUsernames || []).some((name) => name.toLowerCase() === selected.toLowerCase()));
  const cooldownText = status.cooldownUntil ? formatRelative(status.cooldownUntil) : "";
  const statusError = status.lastError || "";
  const accountIntervalSeconds = Number(status.accountIntervalSeconds || 60);
  const cycleMinutes = Math.max(1, Math.ceil((accounts.length * accountIntervalSeconds) / 60));
  return `
    <div class="x-trends-page">
      <section class="x-trends-summary">
        <div>
          <span class="x-trends-kicker">SUPER ADMIN INTELLIGENCE</span>
          <h2>X Mentions & Trends</h2>
          <p>New tweets from today only. Checking 1 monitored account every ${Math.round(accountIntervalSeconds / 60) || 1} minute; full cycle about ${cycleMinutes} minutes.</p>
        </div>
        <div class="x-trends-controls">
          <span class="status ${status.configured ? "available" : "danger"}">${status.configured ? (status.running ? "Checking now" : "Monitor active") : "API key required"}</span>
          <button class="primary-btn small-btn" data-action="refresh-x-mentions" ${!status.configured || status.running ? "disabled" : ""}>Refresh now</button>
        </div>
      </section>

      ${statusError ? `<div class="x-trends-error"><strong>${statusError.includes("429") || status.cooldownUntil ? "Rate limited by X API:" : "Last check had an issue:"}</strong> ${esc(statusError)}${cooldownText ? ` Retrying ${esc(cooldownText)}.` : ""}</div>` : ""}

      <section class="x-trends-toolbar">
        <div><strong>${mentions.length} mention${mentions.length === 1 ? "" : "s"}</strong><span class="muted">Last checked ${status.lastPoll ? formatRelative(status.lastPoll) : "not yet"}${status.lastCheckedAccount ? ` via @${esc(status.lastCheckedAccount)}` : ""}</span></div>
        <div class="x-account-filter"><span>Account</span>
          <div class="x-account-dropdown">
            <button type="button" class="x-account-dropdown-toggle" aria-haspopup="listbox">${selected === "all" ? "All monitored accounts" : `@${esc(selected)}`}</button>
            <div class="x-account-dropdown-menu" role="listbox">
              <button type="button" role="option" class="${selected === "all" ? "is-active" : ""}" data-action="filter-x-mentions" data-account="all">All monitored accounts</button>
              ${accounts.map((account) => `<button type="button" role="option" class="${selected === account ? "is-active" : ""}" data-action="filter-x-mentions" data-account="${esc(account)}">@${esc(account)}</button>`).join("")}
            </div>
          </div>
        </div>
      </section>

      <section class="x-account-chips" aria-label="Monitored account filter">
        <button class="${selected === "all" ? "is-active" : ""}" data-action="filter-x-mentions" data-account="all">All <span>${data.xMentions.length}</span></button>
        ${accounts.map((account) => {
          const count = mentionCounts.get(account.toLowerCase()) || 0;
          return `<button class="${selected.toLowerCase() === account.toLowerCase() ? "is-active" : ""}" data-action="filter-x-mentions" data-account="${esc(account)}">@${esc(account)} <span>${count}</span></button>`;
        }).join("")}
      </section>

      <div class="x-trends-feed">
        ${mentions.length ? mentions.map((mention) => {
          const tweetUrl = safeExternalUrl(mention.tweetUrl || "");
          const profilePicture = safeExternalUrl(mention.authorProfilePicture || "");
          return `
            <article class="x-trend-card">
              <div class="x-trend-avatar"><span>${initials(mention.authorName || mention.authorUsername || "X")}</span>${profilePicture ? `<img src="${esc(profilePicture)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()" />` : ""}</div>
              <div class="x-trend-content">
                <div class="x-trend-author"><strong>${esc(mention.authorName || mention.authorUsername || "Unknown")}</strong><span>@${esc(mention.authorUsername || "unknown")}</span><span>·</span><time>${formatRelative(mention.createdAt)}</time></div>
                <p class="x-trend-text">${esc(mention.text || "")}</p>
                <div class="x-trend-targets">${(mention.monitoredUsernames || []).map((name) => `<span>@${esc(name)}</span>`).join("")}</div>
                <div class="x-trend-footer">
                  <div class="x-trend-metrics"><span title="Replies">Reply ${compactMetric(mention.replyCount)}</span><span title="Reposts">Repost ${compactMetric(mention.retweetCount)}</span><span title="Likes">Like ${compactMetric(mention.likeCount)}</span><span title="Views">View ${compactMetric(mention.viewCount)}</span></div>
                  ${tweetUrl ? `<button class="ghost-btn small-btn" data-action="open-external" data-url="${esc(tweetUrl)}">Open on X</button>` : ""}
                </div>
              </div>
            </article>`;
        }).join("") : `<div class="empty-state"><p><strong>No mentions collected yet.</strong></p><span class="muted">The monitor will add new tweets after its next successful check.</span></div>`}
      </div>
    </div>`;
}

function renderFilesPanel() {
  const deletedAttachmentIds = new Set(
    data.messages
      .filter((message) => message.deletedAt)
      .flatMap((message) => message.attachments || [])
  );
  const files = data.files.filter(file => !deletedAttachmentIds.has(file.id) && (state.workspaceMode === "files" || file.channelId === activeChannel().id))
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const links = collectSharedLinks({ channelId: state.workspaceMode === "files" ? null : activeChannel().id });
  return `<div class="panel-stack">
    <div class="files-heading"><div><h3>Files & links</h3><p class="muted">${files.length} files · ${links.length} links · ${state.workspaceMode === "files" ? "Across your conversations" : esc(channelName())}</p></div>
      <button class="ghost-btn" data-action="open-panel" data-panel="search" aria-label="Search files">${chatIcon("search")} Search</button></div>
    <section class="files-section">
      <h4>Files</h4>
      ${files.map(renderAttachment).join("") || '<div class="empty-state">Files shared in your conversations will appear here.</div>'}
    </section>
    <section class="files-section">
      <h4>Links</h4>
      ${links.map(renderSharedLink).join("") || '<div class="empty-state">Links shared in your conversations will appear here.</div>'}
    </section>
  </div>`;
}

function renderSharedLink(link) {
  const channel = data.channels.find((item) => item.id === link.channelId);
  const author = userById(link.authorId);
  let host = "";
  try {
    host = new URL(link.href).hostname.replace(/^www\./, "");
  } catch {
    host = link.href;
  }
  return `
    <article class="link-card">
      <span class="link-icon">↗</span>
      <div class="link-card-main">
        <strong>${esc(host || "Shared link")}</strong>
        <a href="${esc(link.href)}" target="_blank" rel="noreferrer">${esc(link.label)}</a>
        <span class="muted">${esc(author.displayName || "Someone")} · ${esc(channelName(channel))} · ${formatRelative(link.createdAt)}</span>
      </div>
      <div class="file-actions">
        <button class="ghost-btn small-btn" data-action="jump-message" data-channel-id="${esc(link.channelId || "")}" data-message-id="${esc(link.messageId || "")}">Jump</button>
        <button class="ghost-btn small-btn" data-action="open-external" data-url="${esc(link.href)}">Open</button>
      </div>
    </article>
  `;
}

function channelTasks(channelId = activeChannel().id) {
  return (data.tasks || [])
    .filter((task) => task.channelId === channelId)
    .sort((a, b) => {
      const statusOrder = { open: 0, in_progress: 1, done: 2, cancelled: 3 };
      return (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9)
        || String(a.deadline || "9999-12-31").localeCompare(String(b.deadline || "9999-12-31"))
        || new Date(b.createdAt) - new Date(a.createdAt);
    });
}

function renderMemberPicker(name = "memberIds") {
  const users = data.users
    .filter((user) => user.id !== currentUser()?.id && user.isActive !== false)
    .sort((a, b) => Number(b.isOnline) - Number(a.isOnline) || a.displayName.localeCompare(b.displayName));
  return `
    <div class="member-picker">
      ${users.map((user) => `
        <label class="member-option">
          <input type="checkbox" name="${name}" value="${esc(user.id)}" />
          <span class="avatar tiny">${initials(user.displayName)}</span>
          <span><strong>${esc(user.displayName)}</strong><small>${esc(presenceLabel(user))}</small></span>
        </label>
      `).join("") || `<p class="muted">No users available.</p>`}
    </div>
  `;
}

function renderGroupMembersManager() {
  const channel = activeChannel();
  if (channel.type !== "private" || !isChannelOwner(channel)) return "";
  const members = activeChannelMembers();
  const ownerIds = new Set(channel.ownerIds || []);
  const nonMembers = data.users.filter((user) => user.isActive !== false && !members.some((member) => member.id === user.id));
  return `
    <section class="panel-band">
      <h3>Manage "${esc(channel.name)}" members</h3>
      <div class="people-list">
        ${members.map((member) => `
          <div class="people-row">
            <div class="people-main">
              <span class="avatar small">${initials(member.displayName)}</span>
              <div class="people-info">
                <div class="people-name-line">
                  <strong class="people-name">${esc(member.displayName)}</strong>
                  ${ownerIds.has(member.id) ? `<span class="user-role-badge admin">Owner</span>` : ""}
                </div>
                <span class="people-subtitle">${ownerIds.has(member.id) ? "Group owner" : "Member"}</span>
              </div>
            </div>
            <div class="people-action">
              ${!ownerIds.has(member.id) ? `<button class="danger-btn small-btn" data-action="remove-group-member" data-channel-id="${esc(channel.id)}" data-user-id="${esc(member.id)}">Remove</button>` : `<span class="status available">owner</span>`}
            </div>
          </div>
        `).join("")}
      </div>
      ${nonMembers.length ? `
        <form class="add-member-form" id="addGroupMemberForm" data-channel-id="${esc(channel.id)}">
          <label class="field">
            <span>Add member</span>
            <select name="userId">${nonMembers.map((user) => `<option value="${esc(user.id)}">${esc(user.displayName)}</option>`).join("")}</select>
          </label>
          <button class="primary-btn small-btn" type="submit">Add to group</button>
        </form>
      ` : `<p class="muted">Everyone active is already a member.</p>`}
      ${canDeleteChannel(channel) ? `
        <div class="channel-danger-zone" style="margin-top:14px;padding-top:12px;border-top:1px solid #333846;">
          <button class="danger-btn small-btn" data-action="delete-channel" data-channel-id="${esc(channel.id)}">🗑️ Delete "${esc(channel.name)}" group</button>
        </div>
      ` : ""}
    </section>
  `;
}

function renderGroupPanel() {
  return `
    <div class="panel-stack">
      <form class="panel-band group-form" id="groupForm">
        <h3>Create private group</h3>
        <label class="field"><span>Group name</span><input name="name" required placeholder="project-launch" /></label>
        <label class="field"><span>Topic</span><input name="topic" placeholder="What this group is for" /></label>
        <label class="field"><span>Description</span><textarea name="description" placeholder="Add context for members"></textarea></label>
        <label class="field"><span>Members</span>${renderMemberPicker()}</label>
        <button class="primary-btn" type="submit">Create private group</button>
      </form>
      ${renderGroupMembersManager()}
      <section class="panel-band">
        <h3>Group permissions</h3>
        <div class="policy-list">
          <div><strong>Anyone</strong><span>Create a private group and automatically become its owner.</span></div>
          <div><strong>Group owner</strong><span>Add or remove members and create tasks in their own group.</span></div>
          <div><strong>Admins</strong><span>Create public channels, manage any group, and assign tasks anywhere.</span></div>
        </div>
      </section>
    </div>
  `;
}

function renderTaskCard(task) {
  const assignee = task.assigneeId ? userById(task.assigneeId) : null;
  const creator = userById(task.creatorId);
  const canUpdate = isAdmin() || task.creatorId === currentUser()?.id || task.assigneeId === currentUser()?.id;
  return `
    <article class="task-card ${task.status}">
      <div class="task-card-head">
        <div>
          <strong>${esc(task.title)}</strong>
          <span>Created by ${esc(creator.displayName)}${assignee ? ` - assigned to ${esc(assignee.displayName)}` : ""}</span>
        </div>
        <span class="status ${task.status === "done" ? "available" : task.status === "cancelled" ? "quarantined" : "pending"}">${esc(task.status.replace("_", " "))}</span>
      </div>
      ${task.subject ? `<p>${esc(task.subject)}</p>` : ""}
      <div class="task-meta-grid">
        <div><strong>Deadline</strong><span>${esc(task.deadline || "No date")}</span></div>
        <div><strong>Tags</strong><span>${task.tags?.length ? task.tags.map((tag) => `#${esc(tag)}`).join(" ") : "No tags"}</span></div>
      </div>
      ${task.referenceLinks?.length ? `<div class="task-links">${task.referenceLinks.map((link) => {
        const href = safeExternalUrl(link);
        return href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(link)}</a>` : `<span>${esc(link)}</span>`;
      }).join("")}</div>` : ""}
      ${canUpdate ? `<div class="inline-actions task-actions">
        ${["open", "in_progress", "done", "cancelled"].map((status) => `<button class="chip ${task.status === status ? "is-active" : ""}" data-action="task-status" data-task-id="${esc(task.id)}" data-status="${status}">${esc(status.replace("_", " "))}</button>`).join("")}
      </div>` : ""}
    </article>
  `;
}

function renderTasksPanel() {
  const channel = activeChannel();
  const tasks = channelTasks(channel.id);
  const members = activeChannelMembers().filter((user) => user.id && user.isActive !== false);
  return `
    <div class="panel-stack">
      <section class="presence-overview task-overview">
        <div><strong>${tasks.filter((task) => task.status !== "done" && task.status !== "cancelled").length}</strong><span>open</span></div>
        <div><strong>${tasks.filter((task) => task.status === "done").length}</strong><span>done</span></div>
        <div><strong>${members.length}</strong><span>members</span></div>
      </section>
      ${isChannelOwner(channel) ? `
        <form class="panel-band task-form" id="taskForm">
          <h3>Create task</h3>
          <label class="field"><span>Title</span><input name="title" required placeholder="Task title" /></label>
          <label class="field"><span>Subject</span><textarea name="subject" placeholder="Task details"></textarea></label>
          <label class="field"><span>Reference links</span><textarea name="referenceLinks" placeholder="One link per line"></textarea></label>
          <label class="field"><span>Deadline date</span><input name="deadline" type="date" /></label>
          <label class="field"><span>Tags</span><input name="tags" placeholder="security, urgent, client" /></label>
          <label class="field"><span>Assign to</span><select name="assigneeId"><option value="">Unassigned</option>${members.map((user) => `<option value="${esc(user.id)}">${esc(user.displayName)}</option>`).join("")}</select></label>
          <button class="primary-btn" type="submit">Create task</button>
        </form>
      ` : `<section class="panel-band"><h3>Tasks</h3><p class="muted">The group owner or an admin creates tasks here. Assigned users can update their task status.</p></section>`}
      <section class="panel-band">
        <h3>${esc(channelName(channel))} tasks</h3>
        ${tasks.map(renderTaskCard).join("") || `<div class="empty-state">No tasks in this group yet.</div>`}
      </section>
    </div>
  `;
}

function renderRemoteAssistControls(session) {
  if (session.status === "pending" && session.requesterId === currentUser()?.id) {
    return `<button class="ghost-btn small-btn" data-action="remote-assist-end" data-session-id="${esc(session.id)}">Cancel</button>`;
  }
  if (session.status === "accepted") {
    const controls = session.rdpReady && session.requesterId === currentUser()?.id
      ? `<button class="primary-btn small-btn" data-action="remote-assist-download" data-session-id="${esc(session.id)}">Download RDP</button><button class="ghost-btn small-btn" data-action="remote-assist-copy" data-session-id="${esc(session.id)}">Copy command</button>`
      : "";
    return `${controls}<button class="ghost-btn small-btn" data-action="remote-assist-end" data-session-id="${esc(session.id)}">End</button>`;
  }
  return "";
}

function renderRemoteAssistSession(session) {
  const peer = remoteAssistPeer(session);
  const isTarget = session.targetUserId === currentUser()?.id;
  const actorLabel = isTarget ? `From ${peer.displayName}` : `To ${peer.displayName}`;
  return `
    <article class="remote-card">
      <div class="admin-row">
        <div>
          <strong>${esc(actorLabel)}</strong>
          <span>${esc(remoteAssistStatusLabel(session))} - ${formatRelative(session.updatedAt || session.createdAt)}</span>
        </div>
        <span class="status ${session.status === "accepted" ? "available" : session.status === "pending" ? "pending" : ""}">${esc(session.status)}</span>
      </div>
      ${session.reason ? `<p class="secondary">${esc(session.reason)}</p>` : ""}
      ${session.targetHost ? `<div class="copy-row"><span>${esc(session.targetHost)}</span><strong>RDP host</strong></div>` : ""}
      ${session.status === "pending" && isTarget ? `
        <form class="remote-response" data-action="remote-assist-response" data-session-id="${esc(session.id)}">
          <label class="field"><span>This PC name or IP</span><input name="targetHost" value="${esc(session.targetHost || "")}" placeholder="DESKTOP-123 or 10.30.0.25" required /></label>
          <div class="inline-actions">
            <button class="primary-btn small-btn" type="submit">Accept</button>
            <button class="danger-btn small-btn" type="button" data-action="remote-assist-reject" data-session-id="${esc(session.id)}">Reject</button>
          </div>
        </form>
      ` : ""}
      <div class="inline-actions">${renderRemoteAssistControls(session)}</div>
    </article>
  `;
}

function renderRemoteAssistPanel() {
  const sessions = data.remoteAssists || [];
  const currentId = currentUser()?.id;
  const pendingIncoming = sessions.filter((session) => session.status === "pending" && session.targetUserId === currentId);
  const active = sessions.filter((session) => session.status === "accepted" || (session.status === "pending" && session.requesterId === currentId));
  const history = sessions.filter((session) => !["pending", "accepted"].includes(session.status)).slice(0, 8);
  const candidates = peopleCandidates();
  const defaultTargetId = candidates[0]?.id || "";
  return `
    <div class="panel-stack">
      <form class="panel-band" id="remoteAssistForm">
        <h3>Remote Assist</h3>
        <label class="field"><span>Employee</span><select name="targetUserId" required>${candidates.map((user) => `<option value="${user.id}" ${user.id === defaultTargetId ? "selected" : ""}>${esc(user.displayName)} - ${esc(presenceLabel(user))}</option>`).join("")}</select></label>
        <label class="field"><span>Known PC name or IP</span><input name="targetHost" placeholder="Optional before approval" /></label>
        <label class="field"><span>Reason</span><textarea name="reason" placeholder="What support is needed?"></textarea></label>
        <button class="primary-btn" type="submit" ${!defaultTargetId ? "disabled" : ""}>Request access</button>
      </form>
      <section class="panel-band">
        <h3>Consent queue</h3>
        ${pendingIncoming.map(renderRemoteAssistSession).join("") || `<p class="muted">No pending requests for your PC.</p>`}
      </section>
      <section class="panel-band">
        <h3>Active sessions</h3>
        ${active.map(renderRemoteAssistSession).join("") || `<p class="muted">No active remote assistance sessions.</p>`}
      </section>
      <section class="panel-band">
        <h3>Recent remote history</h3>
        ${history.map(renderRemoteAssistSession).join("") || `<p class="muted">No remote assistance history yet.</p>`}
      </section>
    </div>
  `;
}

function daysUntilDate(dateStr) {
  if (!dateStr) return 0;
  const target = new Date(dateStr);
  target.setHours(0, 0, 0, 0);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((target.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

function formatDateOnly(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function formatTimeOnly(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderCalendarPanel() {
  const events = (data.events || []).sort((a, b) => new Date(a.startsAt) - new Date(b.startsAt));
  return `
    <div class="panel-stack">
      <div class="inline-actions" style="margin-bottom:8px;">
        <button class="primary-btn small-btn" data-action="workspace-view" data-view="calendar">Open Full Monthly Calendar 📅</button>
      </div>
      ${events.map((event) => {
        const isClient = Boolean(event.clientName || event.eventType === "client");
        const dUntil = daysUntilDate(event.startsAt);
        return `
          <article class="event-card ${isClient ? "is-client-event-card" : ""}">
            <div class="event-card-top">
              <time>${formatDateTime(event.startsAt)}</time>
              ${isClient ? `<span class="badge warn">🤝 Client Meeting</span>` : ""}
              ${dUntil >= 1 && dUntil <= (event.advanceNoticeDays || 4) ? `<span class="badge warn">in ${dUntil}d</span>` : ""}
            </div>
            <strong>${esc(event.clientName ? `${event.clientName} — ${event.title}` : event.title)}</strong>
            ${event.description ? `<p>${esc(event.description)}</p>` : ""}
            <span class="muted">${esc(event.location || "Online Huddle")}</span>
            <div class="inline-actions">
              <button class="ghost-btn small-btn" data-action="event-response" data-event-id="${event.id}" data-response="accepted">Accept</button>
              <button class="ghost-btn small-btn" data-action="event-response" data-event-id="${event.id}" data-response="tentative">Tentative</button>
              ${event.creatorId === currentUser()?.id || isAdmin() ? `<button class="ghost-btn small-btn danger-btn" data-action="delete-event" data-event-id="${event.id}">Delete</button>` : ""}
            </div>
          </article>
        `;
      }).join("") || `<div class="empty-state">No events yet.</div>`}
      <form class="panel-band" id="eventForm">
        <h3>New Event / Client Meeting</h3>
        <label class="field"><span>Title *</span><input name="title" required placeholder="Meeting title" /></label>
        <label class="field"><span>Event Type</span><select name="eventType"><option value="client">🤝 Client Meeting</option><option value="meeting">👥 Internal Team Meeting</option><option value="call">📞 One-on-One</option></select></label>
        <label class="field"><span>Client Name</span><input name="clientName" placeholder="e.g. Acme Corp" /></label>
        <label class="field"><span>Start *</span><input name="startsAt" type="datetime-local" required /></label>
        <label class="field"><span>Daily Advance Alert</span><select name="advanceNoticeDays"><option value="4" selected>4 Days Ahead (Daily Alert)</option><option value="3">3 Days Ahead (Daily Alert)</option><option value="5">5 Days Ahead (Daily Alert)</option><option value="2">2 Days Ahead (Daily Alert)</option><option value="1">1 Day Ahead (Daily Alert)</option></select></label>
        <label class="field"><span>Location</span><input name="location" placeholder="Room or huddle" /></label>
        <label class="field"><span>Description</span><textarea name="description" placeholder="Agenda & notes"></textarea></label>
        <button class="primary-btn" type="submit">Create event</button>
      </form>
    </div>
  `;
}

function renderMonthlyCalendarView() {
  const year = state.calendarYear;
  const month = state.calendarMonth;
  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const curMonthName = monthNames[month];
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;

  const events = (data.events || []);
  const advanceAlerts = events.filter((e) => {
    const dUntil = daysUntilDate(e.startsAt);
    const noticeDays = Number(e.advanceNoticeDays || 4);
    return dUntil >= 1 && dUntil <= noticeDays;
  }).sort((a, b) => new Date(a.startsAt) - new Date(b.startsAt));

  const firstDayIndex = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const prevMonthDays = new Date(year, month, 0).getDate();

  const days = [];
  for (let i = firstDayIndex - 1; i >= 0; i--) {
    const d = prevMonthDays - i;
    const prevMonthYear = month === 0 ? year - 1 : year;
    const prevMonthIdx = month === 0 ? 11 : month - 1;
    const dateStr = `${prevMonthYear}-${String(prevMonthIdx + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    days.push({ day: d, dateStr, isCurrentMonth: false });
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    days.push({ day: d, dateStr, isCurrentMonth: true, isToday: dateStr === todayStr });
  }
  const remaining = 7 - (days.length % 7);
  if (remaining < 7) {
    for (let d = 1; d <= remaining; d++) {
      const nextMonthYear = month === 11 ? year + 1 : year;
      const nextMonthIdx = month === 11 ? 0 : month + 1;
      const dateStr = `${nextMonthYear}-${String(nextMonthIdx + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      days.push({ day: d, dateStr, isCurrentMonth: false });
    }
  }

  const upcomingEvents = [...events]
    .filter((e) => new Date(e.startsAt) >= new Date(Date.now() - 24 * 60 * 60 * 1000))
    .sort((a, b) => new Date(a.startsAt) - new Date(b.startsAt));

  return `
    <div class="calendar-page-layout">
      ${advanceAlerts.length > 0 ? `
        <div class="calendar-advance-banner">
          <div class="banner-icon-col">
            <span class="pulse-bell">🔔</span>
          </div>
          <div class="banner-body-col">
            <div class="banner-heading-row">
              <strong>DAILY ADVANCE NOTICE: ${advanceAlerts.length} Upcoming Meeting${advanceAlerts.length > 1 ? "s" : ""} in 3–4 Day Window</strong>
              <span class="banner-daily-tag">Daily Alert Active</span>
            </div>
            <p class="banner-subtitle">You have important meetings scheduled in the next few days. Daily alerts ensure your team can prepare client schedules, agendas, and deliverables ahead of time:</p>
            <div class="advance-meeting-cards">
              ${advanceAlerts.map((e) => {
                const dUntil = daysUntilDate(e.startsAt);
                const isClient = Boolean(e.clientName || e.eventType === "client");
                const canDelete = e.creatorId === currentUser()?.id || isAdmin();
                return `
                  <div class="advance-meeting-card ${isClient ? "is-client-adv" : ""}">
                    <div class="adv-card-header">
                      <span class="adv-badge">${isClient ? "🤝 Client Meeting" : "📅 Team Event"}</span>
                      <span class="adv-countdown ${dUntil <= 1 ? "is-imminent" : ""}">in ${dUntil} day${dUntil > 1 ? "s" : ""} (${formatDateOnly(e.startsAt)})</span>
                      ${canDelete ? `<button class="ghost-btn small-btn danger-btn" style="margin-left:auto;padding:1px 6px;font-size:11px;" data-action="delete-event" data-event-id="${e.id}" title="Delete plan">Delete</button>` : ""}
                    </div>
                    <strong class="adv-title">${esc(e.clientName ? `${e.clientName} — ${e.title}` : e.title)}</strong>
                    <div class="adv-details">
                      <span>⏰ ${formatTimeOnly(e.startsAt)}</span>
                      ${e.location ? `<span>📍 ${esc(e.location)}</span>` : ""}
                    </div>
                  </div>
                `;
              }).join("")}
            </div>
          </div>
        </div>
      ` : ""}

      <div class="calendar-header-bar">
        <div class="cal-nav-controls">
          <button class="icon-btn" data-action="cal-prev-month" title="Previous month">&larr;</button>
          <h2>${curMonthName} ${year}</h2>
          <button class="icon-btn" data-action="cal-next-month" title="Next month">&rarr;</button>
          <button class="ghost-btn small-btn" data-action="cal-today">Today</button>
        </div>
        <div class="cal-actions">
          <button class="primary-btn small-btn" data-action="open-event-modal">+ Schedule Client Meeting / Event</button>
        </div>
      </div>

      <div class="calendar-main-grid-wrap">
        <div class="calendar-grid-header">
          <div>Sun</div><div>Mon</div><div>Tue</div><div>Wed</div><div>Thu</div><div>Fri</div><div>Sat</div>
        </div>
        <div class="calendar-grid-cells">
          ${days.map((cell) => {
            const dayEvents = events.filter((e) => e.startsAt && e.startsAt.startsWith(cell.dateStr));
            return `
              <div class="cal-cell ${!cell.isCurrentMonth ? "is-other-month" : ""} ${cell.isToday ? "is-today" : ""}" data-date="${cell.dateStr}">
                <div class="cal-cell-top">
                  <span class="cal-cell-num ${cell.isToday ? "today-circle" : ""}">${cell.day}</span>
                  <button class="cal-quick-add" data-action="open-event-modal" data-date="${cell.dateStr}" title="Add meeting on ${cell.dateStr}">+</button>
                </div>
                <div class="cal-cell-events">
                  ${dayEvents.slice(0, 3).map((e) => {
                    const isClient = Boolean(e.clientName || e.eventType === "client");
                    const dUntil = daysUntilDate(e.startsAt);
                    const hasAdvAlert = dUntil >= 1 && dUntil <= (e.advanceNoticeDays || 4);
                    const canDelete = e.creatorId === currentUser()?.id || isAdmin();
                    return `
                      <div class="cal-event-pill ${isClient ? "is-client-pill" : "is-meeting-pill"}" title="${esc(e.title)} (${formatTimeOnly(e.startsAt)})">
                        <span class="pill-dot"></span>
                        <span class="pill-text">${isClient ? `🤝 ${esc(e.clientName || e.title)}` : esc(e.title)}</span>
                        <span class="pill-time">${formatTimeOnly(e.startsAt)}</span>
                        ${hasAdvAlert ? `<span class="pill-alert-chip" title="Advance Alert: in ${dUntil}d">⚠️ ${dUntil}d</span>` : ""}
                        ${canDelete ? `<button class="pill-del-btn" data-action="delete-event" data-event-id="${e.id}" title="Delete plan / event">✕</button>` : ""}
                      </div>
                    `;
                  }).join("")}
                  ${dayEvents.length > 3 ? `<span class="cal-more-chip">+${dayEvents.length - 3} more</span>` : ""}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      </div>

      <div class="calendar-upcoming-section">
        <div class="section-title-row">
          <h3>Upcoming Schedule & Client Meetings (${upcomingEvents.length})</h3>
          <span class="muted">Daily advance alerts trigger 3 to 4 days ahead of scheduled date</span>
        </div>
        <div class="calendar-upcoming-list">
          ${upcomingEvents.map((e) => {
            const dUntil = daysUntilDate(e.startsAt);
            const isClient = Boolean(e.clientName || e.eventType === "client");
            const canDelete = e.creatorId === currentUser()?.id || isAdmin();
            let countdownLabel = "";
            if (dUntil < 0) countdownLabel = "Past";
            else if (dUntil === 0) countdownLabel = "Today";
            else if (dUntil === 1) countdownLabel = "Tomorrow";
            else countdownLabel = `In ${dUntil} days`;

            return `
              <div class="upcoming-item-card ${isClient ? "is-client-card" : ""}">
                <div class="upcoming-date-box">
                  <span class="up-month">${new Date(e.startsAt).toLocaleDateString("en-US", { month: "short" })}</span>
                  <strong class="up-day">${new Date(e.startsAt).getDate()}</strong>
                  <span class="up-time">${formatTimeOnly(e.startsAt)}</span>
                </div>
                <div class="upcoming-item-body">
                  <div class="upcoming-tags">
                    <span class="badge ${isClient ? "warn" : ""}">${isClient ? "🤝 Client Meeting" : "Team Sync"}</span>
                    <span class="countdown-badge ${dUntil <= 4 && dUntil >= 0 ? "is-advance-window" : ""}">${countdownLabel}</span>
                    <span class="mini-meta">Alert: ${e.advanceNoticeDays || 4} days ahead</span>
                  </div>
                  <h4 class="upcoming-title">${esc(e.title)}</h4>
                  ${e.clientName ? `<p class="upcoming-client"><strong>Client:</strong> ${esc(e.clientName)}</p>` : ""}
                  ${e.description ? `<p class="upcoming-desc">${esc(e.description)}</p>` : ""}
                  <div class="upcoming-meta-row">
                    ${e.location ? `<span>📍 ${esc(e.location)}</span>` : ""}
                    ${e.creatorName ? `<span>👤 Created by ${esc(e.creatorName)}</span>` : ""}
                  </div>
                </div>
                <div class="upcoming-item-actions">
                  ${canDelete ? `<button class="ghost-btn small-btn danger-btn" data-action="delete-event" data-event-id="${e.id}" title="Delete event">Delete</button>` : ""}
                </div>
              </div>
            `;
          }).join("") || `<div class="empty-state">No upcoming events or client meetings scheduled. Click "+ Schedule Client Meeting / Event" to add one.</div>`}
        </div>
      </div>
    </div>
  `;
}

function renderEventModal() {
  if (!state.showEventModal) return "";
  const initialDate = state.eventModalDate ? `${state.eventModalDate}T10:00` : "";
  const ev = state.eventModal || {
    title: "",
    eventType: "client",
    clientName: "",
    startsAt: initialDate,
    advanceNoticeDays: 4,
    location: "",
    description: ""
  };
  return `
    <div class="modal-backdrop" id="eventModalBackdrop">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Schedule Event or Client Meeting</h3>
          <button type="button" class="icon-btn" data-action="close-event-modal" aria-label="Close modal">✕</button>
        </div>
        <form id="modalEventForm" class="modal-form">
          <label class="field">
            <span>Event Title *</span>
            <input name="title" value="${esc(ev.title || "")}" required placeholder="e.g. Client Project Discussion or Sprint Review" />
          </label>
          <div class="form-grid-2">
            <label class="field">
              <span>Event Type</span>
              <select name="eventType">
                <option value="client" ${ev.eventType === "client" ? "selected" : ""}>🤝 Client Meeting</option>
                <option value="meeting" ${ev.eventType === "meeting" ? "selected" : ""}>👥 Internal Team Meeting</option>
                <option value="call" ${ev.eventType === "call" ? "selected" : ""}>📞 One-on-One Call</option>
                <option value="milestone" ${ev.eventType === "milestone" ? "selected" : ""}>🎯 Project Milestone</option>
              </select>
            </label>
            <label class="field">
              <span>Client Name</span>
              <input name="clientName" value="${esc(ev.clientName || "")}" placeholder="e.g. Acme Corp / Enterprise Partner" />
            </label>
          </div>
          <div class="form-grid-2">
            <label class="field">
              <span>Start Date & Time *</span>
              <input name="startsAt" type="datetime-local" value="${esc(ev.startsAt || initialDate)}" required />
            </label>
            <label class="field">
              <span>Daily Advance Alert</span>
              <select name="advanceNoticeDays">
                <option value="4" ${Number(ev.advanceNoticeDays) === 4 ? "selected" : ""}>4 Days Ahead (Daily Alert)</option>
                <option value="3" ${Number(ev.advanceNoticeDays) === 3 ? "selected" : ""}>3 Days Ahead (Daily Alert)</option>
                <option value="5" ${Number(ev.advanceNoticeDays) === 5 ? "selected" : ""}>5 Days Ahead (Daily Alert)</option>
                <option value="2" ${Number(ev.advanceNoticeDays) === 2 ? "selected" : ""}>2 Days Ahead (Daily Alert)</option>
                <option value="1" ${Number(ev.advanceNoticeDays) === 1 ? "selected" : ""}>1 Day Ahead (Daily Alert)</option>
                <option value="7" ${Number(ev.advanceNoticeDays) === 7 ? "selected" : ""}>7 Days Ahead (Weekly Alert)</option>
              </select>
            </label>
          </div>
          <label class="field">
            <span>Location / Link</span>
            <input name="location" value="${esc(ev.location || "")}" placeholder="e.g. Conference Room A or LiveKit Huddle" />
          </label>
          <label class="field">
            <span>Agenda & Description</span>
            <textarea name="description" rows="3" placeholder="Discussion points, preparation checklist, client background...">${esc(ev.description || "")}</textarea>
          </label>
          <div class="modal-actions">
            <button class="ghost-btn" type="button" data-action="close-event-modal">Cancel</button>
            <button class="primary-btn" type="submit">Schedule Event</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderAddUserModal() {
  if (!state.showAddUserModal) return "";
  const isSuper = currentUser()?.role === "super_admin";
  return `
    <div class="modal-backdrop" id="addUserModalBackdrop">
      <div class="modal-dialog">
        <div class="modal-header">
          <h3>Add New Workspace Member</h3>
          <button type="button" class="icon-btn" data-action="close-add-user-modal" aria-label="Close modal">✕</button>
        </div>
        <form id="addUserForm" class="modal-form">
          <div class="form-grid-2">
            <label class="field">
              <span>Full Name *</span>
              <input name="displayName" required placeholder="e.g. Sarah Jenkins" />
            </label>
            <label class="field">
              <span>Username (optional)</span>
              <input name="username" placeholder="e.g. sjenkins" />
            </label>
          </div>
          <label class="field">
            <span>Company Email *</span>
            <input name="email" type="email" required placeholder="name@beenco.local" />
          </label>
          <label class="field">
            <span>Initial Password *</span>
            <div style="display:flex;gap:8px;">
              <input name="password" id="newUserPasswordInput" type="text" required minlength="10" placeholder="Min 10 chars, upper + lower case and a number" style="flex:1;" />
              <button type="button" class="ghost-btn small-btn" data-action="gen-user-password" title="Generate secure random password">Generate</button>
            </div>
          </label>
          <div class="form-grid-2">
            <label class="field">
              <span>Role / Permissions *</span>
              <select name="role">
                ${isSuper ? `<option value="super_admin">👑 Super Admin (Full Root Permissions)</option>` : ""}
                <option value="admin">🛡️ Admin (Channel & Team Management)</option>
                <option value="moderator">⭐ Moderator (Content & Chat)</option>
                <option value="member" selected>👤 Member (Standard Employee)</option>
                <option value="guest">Guest</option>
              </select>
            </label>
            <label class="field">
              <span>Department</span>
              <input name="department" placeholder="e.g. Engineering, Sales, Security" />
            </label>
          </div>
          <label class="field">
            <span>Job Title</span>
            <input name="title" placeholder="e.g. Lead Systems Architect" />
          </label>
          <div class="modal-actions">
            <button class="ghost-btn" type="button" data-action="close-add-user-modal">Cancel</button>
            <button class="primary-btn" type="submit">Create Member</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function renderAttachmentPreviewModal() {
  if (!state.previewFileId) return "";
  const file = state.previewFile || fileById(state.previewFileId);
  if (!file) return "";

  const ext = file.originalName && file.originalName.includes(".") ? file.originalName.split(".").pop().toLowerCase() : "";
  const mime = String(file.mime || "").toLowerCase();
  const isImage = mime.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext);
  const isPdf = mime === "application/pdf" || ext === "pdf";
  const isAudio = mime.startsWith("audio/") || ["mp3", "wav", "ogg", "m4a", "webm"].includes(ext) || file.kind === "voice";
  const isVideo = mime.startsWith("video/") || ["mp4", "webm", "mov"].includes(ext);
  const isTextOrCode = mime.startsWith("text/") || ["txt", "md", "json", "csv", "js", "ts", "py", "html", "css", "xml", "yaml", "yml", "sql", "log"].includes(ext) || mime === "application/json";

  let bodyHtml = "";
  if (state.previewFileLoading) {
    bodyHtml = `<div class="empty-state">Loading preview...</div>`;
  } else if (isPdf) {
    bodyHtml = `<iframe src="/api/files/${esc(file.id)}/preview" class="preview-pdf-frame" title="PDF preview"></iframe>`;
  } else if (isImage) {
    bodyHtml = `<div class="preview-img-container"><img src="/api/files/${esc(file.id)}/preview" alt="${esc(file.originalName)}" /></div>`;
  } else if (isVideo) {
    bodyHtml = `<div class="preview-video-container"><video controls autoplay src="/api/files/${esc(file.id)}/preview" style="max-width:100%;max-height:75vh;border-radius:6px;"></video></div>`;
  } else if (isAudio) {
    bodyHtml = `<div class="preview-audio-container"><audio controls autoplay src="/api/files/${esc(file.id)}/preview" style="width:100%;max-width:500px;"></audio></div>`;
  } else if (isTextOrCode && state.previewFileContent !== null) {
    bodyHtml = `<pre class="preview-code-viewport"><code>${esc(state.previewFileContent)}</code></pre>`;
  } else {
    bodyHtml = `
      <div class="preview-doc-card">
        <div class="preview-doc-icon">${esc(ext.toUpperCase() || "DOC")}</div>
        <div>
          <h4 style="margin:0 0 6px 0;font-size:16px;">${esc(file.originalName)}</h4>
          <p class="muted" style="margin:0;">${formatBytes(file.sizeBytes || 0)} · ${esc(file.mime || "Document")}</p>
        </div>
        ${file.extractedText && file.extractedText !== file.originalName ? `
          <div class="preview-doc-extracted">
            <strong style="display:block;margin-bottom:4px;color:var(--text);">Document Text Summary:</strong>
            ${esc(file.extractedText.slice(0, 1200))}
          </div>
        ` : ""}
        <button class="primary-btn" data-action="download-file" data-file-id="${esc(file.id)}" style="margin-top:8px;">
          Download to View
        </button>
      </div>
    `;
  }

  return `
    <div class="attachment-preview-backdrop is-open" id="attachmentPreviewBackdrop">
      <div class="attachment-preview-dialog">
        <div class="attachment-preview-header">
          <div class="attachment-preview-title">
            <span class="file-badge">${esc(ext || "file")}</span>
            <h3 title="${esc(file.originalName)}">${esc(file.originalName)}</h3>
            <span class="attachment-preview-meta">(${formatBytes(file.sizeBytes || 0)})</span>
          </div>
          <div class="attachment-preview-actions">
            <button type="button" class="ghost-btn small-btn" data-action="download-file" data-file-id="${esc(file.id)}" title="Download file">
              Download
            </button>
            <button type="button" class="icon-btn" data-action="close-attachment-preview" aria-label="Close preview" title="Close (Esc)">
              ✕
            </button>
          </div>
        </div>
        <div class="attachment-preview-body">
          ${bodyHtml}
        </div>
      </div>
    </div>
  `;
}

async function openAttachmentPreview(fileId) {
  if (!fileId) return;
  let file = fileById(fileId);
  if (!file) {
    state.previewFileId = fileId;
    state.previewFileLoading = true;
    render();
    try {
      const res = await api(`/api/files/${fileId}`);
      if (res && res.file) {
        file = res.file;
        if (!data.files.some(f => f.id === file.id)) {
          data.files.unshift(file);
        }
      }
    } catch {}
  }
  if (!file) {
    file = { id: fileId, originalName: "Attachment", mime: "application/pdf", sizeBytes: 0, status: "available" };
  }
  state.previewFileId = fileId;
  state.previewFile = file;
  state.previewFileContent = null;
  const ext = file.originalName && file.originalName.includes(".") ? file.originalName.split(".").pop().toLowerCase() : "";
  const mime = String(file.mime || "").toLowerCase();
  const isTextOrCode = mime.startsWith("text/") || ["txt", "md", "json", "csv", "js", "ts", "py", "html", "css", "xml", "yaml", "yml", "sql", "log"].includes(ext) || mime === "application/json";

  render();

  if (isTextOrCode) {
    state.previewFileLoading = true;
    render();
    try {
      const res = await fetch(`/api/files/${fileId}/preview`, { credentials: "same-origin" });
      if (res.ok) {
        state.previewFileContent = await res.text();
      } else {
        state.previewFileContent = `[Preview could not be loaded: HTTP ${res.status}]`;
      }
    } catch (err) {
      state.previewFileContent = `[Preview error: ${err.message}]`;
    } finally {
      state.previewFileLoading = false;
      render();
    }
  }
}

function closeAttachmentPreview() {
  state.previewFileId = null;
  state.previewFile = null;
  state.previewFileContent = null;
  state.previewFileLoading = false;
  render();
}


function renderAiReportsView() {
  if (!isReportAdmin()) {
    return `
      <div class="empty-state large-empty">
        <h3>Admins only</h3>
        <p class="muted">AI weekly reports are generated and viewed by administrators.</p>
      </div>
    `;
  }
  const isSuperAdminOrAdmin = true;

  const allReports = [...(data.adminWeeklyReports || [])].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const isCombined = (r) => r.reportType === "combined";

  let displayedReports = allReports;
  if (state.aiReportSelectedUserId === "__team") {
    displayedReports = displayedReports.filter(isCombined);
  } else if (state.aiReportSelectedUserId) {
    displayedReports = displayedReports.filter((r) => r.userId === state.aiReportSelectedUserId);
  }

  let activeReport = null;
  if (state.aiReportSelectedReportId) {
    activeReport = allReports.find((r) => r.id === state.aiReportSelectedReportId);
  }
  if (!activeReport && displayedReports.length > 0) {
    activeReport = displayedReports[0];
  }

  return `
    <div class="ai-reports-layout">
      <div class="ai-reports-top-bar">
        <div class="ai-title-wrap">
          <div class="ai-badge-header">
            <span class="sparkle-icon">✨</span>
            <h2>AI Weekly Activity & Communication Reports</h2>
            <span class="ai-provider-badge">⚡ Powered by Amazon Bedrock (Qwen 3 32B)</span>
          </div>
          <p class="muted">Admin-only. A combined team report for all users is generated automatically every Monday at 9:00 AM (PKT) for the previous week. Generate a single employee's report or a fresh team report any time.</p>
        </div>
        <div class="ai-actions-wrap" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
          <div class="ai-employee-selector-box" style="display:flex;gap:8px;align-items:center;background:rgba(255,255,255,0.04);padding:6px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.1);">
            <label for="aiReportTargetUserSelect" style="font-size:12px;font-weight:600;color:#9ca3af;white-space:nowrap;">Employee:</label>
            <select id="aiReportTargetUserSelect" class="form-select" data-action="target-user-select-change" style="max-width:210px;height:32px;background:#1a1d24;color:#f3f4f6;border:1px solid #374151;border-radius:6px;padding:0 8px;font-size:12.5px;">
              ${(data.users || []).map((u) => `
                <option value="${u.id}" ${(state.aiReportTargetUserId || currentUser()?.id) === u.id ? "selected" : ""}>
                  ${esc(u.displayName)} (${esc(u.role)})
                </option>
              `).join("")}
            </select>
            <button class="primary-btn small-btn ${state.aiReportGenerating ? "is-loading" : ""}" data-action="generate-selected-user-report" ${state.aiReportGenerating ? "disabled" : ""} title="Generate a report for the selected employee (last 7 days)">
              <span class="sparkle-btn-icon">✨</span> Generate Report
            </button>
          </div>
          <button class="ghost-btn small-btn ${state.aiReportGenerating ? "is-loading" : ""}" data-action="generate-all-weekly-reports" ${state.aiReportGenerating ? "disabled" : ""} title="Generate one combined report covering every active user (last 7 days)">
            👑 ${state.aiReportGenerating === "team" ? "Generating team report..." : `Team Report: All Users (${(data.users || []).length})`}
          </button>
        </div>
      </div>

      ${isSuperAdminOrAdmin ? `
        <div class="ai-user-filter-bar">
          <span class="filter-label">Filter Reports:</span>
          <button class="chip ${!state.aiReportSelectedUserId ? "is-active" : ""}" data-action="filter-ai-report-user" data-user-id="">All Reports (${allReports.length})</button>
          <button class="chip ${state.aiReportSelectedUserId === "__team" ? "is-active" : ""}" data-action="filter-ai-report-user" data-user-id="__team">👥 Team Reports (${allReports.filter(isCombined).length})</button>
          ${(data.users || []).map((u) => {
            const count = allReports.filter((r) => r.userId === u.id).length;
            return `
              <button class="chip ${state.aiReportSelectedUserId === u.id ? "is-active" : ""}" data-action="filter-ai-report-user" data-user-id="${u.id}">
                ${initials(u.displayName)} ${esc(u.displayName)} (${count})
              </button>
            `;
          }).join("")}
        </div>
      ` : ""}

      <div class="ai-reports-split-view">
        <aside class="ai-reports-sidebar">
          <div class="sidebar-header-row" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;padding-right:4px;">
            <h3 class="sidebar-heading" style="margin:0;">Reports Archive (${displayedReports.length})</h3>
            ${isSuperAdminOrAdmin && allReports.length > 0 ? `
              <button class="ghost-btn tiny-btn danger-btn" data-action="clear-all-ai-reports" style="border-color:rgba(255,92,92,0.4);color:#ff7b7b;font-size:11px;padding:2px 6px;" title="Delete all reports from archive">
                Clear All
              </button>
            ` : ""}
          </div>
          <div class="ai-reports-list">
            ${displayedReports.map((r) => {
              const combined = isCombined(r);
              const targetUser = combined
                ? { displayName: "All team members" }
                : userById(r.userId) || { displayName: "User", username: "user" };
              const isSelected = activeReport?.id === r.id;
              const metrics = r.metrics || {};
              return `
                <div class="ai-report-nav-item ${isSelected ? "is-active" : ""}" data-action="select-ai-report" data-report-id="${r.id}">
                  <div class="nav-item-top">
                    <span class="avatar tiny">${combined ? "👥" : initials(targetUser.displayName)}</span>
                    <strong class="target-name">${esc(targetUser.displayName)}</strong>
                    <time class="report-time">${formatDateOnly(r.createdAt)}</time>
                  </div>
                  <h4 class="report-nav-title">${esc(r.title)}</h4>
                  <div class="report-meta-pills">
                    <span class="meta-pill">💬 ${metrics.totalMessages || 0} msgs</span>
                    ${combined ? `
                      <span class="meta-pill">👥 ${metrics.activeUsers || 0}/${metrics.usersCovered || 0} active</span>
                    ` : `
                      <span class="meta-pill">👥 ${r.collaborators?.length || 0} chats</span>
                    `}
                    <span class="meta-pill"># ${r.channelsInvolved?.length || 0} chans</span>
                    ${metrics.aiModel ? `<span class="meta-pill ai-model-pill">⚡ ${esc(metrics.aiModel)}</span>` : ""}
                  </div>
                  <div class="delivery-status" style="display:flex;align-items:center;justify-content:space-between;">
                    <span class="status-check">${r.trigger === "scheduled" ? "🗓 Scheduled · Monday run" : "✓ Generated by admin"}</span>
                    ${isSuperAdminOrAdmin ? `
                      <button class="icon-btn tiny-icon" data-action="delete-ai-report" data-report-id="${r.id}" title="Delete report" style="color:#ef4444;opacity:0.75;padding:2px 4px;font-size:12px;">🗑️</button>
                    ` : ""}
                  </div>
                </div>
              `;
            }).join("") || `
              <div class="empty-state">
                <p>No reports in this archive.</p>
                <span class="muted">Select an employee above to generate an executive activity report.</span>
              </div>
            `}
          </div>
        </aside>

        <main class="ai-report-viewer">
          ${activeReport ? renderAiReportDetails(activeReport) : state.aiReportSelectedUserId === "__team" ? `
            <div class="empty-state large-empty">
              <span class="big-sparkle">👥</span>
              <h3>No Team Reports Yet</h3>
              <p class="muted">The combined report for all users is generated every Monday at 9:00 AM (PKT). Use "Team Report: All Users" to generate one now.</p>
            </div>
          ` : (state.aiReportSelectedUserId ? `
            <div class="empty-state large-empty">
              <span class="big-sparkle">✨</span>
              <h3>No Reports Yet for ${esc(userById(state.aiReportSelectedUserId)?.displayName || "Employee")}</h3>
              <p class="muted">No weekly activity reports have been generated for this employee yet. You can synthesize an AI executive briefing using Amazon Bedrock Qwen right now.</p>
              <button class="primary-btn ${state.aiReportGenerating ? "is-loading" : ""}" data-action="generate-specific-user-report" data-user-id="${state.aiReportSelectedUserId}" ${state.aiReportGenerating ? "disabled" : ""} style="margin-top:14px;">
                <span class="sparkle-btn-icon">✨</span> Generate Weekly Report for ${esc(userById(state.aiReportSelectedUserId)?.displayName || "Employee")}
              </button>
            </div>
          ` : `
            <div class="empty-state large-empty">
              <span class="big-sparkle">✨</span>
              <h3>No Report Selected</h3>
              <p class="muted">Select an employee from the dropdown or filter above to generate an executive activity briefing.</p>
            </div>
          `)}
        </main>
      </div>
    </div>
  `;
}

function renderCombinedReportDetails(r) {
  const metrics = r.metrics || {};
  const users = metrics.users || [];
  return `
    <article class="ai-report-paper">
      <header class="report-paper-header">
        <div class="paper-title-row">
          <div style="flex:1;">
            <div class="paper-badge-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span class="executive-tag">Team Report · All Users</span>
              <span class="ai-model-tag">⚡ ${esc(metrics.aiProvider || "Amazon Bedrock")}: ${esc(metrics.aiModel || "")}</span>
              <button class="ghost-btn small-btn danger-btn" data-action="delete-ai-report" data-report-id="${r.id}" style="margin-left:auto;border-color:#ff5c5c;color:#ff5c5c;padding:3px 10px;font-size:12px;" title="Delete this report">
                🗑️ Delete Report
              </button>
            </div>
            <h2 class="paper-title">${esc(r.title)}</h2>
            <p class="paper-period">Week Coverage: <strong>${formatDateTime(r.weekStart)}</strong> to <strong>${formatDateTime(r.weekEnd)}</strong></p>
          </div>
        </div>
        <div class="delivery-banner">
          <div class="delivery-pill admin-delivered">
            <span class="ack-check-icon">${r.trigger === "scheduled" ? "🗓" : "👑"}</span>
            <span>${r.trigger === "scheduled" ? "Scheduled Monday report" : `Generated by ${esc(userById(r.generatedBy)?.displayName || "admin")}`}: <strong>${formatDateTime(r.createdAt)}</strong></span>
          </div>
        </div>
      </header>

      <section class="ai-metrics-grid">
        <div class="metric-card">
          <span class="metric-icon">👥</span>
          <strong class="metric-value">${metrics.activeUsers || 0}/${metrics.usersCovered || 0}</strong>
          <span class="metric-label">Active Members</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">💬</span>
          <strong class="metric-value">${metrics.totalMessages || 0}</strong>
          <span class="metric-label">Messages Sent</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">📢</span>
          <strong class="metric-value">${metrics.channelsCount || 0}</strong>
          <span class="metric-label">Active Channels</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">📋</span>
          <strong class="metric-value">${(metrics.tasksCount || 0) + (metrics.filesCount || 0)}</strong>
          <span class="metric-label">Tasks & Files</span>
        </div>
      </section>

      ${users.length ? `
        <section class="paper-section">
          <h3 class="paper-section-title">🔎 Open an Employee's Own Reports</h3>
          <div class="report-meta-pills">
            ${users.map((u) => `<button class="chip" data-action="filter-ai-report-user" data-user-id="${esc(u.id)}">${initials(u.name)} ${esc(u.name)} · ${u.messages} msgs</button>`).join("")}
          </div>
        </section>
      ` : ""}

      <section class="paper-section">
        <h3 class="paper-section-title">📝 Team Breakdown by Employee</h3>
        <div class="report-prose-content">
          ${renderMarkdown(r.summary)}
        </div>
      </section>
    </article>
  `;
}

function renderAiReportDetails(r) {
  if (r.reportType === "combined") return renderCombinedReportDetails(r);
  const isSuperAdminOrAdmin = isReportAdmin();
  const targetUser = userById(r.userId) || { displayName: "Employee", username: "user", role: "member" };
  const metrics = r.metrics || {};
  const collaborators = r.collaborators || [];
  const channels = r.channelsInvolved || [];

  return `
    <article class="ai-report-paper">
      <header class="report-paper-header">
        <div class="paper-title-row">
          <div style="flex:1;">
            <div class="paper-badge-row" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
              <span class="executive-tag">AI Executive Briefing</span>
              <span class="ai-model-tag">⚡ ${esc(metrics.aiProvider || "Amazon Bedrock")}: ${esc(metrics.aiModel || "qwen.qwen3-32b-v1:0")}</span>
              ${isSuperAdminOrAdmin ? `
                <button class="ghost-btn small-btn danger-btn" data-action="delete-ai-report" data-report-id="${r.id}" style="margin-left:auto;border-color:#ff5c5c;color:#ff5c5c;padding:3px 10px;font-size:12px;" title="Delete this report">
                  🗑️ Delete Report
                </button>
              ` : ""}
            </div>
            <h2 class="paper-title">${esc(r.title)}</h2>
            <p class="paper-period">Week Coverage: <strong>${esc(r.weekStart)}</strong> to <strong>${esc(r.weekEnd)}</strong></p>
          </div>
          <div class="user-badge-card">
            <span class="avatar medium">${initials(targetUser.displayName)}</span>
            <div>
              <strong>${esc(targetUser.displayName)}</strong>
              <span class="muted">${esc(targetUser.title || targetUser.role)}</span>
            </div>
          </div>
        </div>
        <div class="delivery-banner">
          <div class="delivery-pill admin-delivered">
            <span class="ack-check-icon">👑</span>
            <span>Admin-only · Generated${r.generatedBy ? ` by ${esc(userById(r.generatedBy)?.displayName || "admin")}` : ""}: <strong>${formatDateTime(r.createdAt)}</strong></span>
          </div>
        </div>
      </header>

      <section class="ai-metrics-grid">
        <div class="metric-card">
          <span class="metric-icon">💬</span>
          <strong class="metric-value">${metrics.totalMessages || 0}</strong>
          <span class="metric-label">Messages Sent</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">👥</span>
          <strong class="metric-value">${collaborators.length || metrics.collaboratorsCount || 0}</strong>
          <span class="metric-label">1-on-1 Collaborators</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">📢</span>
          <strong class="metric-value">${channels.length || metrics.channelsCount || 0}</strong>
          <span class="metric-label">Channels Participated</span>
        </div>
        <div class="metric-card">
          <span class="metric-icon">⭐</span>
          <strong class="metric-value">${esc(metrics.topCollaborator || "None")}</strong>
          <span class="metric-label">Top Collaborator</span>
        </div>
      </section>

      ${collaborators.length > 0 ? `
        <section class="paper-section">
          <h3 class="paper-section-title">🤝 1-on-1 Direct Conversations & Collaborators</h3>
          <div class="collaborators-grid">
            ${collaborators.map((c) => `
              <div class="collaborator-card">
                <div class="collab-header">
                  <span class="avatar small">${initials(c.name)}</span>
                  <div>
                    <strong>${esc(c.name)}</strong>
                    <span class="collab-count">${c.messages ?? c.messageCount ?? 0} messages</span>
                  </div>
                </div>
                ${c.topics?.length ? `
                  <div class="collab-topics">
                    ${c.topics.map((t) => `<span class="collab-topic-tag">${esc(t)}</span>`).join(" ")}
                  </div>
                ` : ""}
                ${c.snippet ? `<p class="collab-snippet">"${esc(c.snippet)}..."</p>` : ""}
              </div>
            `).join("")}
          </div>
        </section>
      ` : ""}

      ${channels.length > 0 ? `
        <section class="paper-section">
          <h3 class="paper-section-title">📢 Group & Channel Contributions</h3>
          <div class="channels-summary-list">
            ${channels.map((ch) => `
              <div class="channel-summary-row">
                <span class="channel-hash">#</span>
                <strong class="channel-name">${esc(ch.name)}</strong>
                <span class="contrib-badge">${ch.count ?? ch.messageCount ?? 0} contributions</span>
                ${ch.topics?.length ? `<span class="channel-topics">Topics: ${ch.topics.map((t) => `<span class="topic-tag">${esc(t)}</span>`).join(" ")}</span>` : ""}
              </div>
            `).join("")}
          </div>
        </section>
      ` : ""}

      <section class="paper-section">
        <h3 class="paper-section-title">📝 Executive Synthesis & Weekly Breakdown</h3>
        <div class="report-prose-content">
          ${renderMarkdown(r.summary)}
        </div>
      </section>
    </article>
  `;
}

function renderBriefingView() {
  const isSuperAdminOrAdmin = isAdmin();
  const isGenerating = state.briefingGenerating || false;
  return `
    <div class="briefing-view-layout" style="display:flex;flex-direction:column;gap:12px;padding:12px 16px;height:100%;">
      <div class="briefing-header-bar" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;background:#0d0d14;border:1px solid #1f1f33;border-radius:8px;padding:10px 16px;">
        <div>
          <div style="font-weight:700;font-size:0.95rem;color:#00e5ff;letter-spacing:0.04em;display:flex;align-items:center;gap:8px;">
            <span>⚡</span> ABDUTTAYYEB BLOCKCHAIN INTELLIGENCE RADAR
          </div>
          <div style="font-size:0.75rem;color:#94a3b8;margin-top:2px;">
            Multi-source institutional synthesis across 19 sectors · Automated daily generation @ 09:00 AM (Bedrock Qwen 32B, Exa, Tavily, DefiLlama)
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:10px;">
          ${isSuperAdminOrAdmin ? `
            <button class="ghost-btn small-btn" data-action="trigger-briefing" ${isGenerating ? "disabled" : ""} style="border-color:#00ff9d;color:#00ff9d;display:flex;align-items:center;gap:6px;cursor:pointer;">
              ${isGenerating ? `<span>⏳ Synthesizing Briefing...</span>` : `<span>⚡ Generate Briefing Now</span>`}
            </button>
          ` : ""}
          <a href="/blockchain-briefing-live.html" target="_blank" rel="noopener noreferrer" class="ghost-btn small-btn" style="text-decoration:none;border-color:#00e5ff;color:#00e5ff;display:flex;align-items:center;gap:4px;">
            <span>↗ Open Fullscreen Terminal</span>
          </a>
        </div>
      </div>
      <iframe id="briefingIframe" src="/blockchain-briefing-live.html" style="flex:1;min-height:calc(100vh - 210px);width:100%;border:1px solid #1f1f33;border-radius:8px;background:#07070a;" title="Blockchain Briefing Terminal"></iframe>
    </div>
  `;
}

function renderPeoplePanel() {
  const users = [...data.users].sort((a, b) => Number(b.isOnline) - Number(a.isOnline) || a.displayName.localeCompare(b.displayName));
  const channel = activeChannel();
  const canManageCurrentGroup = channel.type === "private" && isChannelOwner(channel);
  const isSuper = currentUser()?.role === "super_admin";
  const canAddMembers = isSuper || currentUser()?.role === "admin";

  const search = (state.peopleSearch || "").trim().toLowerCase();
  const visibleUsers = users.filter((u) => {
    if (!search) return true;
    return (u.displayName && u.displayName.toLowerCase().includes(search))
      || (u.email && u.email.toLowerCase().includes(search))
      || (u.department && u.department.toLowerCase().includes(search))
      || (u.title && u.title.toLowerCase().includes(search))
      || (u.role && u.role.toLowerCase().includes(search));
  });

  return `
    <div class="panel-stack">
      ${canManageCurrentGroup ? renderGroupMembersManager() : ""}
      <section class="presence-overview">
        <div><strong>${onlineUsers().length}</strong><span>online</span></div>
        <div><strong>${data.users.length}</strong><span>people</span></div>
        <div><strong>${activeChannelMembers().length}</strong><span>in channel</span></div>
      </section>
      <div class="inline-actions people-toolbar" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px;">
        ${canAddMembers ? `
          <button class="primary-btn small-btn" data-action="open-add-user-modal" style="font-weight:700;">+ Add Member</button>
        ` : ""}
        <button class="ghost-btn small-btn" data-action="open-panel" data-panel="group">${canManageCurrentGroup ? "Manage group / New group" : "New private group"}</button>
      </div>
      <div class="people-search-wrap">
        <span class="people-search-icon">🔍</span>
        <input type="text" class="people-search-input" data-action="filter-people" placeholder="Search members by name, title, department..." value="${esc(state.peopleSearch || "")}" />
        ${state.peopleSearch ? `<button class="clear-people-search" data-action="clear-people-search" title="Clear search">✕</button>` : ""}
      </div>
      <div class="people-list">
        ${visibleUsers.length > 0 ? visibleUsers.map((user) => {
          const isCurrent = user.id === currentUser()?.id;
          const roleBadge = user.role === "super_admin"
            ? `<span class="user-role-badge super_admin">👑 Super Admin</span>`
            : (user.role === "admin"
                ? `<span class="user-role-badge admin">🛡️ Admin</span>`
                : (user.role === "moderator" ? `<span class="user-role-badge moderator">⭐ Mod</span>` : ""));
          const subtitle = [user.title, user.department].filter(Boolean).join(" · ");
          return `
            <div class="people-card" data-user-id="${user.id}">
              <div class="people-card-header">
                <div class="people-main">
                  <span class="avatar small">${initials(user.displayName)}</span>
                  <div class="people-info">
                    <div class="people-name-line">
                      <strong class="people-name" title="${esc(user.displayName)}">${esc(user.displayName)}</strong>
                      ${roleBadge}
                    </div>
                    ${subtitle ? `<span class="people-subtitle" title="${esc(subtitle)}">${esc(subtitle)}</span>` : ""}
                    <div class="people-presence">
                      <span class="presence-dot ${esc(user.presence)}"></span>
                      <span>${esc(presenceLabel(user))}</span>
                    </div>
                  </div>
                </div>
                <div class="people-action">
                  ${!isCurrent ? `<button class="ghost-btn small-btn" data-action="start-dm" data-user-id="${user.id}">Message</button>` : `<span class="you-badge">you</span>`}
                </div>
              </div>
              ${isSuper && !isCurrent ? `
                <div class="people-card-role-bar">
                  <div style="display:flex;align-items:center;gap:6px;">
                    <span class="role-bar-label">Role:</span>
                    <div class="role-select-wrapper">
                      <select class="role-select" data-action="role-change" data-user-id="${user.id}" title="Change role permissions">
                        <option value="super_admin" ${user.role === "super_admin" ? "selected" : ""}>👑 Super Admin</option>
                        <option value="admin" ${user.role === "admin" ? "selected" : ""}>🛡️ Admin</option>
                        <option value="moderator" ${user.role === "moderator" ? "selected" : ""}>⭐ Moderator</option>
                        <option value="member" ${(!user.role || user.role === "member") ? "selected" : ""}>👤 Member</option>
                        <option value="guest" ${user.role === "guest" ? "selected" : ""}>Guest</option>
                      </select>
                    </div>
                  </div>
                  <button class="ghost-btn tiny-btn danger-btn" data-action="remove-workspace-member" data-user-id="${user.id}" data-user-name="${esc(user.displayName)}" title="Permanently delete ${esc(user.displayName)} from workspace">
                    🗑️ Delete User
                  </button>
                </div>
              ` : ""}
            </div>
          `;
        }).join("") : `<div class="empty-state" style="padding:24px;text-align:center;">No members found matching "${esc(state.peopleSearch)}"</div>`}
      </div>
    </div>
  `;
}

function renderYouPanel() {
  const user = currentUser();
  return `
    <div class="panel-stack">
      <div class="admin-row">
        <div class="people-main">
          <span class="avatar">${initials(user.displayName)}</span>
          <div class="people-info">
            <strong class="people-name">${esc(user.displayName)}</strong>
            <span class="people-subtitle">${esc(user.email)} - ${esc(user.provider)}</span>
          </div>
        </div>
        <span class="status available">${esc(user.role)}</span>
      </div>
      <div class="people-presence"><span class="presence-dot ${user.isOnline ? "online" : ""}"></span><span>${user.isOnline ? "Online now" : "Offline"}</span></div>
      <label class="field"><span>Status</span><input data-action="status-change" value="${esc(user.statusText || "")}" placeholder="Set a status" /></label>
      <label class="field"><span>Title</span><input data-action="title-change" value="${esc(user.title || "")}" placeholder="Job title" /></label>
      <label class="field"><span>Department</span><input data-action="department-change" value="${esc(user.department || "")}" placeholder="Department" /></label>
      <button class="ghost-btn" data-action="logout">Logout</button>
    </div>
  `;
}

function renderAdminPanel() {
  if (!isAdmin()) return `<div class="empty-state">Admin or moderator role required.</div>`;
  const channel = activeChannel();
  return `
    <div class="panel-stack">
      <form class="panel-band" id="channelForm">
        <h3>Create channel or group</h3>
        <label class="field"><span>Name</span><input name="name" required placeholder="project-alpha" /></label>
        <label class="field"><span>Type</span><select name="type"><option value="public">public</option><option value="private">private</option></select></label>
        <label class="field"><span>Topic</span><input name="topic" placeholder="Short channel purpose" /></label>
        <label class="field"><span>Description</span><textarea name="description" placeholder="Who should use this space?"></textarea></label>
        <label class="field"><span>Private members</span>${renderMemberPicker()}</label>
        <button class="primary-btn" type="submit">Create channel</button>
      </form>
      <div class="panel-band"><h3>Users</h3>${data.users.map((user) => `
        <div class="admin-row">
          <div><strong>${esc(user.displayName)}</strong><span>${esc(user.email)} - ${esc(user.provider)}</span></div>
          <select data-action="role-change" data-user-id="${user.id}">${(currentUser()?.role === "super_admin" ? ["super_admin", "admin", "moderator", "member", "guest"] : ["admin", "moderator", "member", "guest"]).map((role) => `<option value="${role}" ${user.role === role ? "selected" : ""}>${role}</option>`).join("")}</select>
        </div>
      `).join("")}</div>
      <form class="panel-band" id="retentionForm">
        <h3>Retention</h3>
        <label class="field"><span>Global days</span><input name="globalDays" type="number" min="1" value="${data.settings.retentionGlobalDays || 365}" /></label>
        <label class="field"><span>Active channel legal hold</span><select name="legalHold"><option value="false" ${!channel.legalHold ? "selected" : ""}>off</option><option value="true" ${channel.legalHold ? "selected" : ""}>on</option></select></label>
        <button class="primary-btn" type="submit">Save retention</button>
      </form>
      <div class="panel-band"><h3>Feature flags</h3>${Object.entries(data.featureFlags || {}).map(([key, value]) => `<label class="admin-row"><span><strong>${esc(key)}</strong><span>${value ? "enabled" : "disabled"}</span></span><input type="checkbox" data-action="flag-change" data-flag="${esc(key)}" ${value ? "checked" : ""} /></label>`).join("")}</div>
      <form class="panel-band" id="announcementForm">
        <h3>Announcement</h3>
        <label class="field"><span>Title</span><input name="title" required placeholder="Broadcast title" /></label>
        <label class="field"><span>Body</span><textarea name="body" required placeholder="Broadcast body"></textarea></label>
        <button class="primary-btn" type="submit">Post announcement</button>
      </form>
      <div class="panel-band"><h3>Moderation</h3><div class="inline-actions"><button class="danger-btn" data-action="lock-channel">${channel.locked ? "Unlock" : "Lock"} channel</button><button class="ghost-btn" data-action="export-channel">Export channel</button>${canDeleteChannel(channel) ? `<button class="danger-btn" data-action="delete-channel" data-channel-id="${channel.id}">Delete channel</button>` : ""}</div></div>
    </div>
  `;
}

function renderAuditPanel() {
  return `<div class="panel-stack"><div class="inline-actions"><span class="status available">${data.audit.length} events</span><span class="status">PostgreSQL</span><button class="ghost-btn small-btn" data-action="export-audit">Export</button></div>${data.audit.map((event) => `<div class="audit-row"><code>${esc(event.action)}</code><span>${formatDateTime(event.createdAt)} - ${esc(userById(event.actorId).displayName)} - ${esc(event.targetType)}:${esc(event.targetId)}</span><span class="muted">${esc(JSON.stringify(event.metadata))}</span></div>`).join("") || `<div class="empty-state">No audit events yet.</div>`}</div>`;
}

function renderMobileTabs() {
  return `
    <nav class="mobile-tabs" aria-label="Mobile navigation">
      <button data-action="toggle-sidebar">Channels</button>
      <button data-action="open-panel" data-panel="files" class="${state.activePanel === "files" ? "is-active" : ""}">Files</button>
      <button data-action="open-panel" data-panel="people" class="${state.activePanel === "people" ? "is-active" : ""}">People</button>
      <button data-action="open-panel" data-panel="search" class="${state.activePanel === "search" ? "is-active" : ""}">Search</button>
      <button data-action="open-panel" data-panel="you" class="${state.activePanel === "you" ? "is-active" : ""}">You</button>
    </nav>
  `;
}

function afterRender() {
  const scroll = $("#messageScroll");
  if (scroll) {
    const mode = state.workspaceMode || "home";
    const isSpecialView = ["activity", "files", "threads", "calendar", "reports", "briefing", "xmentions"].includes(mode);

    if (isSpecialView) {
      if (!state.viewScroll) state.viewScroll = {};
      const savedTop = state.viewScroll[mode] || 0;
      scroll.scrollTop = savedTop;
      scroll.onscroll = () => {
        if (!state.viewScroll) state.viewScroll = {};
        state.viewScroll[mode] = scroll.scrollTop;
      };
    } else {
      const channelChanged = state.messageScroll.channelId !== state.activeChannelId;
      const restore = state.messageScroll.pendingRestore;
      if (restore && restore.channelId === state.activeChannelId) {
        if (restore.mode === "bottom") {
          scroll.scrollTop = scroll.scrollHeight;
        } else {
          const maxTop = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
          scroll.scrollTop = Math.min(restore.top, maxTop);
        }
      } else if (channelChanged) {
        scroll.scrollTop = scroll.scrollHeight;
      } else if (state.messageScroll.nearBottom) {
        scroll.scrollTop = scroll.scrollHeight;
      } else {
        const maxTop = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
        scroll.scrollTop = Math.min(state.messageScroll.top, maxTop);
      }
      state.messageScroll.pendingRestore = null;
      state.messageScroll.channelId = state.activeChannelId;
      state.messageScroll.top = scroll.scrollTop;
      state.messageScroll.nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96;
      scroll.onscroll = () => {
        state.messageScroll.top = scroll.scrollTop;
        state.messageScroll.nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 96;
      };
    }
  }

  const sidebar = $(".sidebar-scroll");
  if (sidebar && typeof state.sidebarScrollTop === "number") {
    sidebar.scrollTop = state.sidebarScrollTop;
  }
  const details = $(".details-body");
  if (details && typeof state.detailsScrollTop === "number") {
    details.scrollTop = state.detailsScrollTop;
  }
  if (pendingFocus) {
    const element = $(`[data-action="${pendingFocus.action}"]`);
    if (element) {
      element.focus();
      if (element.setSelectionRange) element.setSelectionRange(pendingFocus.cursor, pendingFocus.cursor);
    }
    pendingFocus = null;
  }
  renderToasts();
}

function renderToasts() {
  const region = $("#toasts");
  if (!region) return;
  region.innerHTML = state.ui.toasts.map((item) => {
    if (item.isXStyle) {
      return `
        <div class="x-toast ${item.isUrgent ? "x-toast-urgent" : (item.isTag ? "x-toast-tag" : "")}" data-toast-id="${esc(item.id)}">
          <div class="x-toast-header">
            <span class="avatar tiny">${initials(item.authorName || "Beenco")}</span>
            <div class="x-toast-meta">
              <strong>${esc(item.authorName || "Alert")}</strong>
              ${item.authorHandle ? `<span class="x-handle">@${esc(item.authorHandle)}</span>` : ""}
              ${item.isUrgent ? `<span class="urgent-badge-pill">🚨 URGENT</span>` : (item.tag ? `<span class="x-tag-badge">${esc(item.tag)}</span>` : "")}
            </div>
            <button class="x-toast-close" data-action="close-toast" data-toast-id="${esc(item.id)}">&times;</button>
          </div>
          <div class="x-toast-body">${esc(item.body || "")}</div>
          <div class="x-toast-footer">
            ${item.channelName ? `<span class="x-toast-channel">in #${esc(item.channelName)}</span>` : ""}
            ${item.messageId && isAdmin() ? `
              <button class="btn-press-ack urgent-toast-btn" data-action="acknowledge-message" data-message-id="${esc(item.messageId)}">
                <span class="ack-check-icon">✓</span> PRESS TO ACKNOWLEDGE
              </button>
            ` : ""}
            ${item.channelId ? `<button class="ghost-btn small-btn x-jump-btn" data-action="jump-message" data-channel-id="${esc(item.channelId)}" data-message-id="${esc(item.messageId || "")}">Jump &rarr;</button>` : ""}
          </div>
        </div>
      `;
    }
    return `<div class="toast"><strong>${esc(item.title)}</strong>${item.body ? `<p class="muted">${esc(item.body)}</p>` : ""}</div>`;
  }).join("");
}

function resetNotificationAlertState() {
  state.seenNoteIds = null;
  state.seenMessageIds = null;
  state.ui.toasts = [];
  state.lastUrgentPingTime = 0;
  state.lastStalePingTime = 0;
  state.dismissedPendingPingUntil = null;
  state.simulateStalePing = false;
}

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (event.target.id === "loginForm") {
      state.authError = "";
      state.serverHint = "";
      const form = new FormData(event.target);
      await api("/api/auth/login", { method: "POST", body: { email: form.get("email"), password: form.get("password") } });
      resetNotificationAlertState();
      await refresh();
      toast("Signed in", "Session restored from database.");
    }
    if (event.target.id === "signupForm") {
      state.authError = "";
      state.serverHint = "";
      const form = new FormData(event.target);
      await api("/api/auth/signup", { method: "POST", body: { displayName: form.get("displayName"), email: form.get("email"), password: form.get("password") } });
      resetNotificationAlertState();
      await refresh();
      toast("Account created", "Your user is stored in the portal database.");
    }
    if (event.target.id === "addUserForm") {
      const form = new FormData(event.target);
      const payload = await api("/api/users", {
        method: "POST",
        body: {
          displayName: form.get("displayName"),
          username: form.get("username") || undefined,
          email: form.get("email"),
          password: form.get("password"),
          role: form.get("role") || "member",
          department: form.get("department") || undefined,
          title: form.get("title") || undefined
        }
      });
      state.showAddUserModal = false;
      await refresh();
      toast("Member created", `${payload.user?.displayName || "User"} (${payload.user?.role || "member"}) added successfully.`);
    }
    if (event.target.id === "threadReplyForm") {
      const form = event.target;
      const reply = new FormData(form).get("reply")?.toString().trim();
      if (reply) {
        form.reset();
        await createMessage(reply, [], state.selectedThreadId);
      }
    }
    if (event.target.id === "remoteAssistForm") {
      const form = new FormData(event.target);
      await api("/api/remote-assist", { method: "POST", body: { targetUserId: form.get("targetUserId"), targetHost: form.get("targetHost"), reason: form.get("reason") } });
      await refresh();
      state.activePanel = "remote";
      toast("Remote access requested", userById(form.get("targetUserId")).displayName);
    }
    if (event.target.dataset.action === "remote-assist-response") {
      const form = new FormData(event.target);
      await api(`/api/remote-assist/${event.target.dataset.sessionId}/respond`, { method: "POST", body: { response: "accepted", targetHost: form.get("targetHost") } });
      await refresh();
      state.activePanel = "remote";
      toast("Remote access approved", "RDP details are now available to the requester.");
    }
    if (event.target.id === "eventForm") {
      const form = new FormData(event.target);
      const start = new Date(form.get("startsAt"));
      await api("/api/events", { method: "POST", body: { title: form.get("title"), startsAt: start.toISOString(), location: form.get("location"), description: form.get("description") } });
      await refresh();
      toast("Event created", form.get("title"));
    }
    if (event.target.id === "groupForm") {
      const form = new FormData(event.target);
      const memberIds = Array.from(event.target.querySelectorAll('input[name="memberIds"]:checked')).map((input) => input.value);
      const payload = await api("/api/channels", { method: "POST", body: { name: form.get("name"), type: "private", topic: form.get("topic"), description: form.get("description"), memberIds } });
      await refresh();
      state.activePanel = "thread";
      selectChannel(payload.channelId);
      toast("Private group created", form.get("name"));
    }
    if (event.target.id === "addGroupMemberForm") {
      const form = new FormData(event.target);
      const channelId = event.target.dataset.channelId;
      await api(`/api/channels/${channelId}/members`, { method: "POST", body: { userId: form.get("userId") } });
      await refresh();
      state.activePanel = "group";
      toast("Member added", userById(form.get("userId")).displayName);
    }
    if (event.target.id === "taskForm") {
      const form = new FormData(event.target);
      await api("/api/tasks", {
        method: "POST",
        body: {
          channelId: activeChannel().id,
          title: form.get("title"),
          subject: form.get("subject"),
          referenceLinks: form.get("referenceLinks"),
          deadline: form.get("deadline"),
          tags: form.get("tags"),
          assigneeId: form.get("assigneeId")
        }
      });
      await refresh({ preserveMessages: false, pinToBottom: true });
      state.activePanel = "tasks";
      toast("Task created", form.get("title"));
    }
    if (event.target.id === "channelForm") {
      const form = new FormData(event.target);
      const memberIds = Array.from(event.target.querySelectorAll('input[name="memberIds"]:checked')).map((input) => input.value);
      await api("/api/channels", { method: "POST", body: { name: form.get("name"), type: form.get("type"), topic: form.get("topic"), description: form.get("description"), memberIds } });
      await refresh();
      toast("Channel created", form.get("name"));
    }
    if (event.target.id === "retentionForm") {
      const form = new FormData(event.target);
      await api("/api/admin/retention", { method: "PUT", body: { globalDays: Number(form.get("globalDays")), channelId: activeChannel().id, legalHold: form.get("legalHold") === "true" } });
      await refresh();
      toast("Retention saved", "Policy is persisted.");
    }
    if (event.target.id === "announcementForm") {
      const form = new FormData(event.target);
      await api("/api/announcements", { method: "POST", body: { title: form.get("title"), body: form.get("body") } });
      await refresh();
      toast("Announcement posted", form.get("title"));
    }
    if (event.target.id === "employeeForm") {
      const form = new FormData(event.target);
      await api("/api/hr/employees", { method: "POST", body: { fullName: form.get("fullName"), email: form.get("email"), department: form.get("department"), designation: form.get("designation"), joiningDate: form.get("joiningDate") } });
      await refresh();
      toast("Employee created", form.get("fullName"));
    }
    if (event.target.id === "assetForm") {
      const form = new FormData(event.target);
      await api("/api/hr/assets", { method: "POST", body: { assetTag: form.get("assetTag"), category: form.get("category"), model: form.get("model"), serial: form.get("serial") } });
      await refresh();
      toast("Asset created", form.get("assetTag"));
    }
    if (event.target.id === "assignAssetForm") {
      const form = new FormData(event.target);
      await api(`/api/hr/assets/${form.get("assetId")}/assign`, { method: "POST", body: { employeeId: form.get("employeeId"), expectedReturn: form.get("expectedReturn") } });
      await refresh();
      toast("Asset assigned", "Assignment history updated.");
    }
    if (event.target.id === "leaveRequestForm") {
      const form = new FormData(event.target);
      await api("/api/hr/leave", { method: "POST", body: { employeeId: form.get("employeeId"), leaveType: form.get("leaveType"), startDate: form.get("startDate"), endDate: form.get("endDate"), days: Number(form.get("days") || 1) } });
      await refresh();
      toast("Leave requested", "Approval queue updated.");
    }
    if (event.target.id === "attendanceCorrectionForm") {
      const form = new FormData(event.target);
      await api("/api/hr/attendance/corrections", { method: "POST", body: { employeeId: form.get("employeeId"), date: form.get("date"), status: form.get("status"), reason: form.get("reason") } });
      await refresh();
      toast("Attendance corrected", form.get("date"));
    }
    if (event.target.id === "eventForm" || event.target.id === "modalEventForm") {
      const form = new FormData(event.target);
      await api("/api/events", {
        method: "POST",
        body: {
          title: form.get("title"),
          startsAt: form.get("startsAt"),
          location: form.get("location"),
          description: form.get("description"),
          clientName: form.get("clientName"),
          eventType: form.get("eventType") || "meeting",
          advanceNoticeDays: Number(form.get("advanceNoticeDays") || 4)
        }
      });
      state.showEventModal = false;
      state.eventModal = null;
      await refresh();
      toast("Event scheduled", form.get("clientName") ? `Client meeting for ${form.get("clientName")} scheduled with advance alerts` : form.get("title"));
    }
  } catch (error) {
    state.serverHint = "";
    state.authError = error.message;
    toast("Action failed", error.message);
    render();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.form && event.target.form.id === "modalEventForm") {
    if (!state.eventModal) state.eventModal = {};
    state.eventModal[event.target.name] = event.target.value;
  }
  const action = event.target.dataset.action;
  if (action === "composer-draft") {
    state.composerDrafts[state.activeChannelId] = event.target.value;
    queueTyping(Boolean(event.target.value.trim()));
  }
  if (action === "edit-draft") state.editDraft = event.target.value;
  if (action === "filter-sidebar") {
    state.sidebarFilter = event.target.value;
    pendingFocus = { action, cursor: event.target.selectionStart ?? event.target.value.length };
    render();
  }
  if (action === "global-search") {
    state.searchQuery = event.target.value;
    pendingFocus = { action, cursor: event.target.selectionStart ?? event.target.value.length };
    render();
  }
  if (action === "admin-search") {
    state.adminSearch = event.target.value;
    pendingFocus = { action, cursor: event.target.selectionStart ?? event.target.value.length };
    render();
  }
  if (action === "filter-people") {
    state.peopleSearch = event.target.value;
    pendingFocus = { action, cursor: event.target.selectionStart ?? event.target.value.length };
    render();
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.form && event.target.form.id === "modalEventForm") {
    if (!state.eventModal) state.eventModal = {};
    state.eventModal[event.target.name] = event.target.value;
  }
  const action = event.target.dataset.action;
  try {
    if (action === "presence-change") await updateProfile({ presence: event.target.value });
    if (action === "role-change") {
      await api(`/api/users/${event.target.dataset.userId}`, { method: "PATCH", body: { role: event.target.value } });
      await refresh();
    }
    if (action === "flag-change") {
      await api("/api/admin/feature-flags", { method: "PUT", body: { key: event.target.dataset.flag, value: event.target.checked } });
      await refresh();
    }
    if (action === "target-user-select-change" || event.target.id === "aiReportTargetUserSelect") {
      state.aiReportTargetUserId = event.target.value;
      state.aiReportSelectedUserId = event.target.value;
      state.aiReportSelectedReportId = null;
      render();
    }
    if (action === "filter-x-mentions") {
      state.xMentionAccountFilter = event.target.value || "all";
      render();
    }
    if (event.target.id === "fileInput") {
      queueUploads(Array.from(event.target.files || []));
      event.target.value = "";
    }
  } catch (error) {
    toast("Update failed", error.message);
  }
});

document.addEventListener("focusout", async (event) => {
  const action = event.target.dataset.action;
  try {
    if (action === "status-change") await updateProfile({ statusText: event.target.value });
    if (action === "title-change") await updateProfile({ title: event.target.value });
    if (action === "department-change") await updateProfile({ department: event.target.value });
  } catch (error) {
    toast("Profile update failed", error.message);
  }
});

document.addEventListener("click", async (event) => {
  // Close event/addUser modals ONLY if user clicked on the dark backdrop outside the modal dialog
  if (event.target.id === "addUserModalBackdrop") {
    state.showAddUserModal = false;
    render();
    return;
  }
  if (event.target.id === "attachmentPreviewBackdrop") {
    closeAttachmentPreview();
    return;
  }
  if (event.target.id === "eventModalBackdrop" || (event.target.classList && event.target.classList.contains("modal-backdrop") && !event.target.closest(".modal-dialog"))) {
    state.showEventModal = false;
    state.showAddUserModal = false;
    state.eventModal = null;
    render();
    return;
  }

  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  try {
    if (action === "filter-x-mentions") {
      state.xMentionAccountFilter = button.dataset.account || "all";
      render();
      return;
    }
    if (action === "test-desktop-notification") {
      await requestDesktopNotificationPermission({ test: true });
      render();
      return;
    }
    if (action === "workspace-view") {
      const nextView = button.dataset.view === "reports" && !isReportAdmin() ? "home" : button.dataset.view;
      if (state.workspaceMode !== nextView) {
        if (!state.viewScroll) state.viewScroll = {};
        state.viewScroll[nextView] = 0;
      }
      state.workspaceMode = nextView;
      state.ui.detailsOpen = false;
      state.sidebarFilter = "";
      render();
    }
    if (action === "refresh-x-mentions") {
      const response = await api("/api/admin/x-mentions/refresh", { method: "POST", body: {} });
      if (data.xMentionStatus) data.xMentionStatus.running = true;
      render();
      toast(response.started ? "X mentions refresh started" : "X mentions refresh already running", "The feed will update automatically when the check finishes.");
    }
    if (action === "cal-prev-month") {
      if (state.calendarMonth === 0) {
        state.calendarMonth = 11;
        state.calendarYear -= 1;
      } else {
        state.calendarMonth -= 1;
      }
      render();
    }
    if (action === "cal-next-month") {
      if (state.calendarMonth === 11) {
        state.calendarMonth = 0;
        state.calendarYear += 1;
      } else {
        state.calendarMonth += 1;
      }
      render();
    }
    if (action === "cal-today") {
      state.calendarYear = new Date().getFullYear();
      state.calendarMonth = new Date().getMonth();
      render();
    }
    if (action === "open-event-modal") {
      state.showEventModal = true;
      state.eventModalDate = button.dataset.date || "";
      state.eventModal = {
        title: "",
        eventType: "client",
        clientName: "",
        startsAt: state.eventModalDate ? `${state.eventModalDate}T10:00` : "",
        advanceNoticeDays: 4,
        location: "",
        description: ""
      };
      render();
    }
    if (action === "close-event-modal") {
      state.showEventModal = false;
      state.eventModal = null;
      render();
    }
    if (action === "open-add-user-modal") {
      state.showAddUserModal = true;
      render();
    }
    if (action === "close-add-user-modal") {
      state.showAddUserModal = false;
      render();
    }
    if (action === "gen-user-password") {
      const chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%&*";
      let pwd = "";
      for (let i = 0; i < 14; i++) {
        pwd += chars.charAt(Math.floor(Math.random() * chars.length));
      }
      const input = document.getElementById("newUserPasswordInput");
      if (input) {
        input.value = pwd;
        input.type = "text";
        input.select();
        try {
          navigator.clipboard.writeText(pwd);
          toast("Password generated", "Random password copied to clipboard.");
        } catch (e) {
          toast("Password generated", "Password generated in field.");
        }
      }
    }
    if (action === "clear-people-search") {
      state.peopleSearch = "";
      render();
    }
    if (action === "delete-channel") {
      const channelId = button.dataset.channelId || state.activeChannelId;
      const chan = data.channels.find((c) => c.id === channelId);
      if (!chan) return;
      const isProtected = chan.slug === "general" || chan.slug === "announcements" || chan.name === "general" || chan.name === "announcements";
      if (isProtected) {
        toast("Cannot delete", `#${chan.name} is a protected workspace channel.`);
        return;
      }
      if (!confirm(`Are you sure you want to permanently delete #${chan.name}? All messages, files, and conversation history will be permanently deleted.`)) {
        return;
      }
      try {
        await api(`/api/channels/${channelId}`, { method: "DELETE" });
        if (state.activeChannelId === channelId) {
          const fallback = data.channels.find((c) => (c.slug === "general" || c.name === "general") && c.id !== channelId) || data.channels.find((c) => c.id !== channelId);
          state.activeChannelId = fallback ? fallback.id : null;
        }
        await refresh({ preserveMessages: false });
        toast("Channel deleted", `#${chan.name} was successfully deleted.`);
      } catch (err) {
        toast("Delete failed", err.message);
      }
    }
    if (action === "delete-file") {
      const fileId = button.dataset.fileId;
      const file = fileById(fileId);
      const fileName = file?.originalName || "this file";
      if (!confirm(`Are you sure you want to permanently delete "${fileName}"? This cannot be undone.`)) {
        return;
      }
      try {
        await api(`/api/files/${fileId}`, { method: "DELETE" });
        await refresh({ preserveMessages: true });
        toast("File deleted", `"${fileName}" has been permanently removed.`);
      } catch (err) {
        toast("Delete failed", err.message);
      }
    }
    if (action === "delete-event") {
      const eventId = button.dataset.eventId;
      if (eventId && confirm("Are you sure you want to delete this event?")) {
        await api(`/api/events/${eventId}`, { method: "DELETE" });
        await refresh();
        toast("Event deleted", "The calendar event was removed.");
      }
    }
    if (action === "select-ai-report") {
      state.aiReportSelectedReportId = button.dataset.reportId;
      render();
    }
    if (action === "filter-ai-report-user") {
      const userId = button.dataset.userId || null;
      state.workspaceMode = "reports";
      state.aiReportSelectedUserId = userId;
      if (userId !== "__team") state.aiReportTargetUserId = userId;
      state.aiReportSelectedReportId = null;
      render();
    }
    if (action === "open-ai-report" && isReportAdmin()) {
      state.workspaceMode = "reports";
      if (button.dataset.reportId) {
        state.aiReportSelectedReportId = button.dataset.reportId;
        state.aiReportSelectedUserId = null;
      }
      render();
    }
    if (action === "generate-selected-user-report") {
      const select = document.getElementById("aiReportTargetUserSelect");
      const targetUserId = select?.value || state.aiReportTargetUserId || currentUser()?.id;
      const targetUser = userById(targetUserId);
      state.aiReportGenerating = true;
      render();
      try {
        const res = await api("/api/ai/reports/generate", { method: "POST", body: { userId: targetUserId } });
        state.aiReportSelectedUserId = targetUserId;
        state.aiReportTargetUserId = targetUserId;
        await refresh();
        if (res.report) {
          state.aiReportSelectedReportId = res.report.id;
        }
        toast("✨ Weekly Report Generated", `Activity digest for ${targetUser?.displayName || "employee"} synthesized via Amazon Bedrock Qwen.`);
      } catch (err) {
        toast("Generation failed", err.message);
      } finally {
        state.aiReportGenerating = false;
        render();
      }
    }
    if (action === "generate-specific-user-report") {
      const targetUserId = button.dataset.userId || currentUser()?.id;
      const targetUser = userById(targetUserId);
      state.aiReportGenerating = true;
      render();
      try {
        const res = await api("/api/ai/reports/generate", { method: "POST", body: { userId: targetUserId } });
        state.aiReportSelectedUserId = targetUserId;
        state.aiReportTargetUserId = targetUserId;
        await refresh();
        if (res.report) {
          state.aiReportSelectedReportId = res.report.id;
        }
        toast("✨ Weekly Report Generated", `Activity digest for ${targetUser?.displayName || "employee"} synthesized via Amazon Bedrock Qwen.`);
      } catch (err) {
        toast("Generation failed", err.message);
      } finally {
        state.aiReportGenerating = false;
        render();
      }
    }
    if (action === "generate-all-weekly-reports") {
      state.aiReportGenerating = "team";
      render();
      try {
        await api("/api/ai/reports/generate", { method: "POST", body: { all: true } });
        toast("👥 Team report started", "Summarizing every user's week. This can take a few minutes.");
        // The combined report runs in the background (one AI call per user); poll until it finishes.
        const job = await new Promise((resolve, reject) => {
          const poll = setInterval(async () => {
            try {
              const status = await api("/api/ai/reports/status");
              if (!status.running) {
                clearInterval(poll);
                resolve(status);
              }
            } catch (err) {
              clearInterval(poll);
              reject(err);
            }
          }, 4000);
        });
        if (job.lastError) throw new Error(job.lastError);
        await refresh();
        state.aiReportSelectedUserId = "__team";
        state.aiReportSelectedReportId = job.lastReportId || null;
        toast("✨ Team Report Ready", "The combined report for all users is ready.");
      } catch (err) {
        toast("Generation failed", err.message);
      } finally {
        state.aiReportGenerating = false;
        render();
      }
    }
    if (action === "delete-ai-report") {
      const reportId = button.dataset.reportId;
      try {
        await api(`/api/ai/reports/${reportId}`, { method: "DELETE" });
        if (state.aiReportSelectedReportId === reportId) {
          state.aiReportSelectedReportId = null;
        }
        await refresh();
        toast("Report deleted", "Report removed from archive.");
      } catch (err) {
        toast("Delete failed", err.message);
      }
    }
    if (action === "clear-all-ai-reports") {
      if (confirm("Are you sure you want to remove all weekly activity reports from the archive?")) {
        try {
          await api("/api/ai/reports", { method: "DELETE" });
          state.aiReportSelectedReportId = null;
          state.aiReportSelectedUserId = null;
          await refresh();
          toast("Archive cleared", "All weekly activity reports have been removed.");
        } catch (err) {
          toast("Clear failed", err.message);
        }
      }
    }
    if (action === "trigger-briefing") {
      state.briefingGenerating = true;
      toast("⚡ Radar Synthesis Initiated", "Synthesizing 19 crypto categories via Amazon Bedrock Qwen, Exa, Tavily & DefiLlama...");
      render();
      try {
        await api("/api/blockchain-briefing/generate", { method: "POST" });
        const pollInterval = setInterval(async () => {
          try {
            const status = await api("/api/blockchain-briefing/status");
            if (!status.running) {
              clearInterval(pollInterval);
              state.briefingGenerating = false;
              if (status.lastError) {
                toast("Briefing Failed", status.lastError);
              } else {
                toast("⛓️ Briefing Complete", "Daily blockchain intelligence briefing successfully updated!");
                const iframe = document.getElementById("briefingIframe");
                if (iframe) iframe.src = `/blockchain-briefing-live.html?t=${Date.now()}`;
              }
              render();
            }
          } catch (e) {
            clearInterval(pollInterval);
            state.briefingGenerating = false;
            render();
          }
        }, 5000);
      } catch (err) {
        state.briefingGenerating = false;
        toast("Generation Error", err.message || "Failed to trigger briefing");
        render();
      }
    }
    if (action === "auth-mode") {
      state.authMode = button.dataset.mode;
      state.authError = "";
      state.serverHint = "";
      render();
    }
    if (action === "logout") await logout();
    if (action === "switch-chat") {
      state.workspaceMode = "chat";
      render();
    }
    if (action === "switch-command-center") {
      if (canUseCommandCenter()) {
        state.workspaceMode = "command";
        state.adminActiveTab = state.adminActiveTab || "home";
        render();
      }
    }
    if (action === "open-portal") {
      openAdminPortal(button.dataset.portalId);
    }
    if (action === "switch-admin-tab") {
      state.adminActiveTab = button.dataset.tabId;
      render();
    }
    if (action === "close-admin-tab") {
      event.stopPropagation();
      closeAdminPortal(button.dataset.tabId);
    }
    if (action === "open-external") {
      const url = safeExternalUrl(button.dataset.url || "");
      if (url) window.open(url, "_blank", "noopener");
      else toast("Integration not configured", "Add the integration URL on the server first.");
    }
    if (action === "copy-integration-env") {
      await copyText(button.dataset.template || "");
      toast("Setup values copied", "Add them to the server environment and restart.");
    }
    if (action === "toggle-sidebar") {
      if (state.ui.sidebarCollapsed) state.ui.sidebarCollapsed = false;
      state.ui.sidebarOpen = !state.ui.sidebarOpen;
      render();
    }
    if (action === "toggle-sidebar-collapse") {
      state.ui.sidebarCollapsed = !state.ui.sidebarCollapsed;
      state.ui.sidebarOpen = false;
      render();
    }
    if (action === "toggle-nav-section") {
      const section = button.dataset.section;
      state.ui.collapsedNavSections[section] = !state.ui.collapsedNavSections[section];
      render();
    }
    if (action === "select-channel") {
      selectChannel(button.dataset.channelId);
    }
    if (action === "open-panel") {
      state.activePanel = button.dataset.panel;
      state.ui.detailsOpen = true;
      render();
      if (state.activePanel === "search") document.querySelector('[data-action="global-search"]')?.focus();
    }
    if (action === "clear-global-search") {
      state.searchQuery = "";
      render();
      document.querySelector('[data-action="global-search"]')?.focus();
      return;
    }
    if (action === "use-recent-search") {
      state.searchQuery = button.dataset.query || "";
      saveRecentSearch(state.searchQuery);
      render();
      document.querySelector('[data-action="global-search"]')?.focus();
      return;
    }
    if (action === "close-details") {
      state.ui.detailsOpen = false;
      render();
    }
    if (action === "format-composer") {
      const input = document.querySelector('[data-action="composer-draft"]');
      if (input && !input.disabled) {
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const selected = input.value.slice(start, end);
        const marker = { bold: "**", italic: "*", code: String.fromCharCode(96) }[button.dataset.format];
        const inserted = marker ? marker + (selected || "text") + marker : "🙂";
        input.setRangeText(inserted, start, end, "end");
        state.composerDrafts[state.activeChannelId] = input.value;
        input.focus();
      }
    }
    if (action === "toggle-urgent") {
      state.composerUrgent = !state.composerUrgent;
      render();
    }
    if (action === "acknowledge-message") {
      const messageId = button.dataset.messageId;
      if (messageId) {
        try {
          const res = await api(`/api/messages/${messageId}/acknowledge`, { method: "POST" });
          playNotificationChime();
          toast("✓ Message Acknowledged!", "Urgent status cleared and constant pings stopped.");
          const msg = (data.messages || []).find((m) => m.id === messageId);
          if (msg) {
            msg.acknowledgedAt = res?.acknowledgedAt || new Date().toISOString();
            msg.acknowledgedBy = currentUser().id;
          }
          (data.notifications || []).forEach((n) => {
            if (n.payload && n.payload.includes(messageId)) {
              n.readAt = new Date().toISOString();
              n.read_at = n.readAt;
            }
          });
          state.ui.toasts = state.ui.toasts.filter((t) => t.messageId !== messageId);
          renderToasts();
          render();
          await refresh();
        } catch (err) {
          toast("Error", "Could not acknowledge message.");
        }
      }
    }
    if (action === "send-message") {
      const textarea = document.querySelector('[data-action="composer-draft"]');
      const draft = (textarea ? textarea.value : (state.composerDrafts[state.activeChannelId] || "")).trim();
      const channelId = state.activeChannelId;
      const pending = (state.pendingAttachments || []).filter((a) => a.channelId === channelId);

      const hasUploading = pending.some((a) => a.status === "uploading" || a.status === "scan pending");
      if (hasUploading) {
        toast("Attachment loading", "Please wait for your attachment to finish loading.");
        return;
      }

      const readyAttachments = pending.filter((a) => a.status === "available" && a.id);

      if (draft || readyAttachments.length > 0) {
        const attachmentIds = readyAttachments.map((a) => a.id);
        const bodyText = draft || (readyAttachments.length === 1 ? `Shared file **${readyAttachments[0].name}**` : `Shared ${readyAttachments.length} attachments`);

        state.pendingAttachments = (state.pendingAttachments || []).filter((a) => a.channelId !== channelId);
        state.composerDrafts[channelId] = "";
        if (textarea) textarea.value = "";

        await createMessage(bodyText, attachmentIds);

        state.composerDrafts[channelId] = "";
        const afterTextarea = document.querySelector('[data-action="composer-draft"]');
        if (afterTextarea) afterTextarea.value = "";
        render();
      }
    }
    if (action === "react") {
      await api(`/api/messages/${button.dataset.messageId}/reactions`, { method: "POST", body: { emoji: button.dataset.emoji } });
      await refresh();
    }
    if (action === "open-thread") {
      state.selectedThreadId = button.dataset.messageId;
      state.activePanel = "thread";
      state.ui.detailsOpen = true;
      render();
    }
    if (action === "edit-message") {
      const message = data.messages.find((item) => item.id === button.dataset.messageId);
      state.editingMessageId = message?.id || null;
      state.editDraft = message?.body || "";
      pendingFocus = { action: "edit-draft", cursor: state.editDraft.length };
      render();
    }
    if (action === "save-edit") {
      if (state.editDraft.trim()) {
        await api(`/api/messages/${button.dataset.messageId}`, { method: "PATCH", body: { body: state.editDraft.trim() } });
        state.editingMessageId = null;
        state.editDraft = "";
        await refresh();
      } else {
        toast("Message not saved", "Edited messages cannot be empty.");
      }
    }
    if (action === "cancel-edit") {
      state.editingMessageId = null;
      state.editDraft = "";
      render();
    }
    if (action === "delete-message") {
      await api(`/api/messages/${button.dataset.messageId}`, { method: "DELETE" });
      await refresh();
    }
    if (action === "toggle-user-active") {
      await api(`/api/admin/users/${button.dataset.userId}`, { method: "PATCH", body: { isActive: button.dataset.active === "true" } });
      await refresh();
      toast("User updated", button.dataset.active === "true" ? "Enabled" : "Suspended");
    }
    if (action === "remove-workspace-member") {
      const userId = button.dataset.userId;
      const userName = button.dataset.userName || "this user";
      if (confirm(`Are you sure you want to permanently remove "${userName}" from the workspace? All channel memberships and active sessions will be revoked.`)) {
        try {
          const res = await api(`/api/users/${userId}`, { method: "DELETE" });
          await refresh();
          toast("Member removed", res.message || `${userName} was removed from the workspace.`);
        } catch (err) {
          toast("Removal failed", err.message || "Could not remove member.");
        }
      }
    }
    if (action === "lock-channel-id") {
      await api(`/api/channels/${button.dataset.channelId}/lock`, { method: "POST" });
      await refresh();
      toast("Channel updated", "Lock state changed.");
    }
    if (action === "shuffle-run-workflow") {
      await api(`/api/shuffle/workflows/${button.dataset.workflowId}/execute`, { method: "POST", body: { confirmed: true } });
      await refresh();
      toast("Workflow executed", button.dataset.workflowId);
    }
    if (action === "decide-leave") {
      await api(`/api/hr/leave/${button.dataset.leaveId}/decide`, { method: "POST", body: { status: button.dataset.status } });
      await refresh();
      toast("Leave updated", button.dataset.status);
    }
    if (action === "attach-file") $("#fileInput")?.click();
    if (action === "remove-pending-attachment") {
      const tempId = button.dataset.tempId;
      const item = (state.pendingAttachments || []).find((a) => a.tempId === tempId);
      if (item?.id) {
        api(`/api/files/${item.id}`, { method: "DELETE" }).catch(() => {});
      }
      state.pendingAttachments = (state.pendingAttachments || []).filter((a) => a.tempId !== tempId);
      render();
    }
    if (action === "pause-upload") pauseUpload(button.dataset.uploadId);
    if (action === "resume-upload") resumeUpload(button.dataset.uploadId);
    if (action === "download-file") await downloadFile(button.dataset.fileId);
    if (action === "preview-attachment") await openAttachmentPreview(button.dataset.fileId);
    if (action === "close-attachment-preview") closeAttachmentPreview();
    if (action === "toggle-voice") state.voice.recording ? stopVoiceRecording() : await startVoiceRecording();
    if (action === "task-status") {
      await api(`/api/tasks/${button.dataset.taskId}`, { method: "PATCH", body: { status: button.dataset.status } });
      await refresh();
      state.activePanel = "tasks";
      toast("Task updated", button.dataset.status.replace("_", " "));
    }
    if (action === "copy-access-url") {
      await copyText(button.dataset.url || "");
      toast("Address copied", button.dataset.url || "");
    }
    if (action === "remote-assist-reject") {
      await api(`/api/remote-assist/${button.dataset.sessionId}/respond`, { method: "POST", body: { response: "rejected" } });
      await refresh();
      state.activePanel = "remote";
      toast("Remote request rejected", "The requester was notified.");
    }
    if (action === "remote-assist-end") {
      await api(`/api/remote-assist/${button.dataset.sessionId}/end`, { method: "POST" });
      await refresh();
      state.activePanel = "remote";
      toast("Remote session updated", "Remote assistance status was stored.");
    }
    if (action === "remote-assist-copy") {
      const session = data.remoteAssists.find((item) => item.id === button.dataset.sessionId);
      if (session?.targetHost) {
        await copyText(`mstsc /v:${session.targetHost}`);
        toast("RDP command copied", session.targetHost);
      }
    }
    if (action === "remote-assist-download") {
      const session = data.remoteAssists.find((item) => item.id === button.dataset.sessionId);
      if (session?.targetHost) {
        const peer = remoteAssistPeer(session);
        downloadText(`${safeFilename(peer.displayName)}-${safeFilename(session.targetHost)}.rdp`, rdpFileContent(session), "application/x-rdp");
        toast("RDP file ready", session.targetHost);
      }
    }
    if (action === "event-response") {
      await api(`/api/events/${button.dataset.eventId}/response`, { method: "POST", body: { response: button.dataset.response } });
      await refresh();
    }
    if (action === "start-dm") {
      const payload = await api("/api/dms", { method: "POST", body: { userId: button.dataset.userId } });
      await refresh();
      selectChannel(payload.channelId);
    }
    if (action === "lock-channel") {
      await api(`/api/channels/${activeChannel().id}/lock`, { method: "POST" });
      await refresh();
    }
    if (action === "remove-group-member") {
      const removedUser = userById(button.dataset.userId);
      await api(`/api/channels/${button.dataset.channelId}/members/${button.dataset.userId}`, { method: "DELETE" });
      await refresh();
      state.activePanel = "group";
      toast("Member removed", removedUser.displayName);
    }
    if (action === "export-channel") exportJson(`${activeChannel().slug}-export.json`, { channel: activeChannel(), messages: data.messages.filter((message) => message.channelId === activeChannel().id), files: data.files.filter((file) => file.channelId === activeChannel().id) });
    if (action === "export-audit") exportJson("beenco-audit.json", data.audit);
    if (action === "jump-message") {
      const noteId = button.dataset.noteId || button.closest(".x-notif-card")?.dataset?.noteId;
      if (noteId) {
        void api("/api/notifications/read", { method: "POST", body: { id: noteId } });
        const note = (data.notifications || []).find((n) => n.id === noteId);
        if (note) {
          note.readAt = new Date().toISOString();
          note.read_at = note.readAt;
        }
      }
      const message = data.messages.find((item) => item.id === button.dataset.messageId);
      if (message) {
        selectChannel(message.channelId);
        state.selectedThreadId = message.parentId || message.id;
        state.activePanel = "thread";
        render();
      } else if (button.dataset.channelId) {
        selectChannel(button.dataset.channelId);
        if (button.dataset.messageId) state.selectedThreadId = button.dataset.messageId;
        state.activePanel = "thread";
        render();
      }
    }
    if (action === "mark-notification-read") {
      const noteId = button.dataset.noteId;
      await api("/api/notifications/read", { method: "POST", body: { id: noteId } });
      const note = (data.notifications || []).find((n) => n.id === noteId);
      if (note) {
        note.readAt = new Date().toISOString();
        note.read_at = note.readAt;
      }
      render();
    }
    if (action === "mark-all-notifications-read") {
      await api("/api/notifications/read", { method: "POST", body: { all: true } });
      const now = new Date().toISOString();
      (data.notifications || []).forEach((n) => {
        n.readAt = now;
        n.read_at = now;
      });
      render();
      toast("Notifications marked as read", "All alerts marked read.");
    }
    if (action === "mark-all-stale-reviewed") {
      const importantItems = getStaleItems(60);
      await Promise.all(importantItems.map((item) => api(`/api/messages/${item.messageId}/acknowledge`, { method: "POST" })));
      const now = new Date().toISOString();
      const importantIds = new Set(importantItems.map((item) => item.messageId));
      (data.messages || []).forEach((message) => {
        if (importantIds.has(message.id)) {
          message.acknowledgedAt = now;
          message.acknowledgedBy = currentUser().id;
        }
      });
      (data.notifications || []).forEach((notification) => {
        const payload = notificationPayload(notification);
        if (notification.type === "urgent" && importantIds.has(payload.messageId)) {
          notification.readAt = now;
          notification.read_at = now;
        }
      });
      state.dismissedPendingPingUntil = Date.now() + 24 * 60 * 60 * 1000;
      state.simulateStalePing = false;
      render();
      toast("Important work reviewed", `${importantItems.length} important message(s) acknowledged.`);
    }
    if (action === "filter-notifs") {
      state.notifFilter = button.dataset.filter || "all";
      render();
    }
    if (action === "review-stale-pings") {
      const stale = getStaleItems(state.stalePingThresholdMins || 60);
      if (stale.some((i) => i.portal === "hr") && canUsePortal("hr")) {
        openAdminPortal("hr");
      } else {
        state.activePanel = "notifications";
        state.notifFilter = "stale";
        state.ui.detailsOpen = true;
        render();
      }
    }
    if (action === "dismiss-stale-ping") {
      state.dismissedPendingPingUntil = Date.now() + 24 * 60 * 60 * 1000;
      state.simulateStalePing = false;
      render();
      toast("Alert dismissed", "Pending review alert dismissed for today.");
    }
    if (action === "simulate-stale-ping") {
      state.simulateStalePing = !state.simulateStalePing;
      if (state.simulateStalePing) {
        state.dismissedPendingPingUntil = null;
        playUrgentPingChime();
        toast("Simulated 1h Ping Alert", "Playing attention chime and displaying warning banner.");
      } else {
        toast("Simulation stopped", "Alert banner reset.");
      }
      render();
    }
    if (action === "test-notification-chime") {
      playNotificationChime();
      toast("Notification chime played", "Sound is working.");
    }
    if (action === "close-toast") {
      const toastId = button.dataset.toastId;
      state.ui.toasts = state.ui.toasts.filter((t) => t.id !== toastId);
      renderToasts();
    }
    if (action === "query-pending-pings") {
      const res = await api("/api/admin/pending-pings?mins=60");
      toast(`Found ${res.staleCount} stale items (>1h)`, res.hasPendingPings ? "Pending review items exist." : "All caught up!");
    }
  } catch (error) {
    toast("Action failed", error.message);
  }
});

async function logout() {
  if (liveEventSource) {
    liveEventSource.close();
    liveEventSource = null;
  }
  clearTimeout(liveReconnectTimer);
  liveReconnectTimer = null;
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    // The cookie is cleared server-side even if the session had already expired.
  }
  data = emptyData();
  state.activeChannelId = null;
  state.selectedThreadId = null;
  state.workspaceMode = null;
  state.adminActiveTab = "home";
  state.adminTabs = ["home"];
  state.adminSearch = "";
  state.authMode = "login";
  state.authError = "";
  resetNotificationAlertState();
  render();
}

function selectChannel(channelId) {
  if (state.workspaceMode !== "dms") state.workspaceMode = "home";
  state.activeChannelId = channelId;
  const channel = data.channels.find((item) => item.id === channelId);
  if (channel) {
    channel.unread = 0;
    channel.mentions = 0;
  }
  state.selectedThreadId = data.messages.find((message) => message.channelId === channelId && !message.parentId)?.id || null;
  state.messageScroll = { channelId, top: 0, nearBottom: true };
  state.ui.sidebarOpen = false;
  render();
  void markChannelRead(channelId);
}

function openAdminPortal(portalId) {
  if (!portalId || !portalById(portalId)) return;
  if (!state.adminTabs.includes(portalId)) state.adminTabs.push(portalId);
  state.adminActiveTab = portalId;
  render();
}

function closeAdminPortal(portalId) {
  if (portalId === "home") return;
  state.adminTabs = state.adminTabs.filter((tab) => tab !== portalId);
  if (state.adminActiveTab === portalId) state.adminActiveTab = state.adminTabs[state.adminTabs.length - 1] || "home";
  render();
}

async function copyText(value) {
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function downloadText(filename, text, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function createMessage(body, attachments = [], parentId = null, channelId = state.activeChannelId, isUrgent = state.composerUrgent) {
  const urgentFlag = Boolean(isUrgent);
  state.composerUrgent = false;
  if (channelId && !parentId) {
    state.composerDrafts[channelId] = "";
    const textarea = document.querySelector('[data-action="composer-draft"]');
    if (textarea) textarea.value = "";
  }
  await api("/api/messages", { method: "POST", body: { channelId, body, attachments, parentId, isUrgent: urgentFlag } });
  state.composerUrgent = false;
  if (channelId === state.activeChannelId && !parentId) state.messageScroll.nearBottom = true;
  await refresh({ preserveMessages: false, pinToBottom: channelId === state.activeChannelId && !parentId });
  if (channelId && !parentId) {
    state.composerDrafts[channelId] = "";
    const textarea = document.querySelector('[data-action="composer-draft"]');
    if (textarea) textarea.value = "";
  }
}

async function markChannelRead(channelId = state.activeChannelId) {
  if (!channelId || !currentUser()) return;
  try {
    await api(`/api/channels/${channelId}/read`, { method: "POST" });
    const channel = data.channels.find((item) => item.id === channelId);
    if (channel) {
      channel.unread = 0;
      channel.mentions = 0;
    }
    const now = new Date().toISOString();
    (data.notifications || []).forEach((n) => {
      if (n.type !== "urgent") {
        const payload = notificationPayload(n);
        if (payload.channelId === channelId) {
          n.readAt = now;
          n.read_at = now;
        }
      }
    });
    render();
  } catch {
    // Read state is opportunistic and will retry on the next channel change/refresh.
  }
}

function queueTyping(isTyping = true) {
  if (!state.activeChannelId || !currentUser()) return;
  const now = Date.now();
  if (isTyping && now - lastTypingPing > 2200) {
    lastTypingPing = now;
    api(`/api/channels/${state.activeChannelId}/typing`, { method: "POST", body: { typing: true } }).catch(() => {});
  }
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => {
    api(`/api/channels/${state.activeChannelId}/typing`, { method: "POST", body: { typing: false } }).catch(() => {});
  }, 1800);
}

async function updateProfile(payload) {
  await api(`/api/users/${currentUser().id}`, { method: "PATCH", body: payload });
  await refresh();
}

function queueUploads(files) {
  if (!files || !files.length) return;
  if (!state.activeChannelId) {
    toast("No channel selected", "Please open a channel before attaching files.");
    return;
  }

  if (!state.pendingAttachments) {
    state.pendingAttachments = [];
  }
  const channelId = state.activeChannelId;

  files.forEach((file) => {
    const allowed = isUploadAllowed(file);
    if (!allowed.ok) {
      toast("Upload rejected", allowed.message);
      return;
    }
    const pendingItem = {
      id: null,
      tempId: uid("patt"),
      channelId,
      file,
      name: file.name,
      size: file.size,
      mime: file.type || "application/octet-stream",
      progress: 15,
      status: "uploading"
    };
    state.pendingAttachments.push(pendingItem);
    stageUpload(pendingItem);
  });
  render();
}

async function stageUpload(item) {
  try {
    item.progress = 30;
    render();

    const formData = new FormData();
    formData.append("channelId", item.channelId);
    formData.append("originalName", item.name);
    formData.append("mime", item.mime);
    formData.append("sizeBytes", String(item.size));
    formData.append("extractedText", item.name);
    formData.append("attachOnly", "1");
    formData.append("file", item.file, item.name);

    item.progress = 65;
    render();

    const payload = await apiForm("/api/files/upload", formData);
    item.id = payload.fileId;
    item.progress = 90;
    item.status = "scan pending";
    render();

    const scan = await api(`/api/files/${payload.fileId}/scan`, { method: "POST" });
    item.progress = 100;
    item.status = scan.status || "available";
    render();
  } catch (error) {
    item.status = "failed";
    item.error = error.message;
    toast("Upload failed", error.message);
    render();
  }
}

function pauseUpload(uploadId) {
  const upload = state.uploads.find((item) => item.id === uploadId);
  if (!upload) return;
  upload.paused = true;
  upload.status = "paused - resumable";
  render();
}

function resumeUpload(uploadId) {
  const upload = state.uploads.find((item) => item.id === uploadId);
  if (!upload) return;
  upload.paused = false;
  upload.status = "resuming";
  render();
}

function filenameFromDisposition(header = "") {
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const quoted = header.match(/filename="([^"]+)"/i);
  if (quoted) return quoted[1];
  const plain = header.match(/filename=([^;]+)/i);
  return plain ? plain[1].trim() : "";
}

async function downloadFile(fileId) {
  const response = await fetch(`/api/files/${fileId}/download`, { method: "POST", credentials: "same-origin" });
  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : {};
    throw new Error(payload.error || `Download failed (${response.status})`);
  }
  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get("content-disposition") || "") || fileById(fileId)?.originalName || "download";
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  await refresh();
  toast("Download ready", filename);
}

async function startVoiceRecording() {
  state.voice = { recording: true, elapsed: 0, waveform: [12, 22, 15, 28] };
  recordingChunks = [];
  try {
    if (navigator.mediaDevices?.getUserMedia && window.MediaRecorder) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size) recordingChunks.push(event.data);
      };
      mediaRecorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void createVoiceMessage(new Blob(recordingChunks, { type: "audio/webm" })).catch(handleVoiceMessageError);
      };
      mediaRecorder.start();
    } else {
      mediaRecorder = null;
    }
  } catch {
    mediaRecorder = null;
    toast("Microphone unavailable", "A simulated voice clip will be stored.");
  }
  recordingTimer = setInterval(() => {
    state.voice.elapsed += 1;
    state.voice.waveform.push(8 + Math.round(Math.random() * 26));
    render();
  }, 1000);
  render();
}

function stopVoiceRecording() {
  clearInterval(recordingTimer);
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  else void createVoiceMessage(null).catch(handleVoiceMessageError);
}

function handleVoiceMessageError(error) {
  state.voice = { recording: false, elapsed: 0, waveform: [] };
  toast("Voice message failed", error.message || "The audio clip could not be stored.");
  render();
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function makeFallbackVoiceDataUrl(durationSeconds) {
  const sampleRate = 8000;
  const duration = Math.max(1, Math.min(durationSeconds || 1, 5));
  const samples = sampleRate * duration;
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples * 2, true);
  for (let index = 0; index < samples; index += 1) {
    const envelope = Math.sin(Math.PI * index / samples);
    const sample = Math.sin(2 * Math.PI * 440 * index / sampleRate) * 0.28 * envelope;
    view.setInt16(44 + index * 2, sample * 32767, true);
  }
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
  return `data:audio/wav;base64,${btoa(binary)}`;
}

async function createVoiceMessage(blob) {
  const duration = Math.max(1, state.voice.elapsed);
  const waveform = state.voice.waveform;
  const previewDataUrl = blob ? await blobToDataUrl(blob) : makeFallbackVoiceDataUrl(duration);
  const sizeBytes = blob?.size || Math.round((previewDataUrl.length * 3) / 4);
  const mime = blob?.type || "audio/wav";
  const extension = mime.includes("wav") ? "wav" : "webm";
  state.voice = { recording: false, elapsed: 0, waveform: [] };
  const payload = await api("/api/files", {
    method: "POST",
    body: {
      channelId: state.activeChannelId,
      originalName: `voice-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`,
      mime,
      sizeBytes,
      kind: "voice",
      duration,
      waveform,
      previewDataUrl,
      extractedText: "voice message audio clip",
      messageBody: "Voice message"
    }
  });
  await api(`/api/files/${payload.fileId}/scan`, { method: "POST" });
  state.messageScroll.nearBottom = true;
  await refresh({ preserveMessages: false, pinToBottom: true });
  toast("Voice message stored", `${duration}s audio clip`);
}

function exportJson(filename, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function handleIncomingTyping(eventData) {
  if (!data || !data.channels) return;
  const channel = data.channels.find((c) => c.id === eventData.channelId);
  if (!channel) return;
  channel.typingUserIds = channel.typingUserIds || [];
  if (eventData.typing) {
    if (!channel.typingUserIds.includes(eventData.userId)) {
      channel.typingUserIds.push(eventData.userId);
    }
    setTimeout(() => {
      if (channel.typingUserIds) {
        channel.typingUserIds = channel.typingUserIds.filter((id) => id !== eventData.userId);
        if (eventData.channelId === state.activeChannelId) render();
      }
    }, 6000);
  } else {
    channel.typingUserIds = channel.typingUserIds.filter((id) => id !== eventData.userId);
  }
  if (eventData.channelId === state.activeChannelId) {
    render();
  }
}

function initLiveStream() {
  if (liveEventSource) {
    liveEventSource.close();
    liveEventSource = null;
  }
  if (!currentUser()) return;

  clearTimeout(liveReconnectTimer);
  liveReconnectTimer = null;
  try {
    liveEventSource = new EventSource("/api/events");

    liveEventSource.addEventListener("connected", () => {
      // Live event stream active
    });

    liveEventSource.addEventListener("message:created", async (evt) => {
      try {
        const payload = JSON.parse(evt.data || "{}");
        const eventData = payload.data || {};
        if (eventData.channelId === state.activeChannelId) {
          await refresh({ preserveMessages: false, pinToBottom: state.messageScroll.nearBottom });
        } else {
          await refresh({ background: true });
        }
      } catch {
        await refresh({ background: true });
      }
    });

    liveEventSource.addEventListener("message:edited", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("message:deleted", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("reaction:updated", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("message:acknowledged", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("file:uploaded", async (evt) => {
      try {
        const payload = JSON.parse(evt.data || "{}");
        const eventData = payload.data || {};
        if (eventData.channelId === state.activeChannelId) {
          await refresh({ preserveMessages: false, pinToBottom: state.messageScroll.nearBottom });
        } else {
          await refresh({ background: true });
        }
      } catch {
        await refresh({ background: true });
      }
    });

    liveEventSource.addEventListener("file:updated", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("file:deleted", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("channel:updated", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("channel:read", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("user:updated", async () => {
      await refresh({ background: true });
    });

    liveEventSource.addEventListener("channel:typing", (evt) => {
      try {
        const payload = JSON.parse(evt.data || "{}");
        const eventData = payload.data || {};
        if (eventData.channelId === state.activeChannelId && eventData.userId !== currentUser()?.id) {
          handleIncomingTyping(eventData);
        }
      } catch {}
    });

    liveEventSource.onerror = () => {
      if (liveEventSource) {
        liveEventSource.close();
        liveEventSource = null;
      }
      clearTimeout(liveReconnectTimer);
      liveReconnectTimer = setTimeout(initLiveStream, 3000);
    };
  } catch (err) {
    clearTimeout(liveReconnectTimer);
    liveReconnectTimer = setTimeout(initLiveStream, 5000);
  }
}

if ("serviceWorker" in navigator && location.protocol !== "file:") {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}

init();

refreshTimer = setInterval(() => {
  if (!currentUser() || state.loading) return;
  if (!liveEventSource && !liveReconnectTimer) {
    initLiveStream();
  }
  const nextDayKey = localDayKey();
  if (nextDayKey !== currentDayKey) {
    currentDayKey = nextDayKey;
    state.xMentionAccountFilter = "all";
    refresh({ background: true, preserveMessages: false }).catch(() => {});
    return;
  }
  refresh({ background: true }).catch(() => {});
}, 5000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && currentUser() && !state.loading) {
    if (!liveEventSource && !liveReconnectTimer) initLiveStream();
    refresh({ background: true }).catch(() => {});
  }
});

document.addEventListener("keydown", event => {
  if (!currentUser()) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    state.activePanel = "search";
    state.ui.detailsOpen = true;
    render();
    document.querySelector('[data-action="global-search"]')?.focus();
  }
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.target.matches('[data-action="composer-draft"]')) {
    event.preventDefault();
    document.querySelector('[data-action="send-message"]')?.click();
  }
  if (event.key === "Enter" && !event.isComposing && event.target.matches('[data-action="global-search"]')) {
    event.preventDefault();
    saveRecentSearch(state.searchQuery);
    render();
    document.querySelector('[data-action="global-search"]')?.focus();
  }
  if (event.key === "Escape") {
    if (state.previewFileId) {
      closeAttachmentPreview();
      return;
    }
    if (state.showAddUserModal) {
      state.showAddUserModal = false;
      render();
      return;
    }
    if (state.showEventModal) {
      state.showEventModal = false;
      state.eventModal = null;
      render();
      return;
    }
    state.ui.detailsOpen = false;
    state.ui.sidebarOpen = false;
    render();
  }
});
