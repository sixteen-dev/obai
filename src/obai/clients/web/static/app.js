/**
 * OBaI Web UI — Main application.
 *
 * Handles WebSocket connection, session management,
 * message rendering, and streaming responses.
 */

const state = {
    sessions: [],
    activeSessionId: null,
    processingSessionId: null,
    ws: null,
    isReady: false,
    reconnectAttempts: 0,
    compactToolsMode: null,
    savedInputs: {},
    // Last agent_switch label per session so the thinking-trail label can
    // be restored when the user switches back to a still-running session.
    lastAgentBySession: {},
};

const $ = (sel) => document.querySelector(sel);
const loadingOverlay = $("#loading-overlay");
const loadingStatus = $("#loading-status");
const sessionList = $("#session-list");
const messagesDiv = $("#messages");
const welcomeScreen = $("#welcome");
const queryInput = $("#query-input");
const sendBtn = $("#send-btn");
const chatArea = $("#chat-area");
const toolPanel = $("#tool-panel");
const showToolsBtn = $("#show-tools-btn");
const modelSummary = $("#model-summary");
const suggestionButtons = document.querySelectorAll(".suggestion-chip");

const toolTree = new ToolTree("tool-tree");

let streamingBubble = null;
let streamingText = "";
let streamingMessage = null;

document.addEventListener("DOMContentLoaded", () => {
    setupEventListeners();
    syncResponsivePanels();
    checkHubStatus();
    connectWebSocket();
    loadSessions();
});

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = protocol + "//" + location.host + "/ws";

    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
        // A reconnect means the server process went away and came back, so it
        // may have rebuilt its hub on different settings. The status poll
        // stops once the hub is ready, so without this the tab would keep
        // showing the pre-restart model until a manual reload.
        if (state.reconnectAttempts > 0) {
            refreshStatusSurfaces();
        }
        state.reconnectAttempts = 0;
    };

    state.ws.onmessage = (event) => {
        try {
            handleMessage(JSON.parse(event.data));
        } catch (error) {
            console.error("Failed to parse WS message:", error);
        }
    };

    state.ws.onclose = () => {
        const delay = Math.min(1000 * Math.pow(2, state.reconnectAttempts), 10000);
        state.reconnectAttempts += 1;
        setTimeout(connectWebSocket, delay);
    };
}

async function refreshStatusSurfaces() {
    // Re-read /api/status without touching the loading overlay or restarting
    // the readiness poll — used when the server comes back after a restart.
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        if (data.ready) {
            updateStatusSurfaces(data);
        }
    } catch (error) {
        console.error("Failed to refresh hub status:", error);
    }
}

async function checkHubStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        updateStatusSurfaces(data);

        if (data.ready) {
            state.isReady = true;
            loadingOverlay.classList.add("hidden");
            updateSendButton();
        } else {
            loadingStatus.textContent = data.status || "Initializing...";
            setTimeout(checkHubStatus, 2000);
        }
    } catch (error) {
        console.log("Hub status check failed, retrying...", error);
        setTimeout(checkHubStatus, 2000);
    }
}

function handleMessage(msg) {
    // Messages scoped to a session only render when that session is active.
    // session_title is always handled (updates sidebar regardless of view).
    const msgSession = msg.session_id;
    const isActiveSession = !msgSession || msgSession === state.activeSessionId;

    switch (msg.type) {
        case "status":
            if (!state.isReady) {
                loadingStatus.textContent = msg.message;
            }
            break;

        case "agent_switch": {
            const agentLabel = (msg.agent || "Central Hub") + " " + thinkingVerb();
            if (msgSession) {
                state.lastAgentBySession[msgSession] = agentLabel;
            }
            if (!isActiveSession) break;
            toolTree.setActiveAgent(msg.agent);
            const messageDiv = ensureStreamingMessage();
            const trail = ensureThinkingTrail(messageDiv);
            setThinkingTrailLabel(trail, agentLabel);
            toolTree.scrollToBottom();
            scrollChatToBottom();
            break;
        }

        case "tool_start":
            if (!isActiveSession) break;
            toolTree.addTool(
                msg.call_id,
                msg.agent,
                msg.tool,
                msg.args || "",
                msg.parent_id || null,
                msg.is_mcp || false
            );
            toolTree.scrollToBottom();
            break;

        case "tool_complete":
            if (!isActiveSession) break;
            toolTree.completeTool(msg.call_id, msg.duration_ms || 0);
            toolTree.scrollToBottom();
            break;

        case "text_delta":
            if (!isActiveSession) break;
            handleTextDelta(msg.delta);
            break;

        case "thinking_break":
            if (!isActiveSession) break;
            handleThinkingBreak();
            break;

        case "complete":
            handleComplete(msg);
            break;

        case "error":
            handleError(msg);
            break;

        case "session_title":
            updateSessionTitle(msg.session_id, msg.title);
            break;

        case "queued": {
            if (!isActiveSession) break;
            const messageDiv = ensureStreamingMessage();
            const trail = ensureThinkingTrail(messageDiv);
            setThinkingTrailLabel(trail, "Queued, waiting...");
            break;
        }
    }
}

