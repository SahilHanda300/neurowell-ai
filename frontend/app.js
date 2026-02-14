const askBtn = document.getElementById("askBtn");
const questionEl = document.getElementById("question");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const introEl = document.getElementById("intro");
const answerCard = document.getElementById("answerCard");
const severityContainer = document.getElementById("severityContainer");
const providersSection = document.getElementById("providersSection");
const providersList = document.getElementById("providersList");
// sources UI removed

// Lightweight markdown-like renderer: handle headings, numbered lists, bullet lists, bold and italics.
function renderMarkdownLite(text) {
  if (!text && text !== 0) return "";
  // normalize
  let t = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  // split into lines and process blocks
  const lines = t.split("\n");
  const parts = [];
  let i = 0;

  const inlineFmt = (s) => {
    if (!s) return "";
    let out = s;
    // bold: **text** or __text__
    out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/__(.+?)__/g, "<strong>$1</strong>");
    // italic: *text* or _text_
    out = out.replace(/\*(.+?)\*/g, "<em>$1</em>");
    out = out.replace(/_(.+?)_/g, "<em>$1</em>");
    return out;
  };

  while (i < lines.length) {
    let line = (lines[i] || "").trim();
    if (!line) {
      i++;
      continue;
    }
    // headings (#, ##, ...)
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const content = escapeHtml(h[2]);
      parts.push(
        `<h3 class="text-sm font-semibold text-gray-800">${inlineFmt(content)}</h3>`,
      );
      i++;
      continue;
    }

    // numbered list
    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].trim().replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      const itemsHtml = items
        .map((it) => `<li>${inlineFmt(escapeHtml(it))}</li>`)
        .join("");
      parts.push(
        `<ol class="list-decimal list-inside text-sm text-gray-700">${itemsHtml}</ol>`,
      );
      continue;
    }

    // bullet list (-, *, •, +)
    if (/^[-•\*\+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-•\*\+]\s+/.test(lines[i])) {
        items.push(lines[i].trim().replace(/^\s*[-•\*\+]\s+/, ""));
        i++;
      }
      const itemsHtml = items
        .map((it) => `<li>${inlineFmt(escapeHtml(it))}</li>`)
        .join("");
      parts.push(
        `<ul class="list-disc list-inside text-sm text-gray-700">${itemsHtml}</ul>`,
      );
      continue;
    }

    // paragraph (collect until blank line)
    let para = line;
    i++;
    while (i < lines.length && (lines[i] || "").trim()) {
      para += " " + lines[i].trim();
      i++;
    }
    parts.push(
      `<p class="text-sm text-gray-700">${inlineFmt(escapeHtml(para))}</p>`,
    );
  }

  return parts.join("");
}

function clearResult() {
  resultEl.classList.add("hidden");
  if (introEl) introEl.classList.remove("hidden");
  answerCard.innerHTML = "";
  severityContainer.innerHTML = "";
  providersList.innerHTML = "";
  // Sources UI removed — answers displayed directly without source list
  providersSection.classList.add("hidden");
  // sources UI removed
  // clear any previous full-response debug output (debug removed)
}

function severityBadge(sev) {
  if (!sev) return "";
  const s = sev.toLowerCase();
  const color =
    s === "high"
      ? "bg-red-600"
      : s === "medium"
        ? "bg-yellow-600"
        : "bg-green-600";
  return `<span class="text-xs font-semibold text-white ${color} px-2 py-1 rounded-full">${sev.toUpperCase()}</span>`;
}

