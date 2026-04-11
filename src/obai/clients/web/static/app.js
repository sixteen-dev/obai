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
let thinkingEl = null;
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

        case "agent_switch":
            if (!isActiveSession) break;
            toolTree.setActiveAgent(msg.agent);
            updateThinking(thinkingVerb());
            toolTree.scrollToBottom();
            break;

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

        case "complete":
            handleComplete(msg);
            break;

        case "error":
            handleError(msg);
            break;

        case "session_title":
            updateSessionTitle(msg.session_id, msg.title);
            break;

        case "queued":
            if (!isActiveSession) break;
            updateThinking("Queued, waiting...");
            break;
    }
}

function handleTextDelta(delta) {
    if (!streamingBubble) {
        removeThinking();
        const msgDiv = createMessageDiv("assistant", new Date());
        const bubble = msgDiv.querySelector(".message-bubble");
        bubble.classList.add("streaming");
        bubble.textContent = "";
        messagesDiv.appendChild(msgDiv);
        streamingMessage = msgDiv;
        streamingBubble = bubble;
        streamingText = "";
    }

    streamingText += delta;
    streamingBubble.textContent = streamingText;
    scrollChatToBottom();
}

function handleComplete(msg) {
    const completedSession = msg.session_id || state.activeSessionId;
    if (state.processingSessionId === completedSession) {
        state.processingSessionId = null;
    }
    updateSendButton();

    const isActiveSession = completedSession === state.activeSessionId;
    if (!isActiveSession) return;

    toolTree.completeAll();
    toolTree.clearActiveAgent();
    removeThinking();

    if (msg.trace_id && msg.opik_url) {
        toolTree.addTraceLink(msg.trace_id, msg.opik_url);
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
    if (!isActiveSession) return;

    toolTree.completeAll();
    toolTree.clearActiveAgent();
    removeThinking();

    const msgDiv = createMessageDiv("assistant", new Date());
    msgDiv.classList.add("message-error");
    msgDiv.querySelector(".message-bubble").textContent = msg.message || "An error occurred";
    messagesDiv.appendChild(msgDiv);

    streamingMessage = null;
    streamingBubble = null;
    streamingText = "";
    scrollChatToBottom();
}

function renderMarkdownInto(element, markdownText) {
    const html = marked.parse(normalizeAssistantContent(markdownText));
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
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

function updateThinking(text) {
    if (!thinkingEl) {
        thinkingEl = document.createElement("div");
        thinkingEl.className = "thinking-indicator";

        for (let i = 0; i < 3; i += 1) {
            const dot = document.createElement("span");
            dot.className = "thinking-dot";
            thinkingEl.appendChild(dot);
        }

        const label = document.createElement("span");
        thinkingEl.appendChild(label);
        messagesDiv.appendChild(thinkingEl);
    }

    const label = thinkingEl.lastElementChild;
    if (label) {
        label.textContent = text;
    }
    scrollChatToBottom();
}

function removeThinking() {
    if (thinkingEl) {
        thinkingEl.remove();
        thinkingEl = null;
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
    toolTree.switchSession(sessionId);

    messagesDiv.textContent = "";
    streamingMessage = null;
    streamingBubble = null;
    streamingText = "";
    removeThinking();

    // Restore saved input for the new session
    queryInput.value = state.savedInputs[sessionId] || "";
    autoResizeInput();
    updateSendButton();

    try {
        const res = await fetch("/api/sessions/" + sessionId + "/messages");
        const messages = await res.json();

        if (messages.length === 0) {
            welcomeScreen.classList.remove("hidden");
        } else {
            welcomeScreen.classList.add("hidden");
            for (const msg of messages) {
                renderStoredMessage(msg);
            }
            scrollChatToBottom();
        }
    } catch (error) {
        console.error("Failed to load messages:", error);
    }

    queryInput.focus();
}

async function deleteSession(sessionId) {
    try {
        await fetch("/api/sessions/" + sessionId, { method: "DELETE" });
        state.sessions = state.sessions.filter((session) => session.id !== sessionId);
        toolTree.sessionHistory.delete(sessionId);
        delete state.savedInputs[sessionId];

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
    "OBaI is thinking...",
    "OBaI is researching...",
    "OBaI is analyzing...",
    "OBaI is digging in...",
    "OBaI is looking into it...",
    "OBaI is crunching numbers...",
    "OBaI is pulling data...",
    "OBaI is connecting the dots...",
    "OBaI is on it...",
    "OBaI is working through this...",
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

async function saveSettings() {
    const body = {};
    for (const [key, el] of Object.entries(prefFields)) {
        const val = el.value.trim();
        body[key] = key === "initial_capital" ? (val ? parseFloat(val) : 0) : val;
    }

    const statusEl = document.getElementById("settings-status");
    try {
        const res = await fetch("/api/preferences", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });

        if (res.ok) {
            statusEl.textContent = "Saved";
            setTimeout(() => {
                statusEl.textContent = "";
            }, 2000);
        } else {
            statusEl.textContent = "Failed to save";
        }
    } catch (error) {
        console.error("Failed to save preferences:", error);
        statusEl.textContent = "Error saving";
    }
}