function handleTextDelta(delta) {
    if (!streamingBubble) {
        ensureStreamingMessage();
        streamingBubble = appendSegmentBubble(streamingMessage);
        streamingBubble.classList.add("streaming");
        streamingText = "";
    }

    streamingText += delta;
    streamingBubble.textContent = streamingText;
    scrollChatToBottom();
}

function ensureStreamingMessage() {
    if (streamingMessage) {
        return streamingMessage;
    }
    const msgDiv = createMessageDiv("assistant", new Date());
    // Drop the bubble createMessageDiv pre-creates — text segments and the
    // thinking trail are appended explicitly so the layout stays predictable.
    const preBubble = msgDiv.querySelector(".message-bubble");
    if (preBubble) {
        preBubble.remove();
    }
    messagesDiv.appendChild(msgDiv);
    streamingMessage = msgDiv;
    return streamingMessage;
}

function setThinkingTrailLabel(trail, text) {
    const label = trail.querySelector(".thinking-trail-label");
    if (label) {
        label.textContent = text;
    }
}

function appendSegmentBubble(messageDiv) {
    const stack = messageDiv.querySelector(".message-stack");
    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    stack.appendChild(bubble);
    return bubble;
}

function handleThinkingBreak() {
    if (!streamingBubble || !streamingMessage) {
        return;
    }
    // Close out the current segment as intermediate narration and move it
    // into a collapsible "Thinking" trail at the top of the message — same
    // visual pattern as ChatGPT/Claude's reasoning section. Strip bubble
    // styling so the segment renders as a small italic line.
    streamingBubble.classList.remove("streaming", "message-bubble");
    streamingBubble.classList.add("thinking-line");
    const trail = ensureThinkingTrail(streamingMessage);
    trail.querySelector(".thinking-trail-list").appendChild(streamingBubble);
    streamingBubble = null;
    streamingText = "";
}

function ensureThinkingTrail(messageDiv) {
    let trail = messageDiv.querySelector(".thinking-trail");
    if (trail) {
        return trail;
    }
    trail = document.createElement("details");
    trail.className = "thinking-trail";
    trail.open = true;

    const summary = document.createElement("summary");
    summary.className = "thinking-trail-summary";

    const chevron = document.createElement("span");
    chevron.className = "thinking-trail-chevron";
    chevron.setAttribute("aria-hidden", "true");
    chevron.textContent = "›";
    summary.appendChild(chevron);

    const label = document.createElement("span");
    label.className = "thinking-trail-label";
    label.textContent = "Thinking";
    summary.appendChild(label);

    const dots = document.createElement("span");
    dots.className = "thinking-trail-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 3; i += 1) {
        const dot = document.createElement("span");
        dot.textContent = ".";
        dots.appendChild(dot);
    }
    summary.appendChild(dots);

    const list = document.createElement("div");
    list.className = "thinking-trail-list";

    trail.appendChild(summary);
    trail.appendChild(list);

    const stack = messageDiv.querySelector(".message-stack");
    const meta = stack.querySelector(".message-meta");
    if (meta && meta.nextSibling) {
        stack.insertBefore(trail, meta.nextSibling);
    } else {
        stack.appendChild(trail);
    }
    return trail;
}

function handleComplete(msg) {
    const completedSession = msg.session_id || state.activeSessionId;
    if (state.processingSessionId === completedSession) {
        state.processingSessionId = null;
    }
    updateSendButton();

    const isActiveSession = completedSession === state.activeSessionId;
    if (!isActiveSession) {
        // The cached tool-tree DOM for this session was snapshotted while
        // the run was still in flight, so its spinners never finalize.
        // Drop the snapshot; the next switchSession will rebuild from the
        // freshly-persisted tool_data in the DB.
        if (completedSession) {
            toolTree.sessionHistory.delete(completedSession);
            delete state.lastAgentBySession[completedSession];
        }
        return;
    }

    toolTree.completeAll();
    toolTree.clearActiveAgent();
    delete state.lastAgentBySession[completedSession];

    if (msg.trace_id && msg.opik_url) {
        toolTree.addTraceLink(msg.trace_id, msg.opik_url);
    }

    if (streamingMessage) {
        const trail = streamingMessage.querySelector(".thinking-trail");
        if (trail) {
            trail.open = false;
            // Hides the animated dots — see .thinking-trail.is-done in CSS.
            trail.classList.add("is-done");
        }
    }

    if (streamingBubble && streamingText) {
        streamingBubble.classList.remove("streaming");
        if (typeof marked !== "undefined") {
            renderMarkdownInto(streamingBubble, streamingText);
        }

        if (msg.duration_ms && streamingMessage) {
            const parts = [formatDuration(msg.duration_ms)];
            if (msg.specialists && msg.specialists.length > 0) {
                parts.push(
                    msg.specialists.length + " agent" + (msg.specialists.length > 1 ? "s" : "")
                );
            }
            const timestamp = streamingMessage.dataset.timestampLabel || "";
            setMessageMetaDetail(
                streamingMessage,
                [timestamp, parts.join(" · ")].filter(Boolean).join(" · ")
            );
        }
    }

    streamingMessage = null;
    streamingBubble = null;
    streamingText = "";
    scrollChatToBottom();
}