async function askQuestion() {
  clearResult();
  const q = questionEl.value.trim();
  if (!q) {
    statusEl.textContent = "Please enter a question.";
    return;
  }

  statusEl.textContent = "Querying…";
  askBtn.setAttribute("disabled", "disabled");

  try {
    const resp = await fetch("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    if (!resp.ok) {
      try {
        const txt = await resp.text();
        console.warn("/api/qa returned non-ok", resp.status, txt);
        throw new Error(txt || `status ${resp.status}`);
      } catch (e) {
        console.warn(
          "/api/qa returned non-ok (failed to read body)",
          resp.status,
        );
        throw new Error(`status ${resp.status}`);
      }
    }
    const data = await resp.json();

    // Render
    if (introEl) introEl.classList.add("hidden");
    resultEl.classList.remove("hidden");
    const answer =
      data.answer || data.response || data.result || JSON.stringify(data);
    if (typeof answer === "string") {
      try {
        const html = renderMarkdownLite(answer);
        answerCard.innerHTML =
          typeof DOMPurify !== "undefined" ? DOMPurify.sanitize(html) : html;
      } catch (e) {
        console.error("answer rendering error", e);
        answerCard.textContent = answer;
      }
    } else {
      answerCard.textContent = JSON.stringify(answer, null, 2);
    }

    if (data.severity) {
      severityContainer.innerHTML = severityBadge(data.severity);
      // If backend marks severity as 'high' or 'severe', show emergency map (client-side only)
      try {
        const sev = String(data.severity).toLowerCase();
        if (
          sev === "high" ||
          sev === "severe" ||
          sev === "urgent" ||
          sev === "crisis"
        ) {
          showEmergencyMap();
        }
      } catch (e) {
        // ignore
      }
    }

    const providers = data.providers || [];
    if (providers.length) {
      providersSection.classList.remove("hidden");
      providers.forEach((p) => {
        const div = document.createElement("div");
        div.className = "p-3 border rounded-md";
        const name = p.name || p.title || "";
        const addr = p.address || p.vicinity || "";
        const phone = p.phone || p.formatted_phone_number || "";
        div.innerHTML = `<div class="font-medium text-sm">${escapeHtml(name)}</div><div class="text-xs text-gray-600">${escapeHtml(addr)}</div>${phone ? `<div class="text-xs text-gray-500">${escapeHtml(phone)}</div>` : ""}`;
        providersList.appendChild(div);
      });
    }

    // Sources removed by design — do not render data.sources

    // debug full-response view removed; only concise answer displayed
    statusEl.textContent = "";
  } catch (err) {
    console.error(err);
    statusEl.textContent = "Request failed. Please try again.";
  } finally {
    askBtn.removeAttribute("disabled");
  }
}

function escapeHtml(unsafe) {
  if (unsafe === null || unsafe === undefined) return "";
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

askBtn.addEventListener("click", askQuestion);
questionEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    askQuestion();
  }
});

// --- Autocomplete: fetch topics and show suggestions as the user types ---
let _topics = [];
const suggestionsEl = document.getElementById("suggestions");
let _selectedIndex = -1;

async function fetchTopics() {
  try {
    const res = await fetch("/api/topics");
    if (!res.ok) {
      try {
        const txt = await res.text();
        console.warn("/api/topics returned non-ok", res.status, txt);
      } catch (e) {
        console.warn("/api/topics returned non-ok", res.status);
      }
      return [];
    }
    const j = await res.json();
    return Array.isArray(j.topics) ? j.topics : j.topics || [];
  } catch (e) {
    return [];
  }
}

function showSuggestions(list) {
  if (!suggestionsEl) return;
  if (!list || list.length === 0) {
    suggestionsEl.classList.add("hidden");
    suggestionsEl.innerHTML = "";
    _selectedIndex = -1;
    return;
  }
  // Leave room for the Ask button on the right so the popup doesn't
  // extend underneath it on narrow screens.
  try {
    const rightPad = 96; // px reserved for Ask button + gap
    suggestionsEl.style.right = rightPad + "px";
    suggestionsEl.style.maxWidth = `calc(100% - ${rightPad}px)`;
    suggestionsEl.style.boxSizing = "border-box";
    suggestionsEl.style.zIndex = 50;
  } catch (e) {
    // ignore styling errors
  }
  suggestionsEl.classList.remove("hidden");
  suggestionsEl.innerHTML = list
    .map(
      (t, i) =>
        `<div data-idx="${i}" class="px-3 py-2 hover:bg-gray-100 cursor-pointer" role="option" aria-selected="false">${escapeHtml(t)}</div>`,
    )
    .join("");
  // reset selection
  _selectedIndex = -1;
}

function filterTopics(prefix) {
  if (!prefix) return [];
  const p = prefix.toLowerCase();
  return _topics.filter((t) => t.toLowerCase().startsWith(p)).slice(0, 8);
}

questionEl.addEventListener("input", (e) => {
  const val = e.target.value || "";
  // show suggestions only for short single-word prefixes
  const words = val.trim().split(/\s+/);
  const last = words[words.length - 1] || "";
  if (last.length >= 2) {
    const list = filterTopics(last);
    showSuggestions(list.map((s) => s));
  } else {
    showSuggestions([]);
  }
});

