(function () {
  const launcher = document.getElementById("assistant-open-button");
  const drawer = document.getElementById("assistant-drawer");
  const closeButton = document.getElementById("assistant-close-button");
  const endButton = document.getElementById("assistant-end-button");
  const form = document.getElementById("assistant-form");
  const input = document.getElementById("assistant-input");
  const messages = document.getElementById("assistant-messages");
  const loading = document.getElementById("assistant-loading");
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  if (!launcher || !drawer || !closeButton || !endButton || !form || !input || !messages) {
    return;
  }

  let loaded = false;
  let submitting = false;

  function pageContext() {
    const body = document.body;
    return {
      type: body.dataset.assistantPageType || "unknown",
      id: body.dataset.assistantObjectId || "",
      search_query: body.dataset.assistantSearchQuery || new URLSearchParams(window.location.search).get("q") || "",
      url: window.location.pathname + window.location.search,
    };
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderMarkdown(value) {
    const lines = escapeHtml(value).split(/\r?\n/);
    const rendered = [];
    let inList = false;
    for (const line of lines) {
      if (line.startsWith("## ")) {
        if (inList) {
          rendered.push("</ul>");
          inList = false;
        }
        rendered.push(`<h3>${line.slice(3)}</h3>`);
      } else if (line.startsWith("# ")) {
        if (inList) {
          rendered.push("</ul>");
          inList = false;
        }
        rendered.push(`<h3>${line.slice(2)}</h3>`);
      } else if (line.startsWith("- ")) {
        if (!inList) {
          rendered.push("<ul>");
          inList = true;
        }
        rendered.push(`<li>${line.slice(2)}</li>`);
      } else if (line.trim() === "") {
        if (inList) {
          rendered.push("</ul>");
          inList = false;
        }
      } else {
        if (inList) {
          rendered.push("</ul>");
          inList = false;
        }
        rendered.push(`<p>${line}</p>`);
      }
    }
    if (inList) {
      rendered.push("</ul>");
    }
    return rendered.join("");
  }

  function renderCitations(citations) {
    if (!Array.isArray(citations) || !citations.length) {
      return "";
    }
    const cards = citations
      .slice(0, 6)
      .map((citation) => {
        const title = escapeHtml(citation.title || citation.id);
        const description = escapeHtml(citation.description || "");
        const id = escapeHtml(citation.id || "");
        return `<article class="assistant-citation"><strong>${title}</strong><span>${id}</span>${description ? `<p>${description}</p>` : ""}</article>`;
      })
      .join("");
    return `<div class="assistant-citations" aria-label="引用來源">${cards}</div>`;
  }

  function appendMessage(message) {
    const wrapper = document.createElement("article");
    wrapper.className = `assistant-message assistant-message--${message.role}`;
    const content = document.createElement("div");
    content.className = "assistant-message__content";
    content.innerHTML = renderMarkdown(message.content);
    wrapper.appendChild(content);
    if (message.role === "assistant") {
      const citationHtml = renderCitations(message.citations);
      if (citationHtml) {
        const citationWrapper = document.createElement("div");
        citationWrapper.innerHTML = citationHtml;
        wrapper.appendChild(citationWrapper.firstElementChild);
      }
    }
    messages.appendChild(wrapper);
    messages.scrollTop = messages.scrollHeight;
  }

  function replaceMessages(items) {
    messages.innerHTML = "";
    if (!items.length) {
      appendMessage({
        role: "assistant",
        content: "你好，我可以根據平台已核准的研究資料，協助你探索題目、教師、研究方法與推薦閱讀。",
        citations: [],
      });
      return;
    }
    items.forEach(appendMessage);
  }

  function setLoading(active) {
    submitting = active;
    if (loading) {
      loading.hidden = !active;
    }
    form.querySelector("button[type='submit']").disabled = active;
    input.disabled = active;
  }

  async function fetchJson(url, options) {
    const headers = {
      "Accept": "application/json",
      "X-CSRFToken": csrfToken,
      ...(options && options.headers ? options.headers : {}),
    };
    if (options && options.body) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(url, {
      credentials: "same-origin",
      headers,
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.message || payload.error_code || "Request failed.");
    }
    return payload;
  }

  async function loadSession() {
    if (loaded) {
      return;
    }
    loaded = true;
    try {
      const payload = await fetchJson("/api/v1/ai/assistant/session", { method: "GET" });
      replaceMessages(payload.data.messages || []);
    } catch (error) {
      replaceMessages([
        {
          role: "assistant",
          content: "目前無法載入對話紀錄，請稍後再試。",
          citations: [],
        },
      ]);
    }
  }

  function openDrawer() {
    drawer.hidden = false;
    drawer.setAttribute("aria-hidden", "false");
    launcher.setAttribute("aria-expanded", "true");
    window.sessionStorage.setItem("researchNavigator.drawerOpen", "true");
    loadSession().finally(() => input.focus());
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawer.setAttribute("aria-hidden", "true");
    launcher.setAttribute("aria-expanded", "false");
    window.sessionStorage.setItem("researchNavigator.drawerOpen", "false");
    launcher.focus();
  }

  async function submitMessage(event) {
    event.preventDefault();
    if (submitting) {
      return;
    }
    const text = input.value.trim();
    if (!text) {
      return;
    }
    input.value = "";
    appendMessage({ role: "user", content: text, citations: [] });
    setLoading(true);
    try {
      const payload = await fetchJson("/api/v1/ai/assistant/messages", {
        method: "POST",
        body: JSON.stringify({ message: text, page_context: pageContext() }),
      });
      appendMessage(payload.data.assistant_message);
    } catch (error) {
      appendMessage({
        role: "assistant",
        content: `目前無法完成回覆：${error.message}`,
        citations: [],
      });
    } finally {
      setLoading(false);
      input.focus();
    }
  }

  async function endChat() {
    if (submitting) {
      return;
    }
    setLoading(true);
    try {
      await fetchJson("/api/v1/ai/assistant/end", {
        method: "POST",
        body: JSON.stringify({}),
      });
      loaded = false;
      replaceMessages([]);
    } catch (error) {
      appendMessage({
        role: "assistant",
        content: `目前無法結束對話：${error.message}`,
        citations: [],
      });
    } finally {
      setLoading(false);
      input.focus();
    }
  }

  launcher.addEventListener("click", openDrawer);
  closeButton.addEventListener("click", closeDrawer);
  endButton.addEventListener("click", endChat);
  form.addEventListener("submit", submitMessage);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
    if (event.key === "Escape") {
      closeDrawer();
    }
  });

  if (window.sessionStorage.getItem("researchNavigator.drawerOpen") === "true") {
    openDrawer();
  }
})();