function handleError(msg) {
    const errorSession = msg.session_id || state.activeSessionId;
    if (state.processingSessionId === errorSession) {
        state.processingSessionId = null;
    }
    updateSendButton();

    const isActiveSession = errorSession === state.activeSessionId;
    if (!isActiveSession) {
        if (errorSession) {
            toolTree.sessionHistory.delete(errorSession);
            delete state.lastAgentBySession[errorSession];
        }
        return;
    }

    toolTree.completeAll();
    toolTree.clearActiveAgent();
    delete state.lastAgentBySession[errorSession];

    finalizeStreamingMessageWithError(msg.message || "An error occurred");

    streamingMessage = null;
    streamingBubble = null;
    streamingText = "";
    scrollChatToBottom();
}

function finalizeStreamingMessageWithError(errorText) {
    // No in-flight assistant message: render the error as its own bubble.
    if (!streamingMessage) {
        const msgDiv = createMessageDiv("assistant", new Date());
        msgDiv.classList.add("message-error");
        msgDiv.querySelector(".message-bubble").textContent = errorText;
        messagesDiv.appendChild(msgDiv);
        return;
    }

    // Stop the animated thinking trail if one was attached.
    const trail = streamingMessage.querySelector(".thinking-trail");
    if (trail) {
        trail.open = false;
        trail.classList.add("is-done");
    }

    // Finalize whatever was streamed so it stays visible above the error.
    if (streamingBubble) {
        if (streamingText) {
            streamingBubble.classList.remove("streaming");
            if (typeof marked !== "undefined") {
                renderMarkdownInto(streamingBubble, streamingText);
            }
        } else {
            streamingBubble.remove();
        }
    }

    const stack = streamingMessage.querySelector(".message-stack");
    if (!stack) return;
    const errorBubble = document.createElement("div");
    errorBubble.className = "message-bubble message-error-bubble";
    errorBubble.textContent = errorText;
    stack.appendChild(errorBubble);
}

function renderMarkdownInto(element, markdownText) {
    const html = marked.parse(normalizeAssistantContent(markdownText));
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    sanitizeMarkdownTree(doc.body);
    element.textContent = "";
    while (doc.body.firstChild) {
        element.appendChild(doc.body.firstChild);
    }
    // Open all links in a new tab so the user stays in the app
    for (const link of element.querySelectorAll("a[href]")) {
        link.target = "_blank";
        link.rel = "noopener";
    }
    enhanceCodeBlocks(element);
}

// Tool outputs flow through `marked` unchanged, so external strings can carry
// raw HTML. Restrict to the tags `marked` emits from CommonMark and strip any
// script-bearing attribute before the parsed tree is attached to the live DOM.
const ALLOWED_TAGS = new Set([
    "A", "ABBR", "B", "BLOCKQUOTE", "BR", "CODE", "DD", "DEL", "DIV", "DL",
    "DT", "EM", "H1", "H2", "H3", "H4", "H5", "H6", "HR", "I", "IMG", "LI",
    "OL", "P", "PRE", "S", "SPAN", "STRONG", "SUB", "SUP", "TABLE", "TBODY",
    "TD", "TFOOT", "TH", "THEAD", "TR", "U", "UL",
]);
const URL_ATTRS = new Set(["href", "src"]);
const SAFE_URL_RE = /^(?:https?:|mailto:|tel:|#|\/|\.\/|\.\.\/)/i;

function sanitizeMarkdownTree(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    const toRemove = [];
    let node = walker.nextNode();
    while (node) {
        if (!ALLOWED_TAGS.has(node.tagName)) {
            toRemove.push(node);
        } else {
            scrubAttributes(node);
        }
        node = walker.nextNode();
    }
    for (const el of toRemove) {
        el.remove();
    }
}

function scrubAttributes(el) {
    for (const attr of Array.from(el.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith("on")) {
            el.removeAttribute(attr.name);
            continue;
        }
        if (URL_ATTRS.has(name)) {
            const value = attr.value.trim();
            if (!SAFE_URL_RE.test(value)) {
                el.removeAttribute(attr.name);
            }
        }
    }
}

function normalizeAssistantContent(markdownText) {
    const text = String(markdownText || "");
    const trimmed = text.trim();

    if (!trimmed || trimmed.includes("```")) {
        return text;
    }

    const startsLikeJson = trimmed.startsWith("{") || trimmed.startsWith("[");
    if (!startsLikeJson) {
        return text;
    }

    try {
        const parsed = JSON.parse(trimmed);
        return "```json\n" + JSON.stringify(parsed, null, 2) + "\n```";
    } catch {
        return text;
    }
}

function enhanceCodeBlocks(element) {
    const blocks = element.querySelectorAll("pre");
    for (const pre of blocks) {
        const code = pre.querySelector("code");
        if (!code || !looksLikeJsonBlock(code)) {
            continue;
        }

        pre.classList.add("has-copy-button");
        if (pre.querySelector(".code-copy-btn")) {
            continue;
        }

        const button = document.createElement("button");
        button.type = "button";
        button.className = "code-copy-btn";
        button.setAttribute("aria-label", "Copy JSON");
        button.innerHTML =
            "<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\">" +
            "<rect x=\"9\" y=\"9\" width=\"10\" height=\"10\" rx=\"2\"></rect>" +
            "<path d=\"M5 15V7a2 2 0 0 1 2-2h8\"></path>" +
            "</svg>";

        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(code.textContent || "");
                button.classList.add("copied");
                button.setAttribute("aria-label", "Copied");
                setTimeout(() => {
                    button.classList.remove("copied");
                    button.setAttribute("aria-label", "Copy JSON");
                }, 1200);
            } catch (error) {
                console.error("Failed to copy JSON block:", error);
            }
        });

        pre.appendChild(button);
    }
}