// Click handler for suggestions
document.addEventListener("click", (e) => {
  if (!suggestionsEl) return;
  const item = e.target.closest("#suggestions > div");
  if (item) {
    const text = item.textContent || item.innerText || "";
    // replace the last token in textarea with the suggestion
    const cur = questionEl.value || "";
    const parts = cur.split(/(\s+)/);
    // replace last word token
    for (let i = parts.length - 1; i >= 0; i--) {
      if (!/\s+/.test(parts[i])) {
        parts[i] = text;
        break;
      }
    }
    questionEl.value = parts.join("");
    showSuggestions([]);
    questionEl.focus();
  } else {
    // click outside suggestions hides it
    if (!e.target.closest("#suggestions")) showSuggestions([]);
  }
});

// Helper to visually mark selection
function updateSelection(newIndex) {
  const items = suggestionsEl
    ? Array.from(suggestionsEl.querySelectorAll("div[data-idx]"))
    : [];
  if (!items.length) return;
  // clamp
  if (newIndex < 0) newIndex = items.length - 1;
  if (newIndex >= items.length) newIndex = 0;
  // clear previous
  items.forEach((it, idx) => {
    it.classList.remove("bg-sky-100");
    it.setAttribute("aria-selected", "false");
  });
  const selected = items[newIndex];
  if (selected) {
    selected.classList.add("bg-sky-100");
    selected.setAttribute("aria-selected", "true");
    _selectedIndex = newIndex;
    // ensure visible
    selected.scrollIntoView({ block: "nearest" });
  }
}

// Keyboard handling for suggestions: arrows + Enter + Escape
questionEl.addEventListener("keydown", (e) => {
  // existing Ctrl/Cmd+Enter behavior
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    askQuestion();
    return;
  }

  // when suggestions visible
  if (suggestionsEl && !suggestionsEl.classList.contains("hidden")) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      updateSelection(_selectedIndex + 1);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      updateSelection(_selectedIndex - 1);
      return;
    }
    if (e.key === "Enter") {
      // accept selected suggestion if any
      if (_selectedIndex >= 0) {
        e.preventDefault();
        const sel = suggestionsEl.querySelector(
          `div[data-idx=\"${_selectedIndex}\"]`,
        );
        if (sel) {
          const text = sel.textContent || sel.innerText || "";
          // replace last token in textarea
          const cur = questionEl.value || "";
          const parts = cur.split(/(\s+)/);
          for (let i = parts.length - 1; i >= 0; i--) {
            if (!/\s+/.test(parts[i])) {
              parts[i] = text;
              break;
            }
          }
          questionEl.value = parts.join("");
          showSuggestions([]);
        }
      }
      return;
    }
    if (e.key === "Escape") {
      showSuggestions([]);
      return;
    }
  }
});

// Initialize topics on load
window.addEventListener("DOMContentLoaded", async () => {
  _topics = await fetchTopics();
});

// Heuristic to detect severe/emergency queries client-side
function isSevereQuery(lowerQ) {
  if (!lowerQ) return false;
  const keywords = [
    "suicide",
    "kill myself",
    "end my life",
    "self-harm",
    "hurt myself",
    "want to die",
    "cant go on",
    "can't go on",
    "immediate help",
    "in danger",
    "crisis",
    "urgent",
    "panic attack not breathing",
  ];
  return keywords.some((k) => lowerQ.includes(k));
}

// Show emergency map modal. Attempts to center map on user's geolocation if available.
function showEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  const iframe = document.getElementById("emergencyMapIframe");
  const openLink = document.getElementById("openMapsLink");
  if (!modal || !iframe || !openLink) return;
  // Try geolocation to center the search
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lat = pos.coords.latitude;
        const lng = pos.coords.longitude;
        const q = encodeURIComponent("psychology center");
        const embedUrl = `https://www.google.com/maps?q=${q}+near+${lat},${lng}&output=embed`;
        const openUrl = `https://www.google.com/maps/search/${q}/@${lat},${lng},13z`;
        iframe.src = embedUrl;
        openLink.href = openUrl;
        modal.classList.remove("hidden");
      },
      (err) => {
        // If geolocation fails/denied, fall back to generic nearby search
        iframe.src =
          "https://www.google.com/maps?q=psychology+center+near+me&output=embed";
        openLink.href =
          "https://www.google.com/maps/search/psychology+center+near+me";
        modal.classList.remove("hidden");
      },
      { timeout: 3000 },
    );
  } else {
    iframe.src =
      "https://www.google.com/maps?q=psychology+center+near+me&output=embed";
    openLink.href =
      "https://www.google.com/maps/search/psychology+center+near+me";
    modal.classList.remove("hidden");
  }
}

function hideEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  if (!modal) return;
  modal.classList.add("hidden");
}

// Emergency follow-up UI removed.
