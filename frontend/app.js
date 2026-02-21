const askBtn = document.getElementById("askBtn");
const askSpinner = document.getElementById("askSpinner");
const questionEl = document.getElementById("question");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const introEl = document.getElementById("intro");
const answerCard = document.getElementById("answerCard");
const severityContainer = document.getElementById("severityContainer");
const providersSection = document.getElementById("providersSection");
const providersList = document.getElementById("providersList");

// Configure marked (optional but recommended)
marked.setOptions({
  breaks: true,
  gfm: true,
});

// Severity badge generator
function severityBadge(sev) {
  if (!sev) return "";

  const s = String(sev).toLowerCase();
  let color = "bg-gray-500";

  if (s === "low") color = "bg-green-600";
  else if (s === "medium") color = "bg-yellow-600";
  else if (
    s === "high" ||
    s === "severe" ||
    s === "urgent" ||
    s === "crisis"
  ) {
    color = "bg-red-600";
  }

  return `
    <span class="inline-block px-2 py-1 text-xs font-semibold text-white rounded ${color}">
      ${escapeHtml(String(sev).toUpperCase())}
    </span>
  `;
}

async function askQuestion() {
  const q = questionEl.value.trim();

  if (!q) {
    statusEl.textContent = "Please enter a question.";
    return;
  }

  statusEl.textContent = "Querying…";
  askBtn.setAttribute("disabled", "disabled");
  if (askSpinner) {
    askSpinner.classList.remove("hidden");
  }

  try {
    const resp = await fetch("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });

    if (!resp.ok) throw new Error(`status ${resp.status}`);

    const data = await resp.json();

    if (introEl) introEl.classList.add("hidden");
    resultEl.classList.remove("hidden");

    const answer =
      data.answer || data.response || data.result || JSON.stringify(data);

    // ✅ Proper Markdown Rendering (Reliable)
    const html = marked.parse(answer);
    answerCard.innerHTML = DOMPurify.sanitize(html);

    // Severity Badge
    if (data.severity) {
      severityContainer.innerHTML = severityBadge(data.severity);

      const sev = String(data.severity).toLowerCase();
      if (
        sev === "high" ||
        sev === "severe" ||
        sev === "urgent" ||
        sev === "crisis"
      ) {
        showEmergencyMap();
      }
    } else {
      severityContainer.innerHTML = "";
    }

    // Providers Section
    const providers = data.providers || [];

    if (providers.length) {
      providersSection.classList.remove("hidden");
      providersList.innerHTML = "";

      providers.forEach((p) => {
        const div = document.createElement("div");
        div.className = "p-3 border rounded-md";

        div.innerHTML = `
          <div class="font-medium text-sm">${escapeHtml(p.name || "")}</div>
          <div class="text-xs text-gray-600">${escapeHtml(p.address || "")}</div>
          ${
            p.phone
              ? `<div class="text-xs text-gray-500">${escapeHtml(p.phone)}</div>`
              : ""
          }
        `;

        providersList.appendChild(div);
      });
    } else {
      providersSection.classList.add("hidden");
    }

    statusEl.textContent = "";
  } catch (err) {
    console.error(err);
    statusEl.textContent = "Request failed. Please try again.";
  } finally {
    askBtn.removeAttribute("disabled");
    if (askSpinner) {
      askSpinner.classList.add("hidden");
    }
  }
}

// Escape HTML (for non-markdown injected content like severity/providers)
function escapeHtml(unsafe) {
  if (unsafe == null) return "";
  return String(unsafe)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Emergency Map Modal
function showEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  const iframe = document.getElementById("emergencyMapIframe");
  const openLink = document.getElementById("openMapsLink");

  if (!modal || !iframe || !openLink) return;

  iframe.src =
    "https://www.google.com/maps?q=psychology+center+near+me&output=embed";
  openLink.href =
    "https://www.google.com/maps/search/psychology+center+near+me";

  modal.classList.remove("hidden");
}

function hideEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  if (modal) modal.classList.add("hidden");
}

// Events
askBtn.addEventListener("click", askQuestion);

questionEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    askQuestion();
  }
});

// Privacy note visibility: hide when the question box is visible
function updatePrivacyNoteVisibility() {
  const privacy = document.getElementById("privacyNote");
  if (!privacy) return;

  // Consider the question box visible when it's rendered and not display:none
  const q = document.getElementById("question");
  const isVisible = !!(
    q && q.offsetParent !== null && getComputedStyle(q).display !== "none"
  );

  privacy.style.display = isVisible ? "none" : "";
}

// Observe footer attribute changes (style/class) to detect visibility toggles
const chatFooter = document.getElementById("chatFooter");
if (chatFooter) {
  const mo = new MutationObserver(updatePrivacyNoteVisibility);
  mo.observe(chatFooter, { attributes: true, attributeFilter: ["style", "class"] });
}

// Also run on load and on window events that may change layout
window.addEventListener("load", updatePrivacyNoteVisibility);
window.addEventListener("resize", updatePrivacyNoteVisibility);
// Run once now
updatePrivacyNoteVisibility();