function looksLikeJsonBlock(codeEl) {
    const className = codeEl.className || "";
    if (className.includes("language-json")) {
        return true;
    }

    const text = (codeEl.textContent || "").trim();
    if (!(text.startsWith("{") || text.startsWith("["))) {
        return false;
    }

    try {
        JSON.parse(text);
        return true;
    } catch {
        return false;
    }
}

async function loadSessions() {
    try {
        const res = await fetch("/api/sessions");
        state.sessions = await res.json();
        renderSessionList();

        if (state.sessions.length > 0 && !state.activeSessionId) {
            switchSession(state.sessions[0].id);
        } else if (state.sessions.length === 0) {
            await createSession();
        }
    } catch {
        setTimeout(loadSessions, 2000);
    }
}

function renderSessionList() {
    sessionList.textContent = "";

    if (state.sessions.length === 0) {
        const empty = document.createElement("div");
        empty.className = "tool-empty-state";
        empty.innerHTML = "<p>Create a conversation to begin.</p>";
        sessionList.appendChild(empty);
        return;
    }

    for (const session of state.sessions) {
        const item = document.createElement("div");
        let className = "session-item";
        if (session.id === state.activeSessionId) className += " active";
        if (session.id === state.processingSessionId) className += " processing";
        item.className = className;
        item.dataset.id = session.id;

        const title = document.createElement("span");
        title.className = "session-title";
        title.textContent = session.title;
        item.appendChild(title);

        const deleteBtn = document.createElement("button");
        deleteBtn.className = "session-delete";
        deleteBtn.textContent = "\u00d7";
        deleteBtn.title = "Delete";
        deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            deleteSession(session.id);
        });
        item.appendChild(deleteBtn);

        item.addEventListener("click", () => switchSession(session.id));
        sessionList.appendChild(item);
    }
}

async function createSession() {
    try {
        const res = await fetch("/api/sessions", { method: "POST" });
        const session = await res.json();
        state.sessions.unshift(session);
        renderSessionList();
        switchSession(session.id);
        // The sidebar names the hub that will answer this chat. It is only
        // re-read on page load, on WS reconnect, and after Save, so a tab left
        // open across a model change would otherwise start a new chat still
        // showing the old one.
        refreshStatusSurfaces();
    } catch (error) {
        console.error("Failed to create session:", error);
    }
}

async function switchSession(sessionId) {
    // Save current input text for the old session
    if (state.activeSessionId) {
        state.savedInputs[state.activeSessionId] = queryInput.value;
    }

    state.activeSessionId = sessionId;
    renderSessionList();

    messagesDiv.textContent = "";
    streamingMessage = null;
    streamingBubble = null;
    streamingText = "";

    // Restore saved input for the new session
    queryInput.value = state.savedInputs[sessionId] || "";
    autoResizeInput();
    updateSendButton();

    let messages = [];
    try {
        const res = await fetch("/api/sessions/" + sessionId + "/messages");
        messages = await res.json();
    } catch (error) {
        console.error("Failed to load messages:", error);
    }

    // Hand messages to the tool tree so it can rebuild from persisted
    // tool_data when the in-memory cache misses (e.g. after page reload).
    toolTree.switchSession(sessionId, messages);

    if (messages.length === 0) {
        welcomeScreen.classList.remove("hidden");
    } else {
        welcomeScreen.classList.add("hidden");
        for (const msg of messages) {
            renderStoredMessage(msg);
        }
        scrollChatToBottom();
    }

    // If the run for this session is still in flight, rebuild a placeholder
    // streaming bubble + thinking trail so incoming text_deltas attach to a
    // labeled trail instead of a fresh "Thinking" stub. The next agent_switch
    // will overwrite the label.
    if (state.processingSessionId === sessionId) {
        ensureStreamingMessage();
        const trail = ensureThinkingTrail(streamingMessage);
        const label = state.lastAgentBySession[sessionId] || "Working...";
        setThinkingTrailLabel(trail, label);
        toolTree.setActiveAgent(label.replace(/ \S+$/, ""));
        scrollChatToBottom();
    }

    queryInput.focus();
}

