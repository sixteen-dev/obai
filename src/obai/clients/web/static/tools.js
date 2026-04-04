/**
 * ToolTree — hierarchical tool execution display.
 *
 * Stores tool activity per session so switching conversation tabs
 * shows the correct activity. Supports Opik trace links.
 */

class ToolTree {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.tools = new Map();
        this.currentGroup = null;
        this.activeAgent = null;
        this.agentStatusEl = null;
        this.sessionHistory = new Map();
        this.activeSessionId = null;

        this.renderEmptyState();
    }

    switchSession(sessionId) {
        if (this.activeSessionId) {
            this.sessionHistory.set(this.activeSessionId, this.container.cloneNode(true));
        }

        this.activeSessionId = sessionId;
        this.tools.clear();
        this.currentGroup = null;
        this.clearActiveAgent();

        this.container.textContent = "";
        if (!sessionId) {
            this.renderEmptyState();
            return;
        }

        const saved = this.sessionHistory.get(sessionId);
        if (saved) {
            while (saved.firstChild) {
                this.container.appendChild(saved.firstChild);
            }
            this.sessionHistory.delete(sessionId);
            return;
        }

        this.renderEmptyState();
    }

    newQuery(queryText) {
        if (this.container.querySelector(".tool-empty-state")) {
            this.container.textContent = "";
        }

        const group = document.createElement("section");
        group.className = "tool-query-group";

        const header = document.createElement("div");
        header.className = "tool-query-header";

        const title = document.createElement("div");
        title.className = "tool-query-title";
        title.textContent = queryText.length > 88 ? queryText.slice(0, 85) + "..." : queryText;

        header.appendChild(title);
        group.appendChild(header);

        this.container.appendChild(group);
        this.currentGroup = group;
        this.tools.clear();

        if (this.agentStatusEl) {
            this.agentStatusEl.remove();
            this.agentStatusEl = null;
        }
    }

    setActiveAgent(agentName) {
        if (this.activeAgent === agentName) {
            return;
        }
        this.activeAgent = agentName;

        if (this.agentStatusEl) {
            this.agentStatusEl.remove();
        }

        const el = document.createElement("div");
        el.className = "tool-agent-status";

        const dot = document.createElement("span");
        dot.className = "status-dot";
        el.appendChild(dot);
        el.appendChild(document.createTextNode(agentName + " working"));

        this.agentStatusEl = el;

        if (this.currentGroup) {
            const header = this.currentGroup.querySelector(".tool-query-header");
            if (header && header.nextSibling) {
                this.currentGroup.insertBefore(el, header.nextSibling);
            } else {
                this.currentGroup.appendChild(el);
            }
        }
    }

    clearActiveAgent() {
        if (this.agentStatusEl) {
            this.agentStatusEl.remove();
            this.agentStatusEl = null;
        }
        this.activeAgent = null;
    }

    addTool(callId, agentName, toolName, args, parentId, isMcp) {
        if (this.tools.has(callId)) {
            return;
        }

        const el = document.createElement("div");
        el.className = "tool-item" + (isMcp ? " tool-child" : "");
        el.dataset.callId = callId;

        const spinner = document.createElement("span");
        spinner.className = "tool-spinner";
        el.appendChild(spinner);

        if (!isMcp) {
            const agent = document.createElement("span");
            agent.className = "tool-agent";
            agent.textContent = agentName + ":";
            el.appendChild(agent);
        }

        const name = document.createElement("span");
        name.className = "tool-name";
        name.textContent = toolName;
        el.appendChild(name);

        if (args) {
            const argsEl = document.createElement("span");
            argsEl.className = "tool-args";
            argsEl.textContent = "(" + args + ")";
            el.appendChild(argsEl);
        }

        this.tools.set(callId, el);

        if (!this.currentGroup) {
            return;
        }

        if (isMcp && parentId) {
            const parentEl = this.tools.get(parentId);
            if (parentEl) {
                let insertAfter = parentEl;
                let next = parentEl.nextElementSibling;
                while (
                    next &&
                    next.classList.contains("tool-child") &&
                    next.dataset.parentId === parentId
                ) {
                    insertAfter = next;
                    next = next.nextElementSibling;
                }
                el.dataset.parentId = parentId;
                insertAfter.after(el);
                return;
            }
        }

        this.currentGroup.appendChild(el);
    }

    completeTool(callId, durationMs) {
        const el = this.tools.get(callId);
        if (!el) {
            return;
        }

        const spinner = el.querySelector(".tool-spinner");
        if (spinner) {
            const check = document.createElement("span");
            check.className = "tool-check";
            check.textContent = "\u25CF";
            spinner.replaceWith(check);
        }

        if (!el.querySelector(".tool-timing")) {
            const timing = document.createElement("span");
            timing.className = "tool-timing";
            timing.textContent = formatDuration(durationMs);
            el.appendChild(timing);
        }
    }

    completeAll() {
        for (const [callId, el] of this.tools) {
            if (el.querySelector(".tool-spinner")) {
                this.completeTool(callId, 0);
            }
        }
    }

    addTraceLink(traceId, opikUrl) {
        if (!this.currentGroup || !traceId || !opikUrl) {
            return;
        }

        const existing = this.currentGroup.querySelector(".tool-trace-link");
        if (existing) {
            existing.remove();
        }

        const baseUrl = opikUrl.replace(/\/+$/, "");
        const href = baseUrl + "/api/v1/session/redirect/projects/?trace_id=" + encodeURIComponent(traceId);

        const linkEl = document.createElement("a");
        linkEl.className = "tool-trace-link";
        linkEl.href = href;
        linkEl.target = "_blank";
        linkEl.rel = "noopener";
        linkEl.textContent = "View trace ->";

        this.currentGroup.appendChild(linkEl);
    }

    scrollToBottom() {
        this.container.scrollTop = this.container.scrollHeight;
    }

    renderEmptyState() {
        this.container.textContent = "";

        const empty = document.createElement("div");
        empty.className = "tool-empty-state";
        empty.innerHTML =
            "<h3>No activity yet</h3>" +
            "<p>Tool calls appear here during a run.</p>";

        this.container.appendChild(empty);
    }
}

function formatDuration(ms) {
    if (ms < 1000) {
        return ms + "ms";
    }
    return (ms / 1000).toFixed(1) + "s";
}