async function deleteSession(sessionId) {
    try {
        await fetch("/api/sessions/" + sessionId, { method: "DELETE" });
        state.sessions = state.sessions.filter((session) => session.id !== sessionId);
        toolTree.sessionHistory.delete(sessionId);
        delete state.savedInputs[sessionId];
        delete state.lastAgentBySession[sessionId];

        if (state.activeSessionId === sessionId) {
            state.activeSessionId = null;
            messagesDiv.textContent = "";
            welcomeScreen.classList.remove("hidden");

            if (state.sessions.length > 0) {
                switchSession(state.sessions[0].id);
            } else {
                toolTree.switchSession(null);
            }
        }

        renderSessionList();
    } catch (error) {
        console.error("Failed to delete session:", error);
    }
}

function updateSessionTitle(sessionId, title) {
    const session = state.sessions.find((item) => item.id === sessionId);
    if (session) {
        session.title = title;
        renderSessionList();
    }
}

function renderStoredMessage(msg) {
    const msgDiv = createMessageDiv(msg.role, msg.created_at);
    const bubble = msgDiv.querySelector(".message-bubble");

    if (msg.role === "assistant" && typeof marked !== "undefined") {
        renderMarkdownInto(bubble, msg.content);
    } else {
        bubble.textContent = msg.content;
    }

    if (msg.role === "assistant") {
        const parts = [];
        if (msg.created_at) {
            parts.push(formatMessageTimestamp(msg.created_at));
        }
        if (msg.duration_ms) {
            parts.push(formatDuration(msg.duration_ms));
        }
        setMessageMetaDetail(msgDiv, parts.join(" · "));
    }

    messagesDiv.appendChild(msgDiv);
}

async function sendQuery() {
    const text = queryInput.value.trim();
    const activeBlocked = state.processingSessionId === state.activeSessionId;
    if (!text || activeBlocked || !state.ws || !state.isReady) {
        return;
    }

    if (!state.activeSessionId) {
        await createSession();
        if (!state.activeSessionId) {
            return;
        }
    }

    state.processingSessionId = state.activeSessionId;
    updateSendButton();

    welcomeScreen.classList.add("hidden");

    const now = new Date();
    const userMsg = createMessageDiv("user", now);
    userMsg.querySelector(".message-bubble").textContent = text;
    messagesDiv.appendChild(userMsg);

    toolTree.newQuery(text);

    state.ws.send(JSON.stringify({
        type: "query",
        session_id: state.activeSessionId,
        text,
    }));

    queryInput.value = "";
    autoResizeInput();
    scrollChatToBottom();
}

function createMessageDiv(role, createdAt = null) {
    const div = document.createElement("div");
    div.className = "message message-" + role;

    const stack = document.createElement("div");
    stack.className = "message-stack";

    const meta = document.createElement("div");
    meta.className = "message-meta";

    const metaLabel = document.createElement("span");
    metaLabel.className = "message-meta-label";
    metaLabel.textContent = role === "assistant" ? "OBaI" : "You";
    meta.appendChild(metaLabel);

    const metaDetail = document.createElement("span");
    metaDetail.className = "message-meta-detail hidden";
    meta.appendChild(metaDetail);

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";

    stack.appendChild(meta);
    stack.appendChild(bubble);
    div.appendChild(stack);

    if (createdAt) {
        const timestampLabel = formatMessageTimestamp(createdAt);
        div.dataset.timestampLabel = timestampLabel;
        setMessageMetaDetail(div, timestampLabel);
        if (timestampLabel) {
            metaDetail.title = formatExactTimestamp(createdAt);
        }
    }

    return div;
}

function setMessageMetaDetail(messageEl, text) {
    const detail = messageEl.querySelector(".message-meta-detail");
    if (!detail) {
        return;
    }

    if (text) {
        detail.textContent = text;
        detail.classList.remove("hidden");
        return;
    }

    detail.textContent = "";
    detail.classList.add("hidden");
}

function formatMessageTimestamp(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "";
    }

    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    const sameYear = date.getFullYear() === now.getFullYear();

    const options = sameDay
        ? { hour: "numeric", minute: "2-digit" }
        : sameYear
            ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
            : {
                year: "numeric",
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
            };

    return new Intl.DateTimeFormat(undefined, options).format(date);
}

function formatExactTimestamp(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
        return "";
    }

    return new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}

function scrollChatToBottom() {
    chatArea.scrollTop = chatArea.scrollHeight;
}

function updateSendButton() {
    const hasText = queryInput.value.trim().length > 0;
    const activeBlocked = state.processingSessionId === state.activeSessionId;
    sendBtn.disabled = !hasText || activeBlocked || !state.isReady;
}

const thinkingVerbsList = [
    "Arbitraging",
    "Divining",
    "Bull-charging",
    "Candle-gazing",
    "Chart-whispering",
    "Crystal-balling",
    "Edge-hunting",
    "Hedging",
    "Hodling",
    "Liquidity-sniffing",
    "Momentum-surfing",
    "Odds-weighing",
    "Pip-chasing",
    "Prognosticating",
    "Tape-reading",
    "Tea-leafing",
    "Theta-decaying",
    "Trend-spotting",
    "Vol-surfing",
    "Whale-watching",
];

function thinkingVerb() {
    return thinkingVerbsList[Math.floor(Math.random() * thinkingVerbsList.length)];
}

function autoResizeInput() {
    queryInput.style.height = "auto";
    queryInput.style.height = Math.min(queryInput.scrollHeight, 180) + "px";
}

function compactModelName(name) {
    if (!name) {
        return "";
    }

    const value = String(name);
    const slashParts = value.split("/");
    const preferred = slashParts[slashParts.length - 1];
    if (preferred.length <= 30) {
        return preferred;
    }
    return preferred.slice(0, 27) + "...";
}

async function renderPendingHubHint() {
    // Appended to the model summary, which reports the running hub. When the
    // saved settings differ, say so where the user is already looking rather
    // than only inside the settings modal.
    if (!modelSummary) {
        return;
    }

    try {
        const res = await fetch("/api/settings");
        if (!res.ok) {
            return;
        }
        const data = await res.json();
        if (!data.restart_required) {
            return;
        }

        const hint = document.createElement("div");
        hint.className = "model-summary-pending";
        hint.textContent =
            "Restart to apply " + compactModelName(data.saved.hub_model) +
            " / " + data.saved.hub_reasoning_effort;
        hint.title = "Saved in settings but not yet running";
        modelSummary.appendChild(hint);
    } catch (error) {
        console.error("Failed to check pending hub settings:", error);
    }
}

function updateStatusSurfaces(data) {
    if (!data || !modelSummary) {
        return;
    }

    modelSummary.textContent = "";

    if (data.ready && (data.orchestrator_model || data.specialist_model)) {
        const models = [
            ["Hub", data.orchestrator_model],
            ["Agents", data.specialist_model],
        ].filter(([, value]) => Boolean(value));

        for (const [label, model] of models) {
            const row = document.createElement("div");
            row.className = "model-summary-item";
            row.title = model;

            const labelEl = document.createElement("span");
            labelEl.className = "model-summary-label";
            labelEl.textContent = label;

            const valueEl = document.createElement("span");
            valueEl.className = "model-summary-value";
            valueEl.textContent = compactModelName(model);

            row.appendChild(labelEl);
            row.appendChild(valueEl);
            modelSummary.appendChild(row);
        }

        // These rows show what is RUNNING. Without this, a saved-but-not-yet
        // applied change looks like the save silently failed.
        renderPendingHubHint();
        return;
    }

    modelSummary.textContent = data.status || "Initializing agents...";
}

function syncResponsivePanels() {
    const compact = window.matchMedia("(max-width: 1120px)").matches;
    if (state.compactToolsMode === compact) {
        return;
    }

    state.compactToolsMode = compact;
    if (compact) {
        toolPanel.classList.add("hidden");
        showToolsBtn.classList.remove("hidden");
        return;
    }

    toolPanel.classList.remove("hidden");
    showToolsBtn.classList.add("hidden");
}

function applySuggestedPrompt(prompt) {
    queryInput.value = prompt;
    autoResizeInput();
    updateSendButton();
    queryInput.focus();
}

function setupEventListeners() {
    sendBtn.addEventListener("click", sendQuery);

    queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendQuery();
        }
    });

    queryInput.addEventListener("input", () => {
        autoResizeInput();
        updateSendButton();
    });

    document.getElementById("new-session-btn").addEventListener("click", createSession);

    document.getElementById("toggle-tools-btn").addEventListener("click", () => {
        toolPanel.classList.add("hidden");
        showToolsBtn.classList.remove("hidden");
    });

    showToolsBtn.addEventListener("click", () => {
        toolPanel.classList.remove("hidden");
        showToolsBtn.classList.add("hidden");
    });

    document.getElementById("settings-btn")?.addEventListener("click", openSettings);
    document.getElementById("settings-close-btn")?.addEventListener("click", closeSettings);
    document.getElementById("settings-overlay")?.addEventListener("click", (e) => {
        if (e.target.id === "settings-overlay") {
            closeSettings();
        }
    });
    document.getElementById("settings-save-btn")?.addEventListener("click", saveSettings);

    suggestionButtons.forEach((button) => {
        button.addEventListener("click", () => {
            applySuggestedPrompt(button.dataset.prompt || button.textContent || "");
        });
    });

    window.addEventListener("resize", syncResponsivePanels);
}

const settingsOverlay = document.getElementById("settings-overlay");

const prefFields = {
    risk_tolerance: document.getElementById("pref-risk"),
    investment_horizon: document.getElementById("pref-horizon"),
    default_benchmark: document.getElementById("pref-benchmark"),
    initial_capital: document.getElementById("pref-capital"),
    currency: document.getElementById("pref-currency"),
    market: document.getElementById("pref-market"),
};

// Hub model + reasoning effort. These are the only agent settings the user
// owns; specialists stay code-owned. Saving retunes the running web hub in
// place, so the change lands on the next message with no terminal restart.
// An exported ORCHESTRATOR_* variable outranks the file entirely, which the
// notes say; and separate `obai chat`/`obai tui` processes hold their own
// hub, so those still pick the change up when they next launch.
const hubFields = {
    hub_model: document.getElementById("hub-model"),
    hub_reasoning_effort: document.getElementById("hub-effort"),
};

const hubNotes = {
    hub_model: document.getElementById("hub-model-note"),
    hub_reasoning_effort: document.getElementById("hub-effort-note"),
};

// Values last reported by the server, used to skip a PATCH that would change
// nothing. Saving is not free: it takes the hub's query lock, so an untouched
// dropdown must not queue behind an in-flight answer.
let hubSavedSnapshot = null;

function setNote(el, text, warn) {
    if (!el) {
        return;
    }
    el.textContent = text;
    el.classList.toggle("warn", Boolean(warn));
    el.classList.toggle("hidden", !text);
}

function fillChoices(select, choices, selected) {
    if (!select) {
        return;
    }
    select.textContent = "";
    for (const choice of choices || []) {
        const option = document.createElement("option");
        option.value = choice;
        option.textContent = choice;
        select.appendChild(option);
    }
    if (selected) {
        select.value = selected;
    }
}

function isEnvOverride(value) {
    // The server reports an unset variable as null. An exported empty string
    // is still an override — pydantic-settings applies it — so only null and
    // undefined mean "no override".
    return value !== null && value !== undefined;
}

function renderHubSettings(data) {
    const saved = data.saved || {};
    hubSavedSnapshot = { ...saved };
    const choices = data.choices || {};
    const overrides = data.env_overrides || {};
    const envVars = data.env_vars || {};

    for (const [key, el] of Object.entries(hubFields)) {
        fillChoices(el, choices[key], saved[key]);
        // An exported-but-empty variable still outranks the file, so test for
        // presence rather than truthiness — the server does the same.
        const override = overrides[key];
        const warning = isEnvOverride(override)
            ? envVars[key] + '="' + override + '" is set in your environment and outranks this ' +
              "setting. The saved value will not take effect until you unset it and restart."
            : "";
        setNote(hubNotes[key], warning, Boolean(warning));
    }

    const running = data.running || {};
    const runningLine = "Running now: " + (running.hub_model || "unknown") +
        " / " + (running.hub_reasoning_effort || "unknown") + ".";
    // The web hub retunes in place, so a lingering mismatch means the save
    // landed mid-initialization and only a restart will pick it up.
    let applyLine = "";
    if (data.restart_required) {
        applyLine = " Saved values apply after you restart OBaI.";
    } else if (data.pending_apply) {
        applyLine = " Saved values apply once the current answer finishes.";
    }
    setNote(document.getElementById("hub-apply-note"), runningLine + applyLine, false);
}

async function loadHubSettings() {
    const applyNote = document.getElementById("hub-apply-note");
    try {
        const res = await fetch("/api/settings");
        const data = await res.json();

        if (!res.ok) {
            // A broken settings file is repaired by saving a complete pair of
            // values over it, so still offer both dropdowns rather than
            // leaving the user stuck with an error and two empty selects.
            const choices = data.choices || {};
            // Nothing trustworthy is on disk, so forget the last snapshot:
            // every value must be sent to repair the file.
            hubSavedSnapshot = null;
            for (const [key, el] of Object.entries(hubFields)) {
                fillChoices(el, choices[key], null);
            }
            setNote(applyNote, data.error || "Failed to load model settings.", true);
            return;
        }
        renderHubSettings(data);
    } catch (error) {
        console.error("Failed to load model settings:", error);
        hubSavedSnapshot = null;
        setNote(applyNote, "Failed to load model settings.", true);
    }
}

function changedHubFields() {
    // Only the fields the user actually moved. A null snapshot means the load
    // failed or the file was corrupt, in which case every value is sent — a
    // complete pair is the repair path for a broken settings file.
    const body = {};
    for (const [key, el] of Object.entries(hubFields)) {
        if (!el || !el.value) {
            continue;
        }
        if (hubSavedSnapshot === null || hubSavedSnapshot[key] !== el.value) {
            body[key] = el.value;
        }
    }
    return body;
}

async function saveHubSettings() {
    const body = changedHubFields();
    if (!Object.keys(body).length) {
        return { ok: true, unchanged: true, restartRequired: false, pendingApply: false, envPinned: false };
    }

    try {
        const res = await fetch("/api/settings", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();

        if (!res.ok) {
            return { ok: false, error: data.error || "Failed to save model settings" };
        }
        renderHubSettings(data);
        return {
            ok: true,
            restartRequired: Boolean(data.restart_required),
            pendingApply: Boolean(data.pending_apply),
            envPinned: Object.values(data.env_overrides || {}).some(isEnvOverride),
        };
    } catch (error) {
        console.error("Failed to save model settings:", error);
        return { ok: false, error: "Error saving model settings" };
    }
}

function hubSaveStatus(hub) {
    // restartRequired means the server could NOT hot-apply — the hub was
    // still initializing when the save landed. It already excludes env-pinned
    // fields, so both flags can be true at once: one field awaits a restart
    // while a different one is pinned. Report the restart first; hiding it
    // behind the env warning would leave a real pending change looking dead.
    if (hub.restartRequired && hub.envPinned) {
        return {
            text: "Saved — restart OBaI to apply; an environment variable pins the rest",
            sticky: true,
        };
    }
    if (hub.restartRequired) {
        return { text: "Saved — restart OBaI to apply", sticky: true };
    }
    if (hub.envPinned) {
        return { text: "Saved — an environment variable still overrides it", sticky: true };
    }
    if (hub.unchanged) {
        return { text: "Saved", sticky: false };
    }
    if (hub.pendingApply) {
        // The hub was mid-answer, so the change waits rather than switching
        // models underneath the turn that is streaming.
        return { text: "Saved — applies once the current answer finishes", sticky: false };
    }
    return { text: "Saved — applies to your next message", sticky: false };
}

async function openSettings() {
    if (!settingsOverlay) {
        return;
    }

    settingsOverlay.classList.remove("hidden");

    try {
        const res = await fetch("/api/preferences");
        const prefs = await res.json();

        for (const [key, el] of Object.entries(prefFields)) {
            if (prefs[key] !== undefined) {
                el.value = prefs[key];
            }
        }
    } catch (error) {
        console.error("Failed to load preferences:", error);
    }

    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        const opikLink = document.getElementById("opik-link");

        if (data.opik_enabled && data.opik_url) {
            opikLink.href = data.opik_url;
            opikLink.textContent = data.opik_url;
            opikLink.classList.remove("disabled");
        } else {
            opikLink.href = "#";
            opikLink.textContent = "Not running";
            opikLink.classList.add("disabled");
        }
    } catch (error) {
        console.error("Failed to load status:", error);
    }

    await loadHubSettings();
}

function closeSettings() {
    if (!settingsOverlay) {
        return;
    }

    settingsOverlay.classList.add("hidden");
    const statusEl = document.getElementById("settings-status");
    if (statusEl) {
        statusEl.textContent = "";
    }
}

async function savePreferences() {
    const body = {};
    for (const [key, el] of Object.entries(prefFields)) {
        const val = el.value.trim();
        body[key] = key === "initial_capital" ? (val ? parseFloat(val) : 0) : val;
    }

    try {
        const res = await fetch("/api/preferences", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        return res.ok ? { ok: true } : { ok: false, error: "Failed to save" };
    } catch (error) {
        console.error("Failed to save preferences:", error);
        return { ok: false, error: "Error saving" };
    }
}

async function saveSettings() {
    const statusEl = document.getElementById("settings-status");
    if (!statusEl) {
        return;
    }

    statusEl.textContent = "Saving...";
    const prefs = await savePreferences();
    const hub = await saveHubSettings();

    if (!prefs.ok || !hub.ok) {
        // The hub message names the offending field; the preferences one is generic.
        statusEl.textContent = hub.error || prefs.error;
        return;
    }

    const status = hubSaveStatus(hub);
    statusEl.textContent = status.text;
    if (!status.sticky) {
        setTimeout(() => {
            statusEl.textContent = "";
        }, 2000);
    }

    // The sidebar reports the running hub, so a save must refresh it — both
    // to show the newly applied model and, if the hot-apply could not happen,
    // to leave a pending trace after the modal is closed and forgotten.
    refreshStatusSurfaces();
}
