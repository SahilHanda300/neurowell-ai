const askBtn = document.getElementById("askBtn");
const askSpinner = document.getElementById("askSpinner");
const questionEl = document.getElementById("question");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const introEl = document.getElementById("intro");
const chatMessages = document.getElementById("chatMessages");
const severityContainer = document.getElementById("severityContainer");
const providersSection = document.getElementById("providersSection");
const providersList = document.getElementById("providersList");

// In-memory chat history for context preservation between turns.
// Each entry: { role: 'user'|'assistant', content: '...' }
const chatHistory = [];

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
  else if (s === "high" || s === "severe" || s === "urgent" || s === "crisis") {
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

  // Ensure audio is unlocked/created on the user's gesture so WebAudio
  // can play later when the assistant reply arrives. Some browsers only
  // allow starting/resuming AudioContext within a user interaction.
  try {
    ensureAudioUnlocked();
  } catch (e) {}
  statusEl.textContent = "Querying…";
  askBtn.setAttribute("disabled", "disabled");
  if (askSpinner) {
    askSpinner.classList.remove("hidden");
  }

  try {
    // Optimistically show the user's message in the chat view and record it
    try {
      chatHistory.push({ role: "user", content: q });
      if (chatMessages) {
        renderMessage("user", q);
      }
      // clear the input after enqueueing the user's message
      questionEl.value = "";
    } catch (e) {
      console.warn("chatHistory append failed", e);
    }

    // Send prior chat history so the backend can incorporate context
    const resp = await fetch("/api/qa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: chatHistory }),
    });

    if (!resp.ok) throw new Error(`status ${resp.status}`);

    const data = await resp.json();

    if (introEl) introEl.classList.add("hidden");
    resultEl.classList.remove("hidden");

    // If the server returned a persisted conversation, use it as the
    // authoritative source and render the whole thread.
    const serverConv = Array.isArray(data.conversation)
      ? data.conversation
      : null;

    if (serverConv) {
      // Replace local history and re-render full conversation
      try {
        chatHistory.length = 0;
        serverConv.forEach((m) => chatHistory.push(m));
      } catch (e) {
        console.warn("failed applying server conversation", e);
      }

      if (chatMessages) {
        chatMessages.innerHTML = "";
        serverConv.forEach((m) => {
          renderMessage(m.role || "assistant", m.content || "");
        });
      }
      // If the server returned a conversation as part of this Ask request,
      // play the assistant tone for the most recent assistant message so
      // the user hears feedback even when we re-render the persisted history.
      try {
        const last = serverConv[serverConv.length - 1];
        if (last && (last.role || "").toLowerCase() === "assistant") {
          playAssistantTone()
            .then((started) => {
              if (!started) {
                try {
                  playHtmlFallback();
                } catch (e) {}
              }
            })
            .catch(() => {});
        }
      } catch (e) {}
      // update Clear button visibility after loading server conversation
      try {
        updateClearButtonVisibility();
      } catch (e) {}
    } else {
      const answer =
        data.answer || data.response || data.result || JSON.stringify(data);

      // Append assistant turn to history and render it
      try {
        chatHistory.push({ role: "assistant", content: answer });
        if (chatMessages) {
          renderMessage("assistant", answer, true);
        }
        try {
          updateClearButtonVisibility();
        } catch (e) {}
      } catch (e) {
        console.warn("chatHistory append failed", e);
        if (chatMessages) renderMessage("assistant", answer, true);
      }
    }

    // Severity Badge
    if (data.severity) {
      // Show severity badge and a single "Get Help" button the user can
      // click to open emergency resources. Do not auto-open the modal.
      try {
        severityContainer.innerHTML = "";
        const badgeHtml = severityBadge(data.severity) || "";
        const wrapper = document.createElement("div");
        wrapper.className = "flex items-center gap-2";
        const span = document.createElement("span");
        span.innerHTML = badgeHtml;
        wrapper.appendChild(span);

        // Only show the Get Help button for high/severe-like severities
        const sev = String(data.severity).toLowerCase();
        if (
          sev === "high" ||
          sev === "severe" ||
          sev === "urgent" ||
          sev === "crisis"
        ) {
          const btn = document.createElement("button");
          btn.id = "getHelpBtn";
          btn.className =
            "inline-flex items-center px-3 py-1 text-xs font-medium rounded-md text-white bg-red-600 hover:bg-red-700";
          btn.textContent = "Get Help";
          btn.addEventListener("click", (e) => {
            e.preventDefault();
            showEmergencyMap();
          });
          wrapper.appendChild(btn);
        }

        severityContainer.appendChild(wrapper);
      } catch (e) {
        // Fallback: show plain badge
        try {
          severityContainer.innerHTML = severityBadge(data.severity);
        } catch (ee) {}
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

// Render a message into the `#chatMessages` container
function renderMessage(role, content, playSound = false) {
  if (!chatMessages) return;
  // Build a message row with avatar + bubble. Assistant avatars on left,
  // user avatars on right.
  const row = document.createElement("div");
  row.className = "flex items-start gap-3 py-1 px-2";

  const avatarWrap = document.createElement("div");
  avatarWrap.className = "flex-shrink-0";
  avatarWrap.style.width = "36px";
  avatarWrap.style.height = "36px";

  const bubble = document.createElement("div");
  bubble.style.maxWidth = "85%";

  if (role === "assistant") {
    // assistant: avatar left, bubble to the right
    row.classList.add("justify-start");
    avatarWrap.innerHTML = getAssistantAvatarHtml();
    bubble.className =
      "bg-white text-gray-800 px-3 py-2 rounded-lg border text-sm";
    try {
      const html = marked.parse(content || "");
      bubble.innerHTML = DOMPurify.sanitize(html);
    } catch (e) {
      bubble.textContent = String(content || "");
    }
    row.appendChild(avatarWrap);
    row.appendChild(bubble);
    if (playSound) {
      try {
        // Try to play via WebAudio; if it doesn't start, ensure the HTML
        // fallback is attempted (playAssistantTone returns a Promise<boolean>).
        playAssistantTone().then((started) => {
          if (!started) {
            try {
              playHtmlFallback();
            } catch (e) {}
          }
        });
      } catch (e) {}
    }
  } else {
    // user: bubble first, avatar on the right
    row.classList.add("justify-end");
    bubble.className = "bg-sky-600 text-white px-3 py-2 rounded-lg text-sm";
    bubble.textContent = String(content || "");
    row.appendChild(bubble);
    avatarWrap.innerHTML = getUserAvatarHtml();
    row.appendChild(avatarWrap);
  }

  chatMessages.appendChild(row);

  // Keep chat scrolled to bottom
  try {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  } catch (e) {}
}

// Build user avatar HTML: prefer profile photo if available, else initials
function getUserAvatarHtml() {
  try {
    const img = document.getElementById("profilePhoto");
    const nameEl = document.getElementById("profileName");
    if (img && img.src && img.src.trim()) {
      return `<img src="${escapeHtml(img.src)}" alt="You" class="rounded-full" style="width:36px;height:36px;object-fit:cover;"/>`;
    }
    const name = nameEl ? nameEl.textContent || "" : "";
    const initial = (name.trim().charAt(0) || "U").toUpperCase();
    return `<div class="rounded-full bg-sky-400 text-white flex items-center justify-center" style="width:36px;height:36px;font-weight:600">${escapeHtml(initial)}</div>`;
  } catch (e) {
    return `<div class="rounded-full bg-sky-400 text-white flex items-center justify-center" style="width:36px;height:36px;font-weight:600">U</div>`;
  }
}

// Build assistant avatar HTML (initials NW)
let _assistantAvatarData = null;
function getAssistantAvatarHtml() {
  try {
    if (_assistantAvatarData) return _assistantAvatarData;
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='36' height='36' viewBox='0 0 36 36'><rect width='36' height='36' rx='6' fill='#E6EEF8'/><text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' font-family='Inter, system-ui, -apple-system, Roboto, "Segoe UI", Helvetica, Arial' font-size='14' font-weight='700' fill='#0F172A'>NW</text></svg>`;
    const data = "data:image/svg+xml;utf8," + encodeURIComponent(svg);
    _assistantAvatarData = `<img src="${data}" alt="NeuroWell" class="rounded-full" style="width:36px;height:36px;object-fit:cover;"/>`;
    return _assistantAvatarData;
  } catch (e) {
    return `<div class="rounded-full bg-gray-200" style="width:36px;height:36px"></div>`;
  }
}

// Play a short notification tone for assistant replies using WebAudio
let _audioContext = null;
// Fallback HTML audio element (recorded sound)
let _fallbackAudio = null;
// Track active audio nodes/instances so they are not garbage-collected
// while playing and so we can clean them up after playback finishes.
const _activeAudioPlayers = [];
// Embedded generated WAV data-URI for a short 'ting' tone (generated at runtime)
let _embeddedTingUri = null;

function _makeBeepDataURI(durationMs = 140, freq = 700, sampleRate = 44100) {
  try {
    const samples = Math.floor((sampleRate * durationMs) / 1000);
    const buffer = new ArrayBuffer(44 + samples * 2);
    const view = new DataView(buffer);
    function writeString(view, offset, str) {
      for (let i = 0; i < str.length; i++)
        view.setUint8(offset + i, str.charCodeAt(i));
    }
    writeString(view, 0, "RIFF");
    view.setUint32(4, 36 + samples * 2, true);
    writeString(view, 8, "WAVE");
    writeString(view, 12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, 1, true); // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // byte rate (sampleRate * blockAlign)
    view.setUint16(32, 2, true); // block align (numChannels * bytesPerSample)
    view.setUint16(34, 16, true); // bits per sample
    writeString(view, 36, "data");
    view.setUint32(40, samples * 2, true);
    // PCM samples
    let offset = 44;
    for (let i = 0; i < samples; i++) {
      const t = i / sampleRate;
      // simple sine wave with quick envelope
      const env = Math.min(1, Math.max(0, 1 - i / (samples * 0.9)));
      // Increase amplitude for louder playback while keeping a quick envelope
      const sample = Math.sin(2 * Math.PI * freq * t) * 0.9 * env;
      const s = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, s * 0x7fff, true);
      offset += 2;
    }

    // convert to binary string in chunks to avoid stack limits
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return "data:audio/wav;base64," + btoa(binary);
  } catch (e) {
    return null;
  }
}

// Play a generated tone directly into the AudioContext (avoids decoding data-URIs)
async function playGeneratedBufferTone(
  durationMs = 140,
  freq = 700,
  sampleRate = 44100,
) {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    if (!_audioContext) _audioContext = new AC();
    // resume if needed
    try {
      if (_audioContext.state === "suspended") await _audioContext.resume();
    } catch (e) {}

    const length = Math.floor((sampleRate * durationMs) / 1000);
    const buffer = _audioContext.createBuffer(1, length, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i++) {
      const t = i / sampleRate;
      const env = Math.min(1, Math.max(0, 1 - i / (length * 0.9)));
      // Increase amplitude for louder playback while keeping a quick envelope
      data[i] = Math.sin(2 * Math.PI * freq * t) * 0.9 * env;
    }

    const src = _audioContext.createBufferSource();
    src.buffer = buffer;
    src.connect(_audioContext.destination);
    src.start();
    return new Promise((resolve) => {
      src.onended = () => {
        try {
          src.disconnect();
        } catch (e) {}
        resolve(true);
      };
    });
  } catch (e) {
    console.debug("playGeneratedBufferTone failed", e);
    return false;
  }
}
async function playAssistantTone() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) {
      // No WebAudio support; try HTMLAudio fallback
      console.debug(
        "playAssistantTone: WebAudio not supported, using HTML fallback",
      );
      return playHtmlFallback();
    }

    if (!_audioContext) _audioContext = new AC();

    // If the context is suspended, try to resume and only play via WebAudio
    // if resume succeeds. If resume fails or remains suspended, fall back.
    if (_audioContext.state === "suspended") {
      try {
        console.debug("playAssistantTone: attempting AudioContext.resume()...");
        await _audioContext.resume();
        console.debug("playAssistantTone: AudioContext resumed");
      } catch (e) {
        console.debug(
          "playAssistantTone: resume() rejected, using HTML fallback",
          e,
        );
        return playHtmlFallback();
      }
      // If still not running, fallback
      if (_audioContext.state !== "running") {
        console.debug(
          "playAssistantTone: AudioContext not running after resume, using HTML fallback",
        );
        return playHtmlFallback();
      }
    }

    const o = _audioContext.createOscillator();
    const g = _audioContext.createGain();
    o.type = "sine";
    o.frequency.value = 700;
    g.gain.value = 0;
    o.connect(g);
    g.connect(_audioContext.destination);
    const now = _audioContext.currentTime;
    g.gain.setValueAtTime(0, now);
    // Raised peak gain for a louder but still short "ting" sound
    g.gain.linearRampToValueAtTime(0.5, now + 0.01);
    g.gain.exponentialRampToValueAtTime(0.002, now + 0.13);
    o.start(now);
    const stopAt = now + 0.14;
    o.stop(stopAt);

    // Keep a reference to the nodes until after they stop to ensure full playback.
    const player = { osc: o, gain: g };
    _activeAudioPlayers.push(player);
    // schedule cleanup slightly after the stop time
    const cleanupMs =
      Math.ceil((stopAt - _audioContext.currentTime) * 1000) + 50;
    setTimeout(() => {
      try {
        o.disconnect();
      } catch (e) {}
      try {
        g.disconnect();
      } catch (e) {}
      const idx = _activeAudioPlayers.indexOf(player);
      if (idx >= 0) _activeAudioPlayers.splice(idx, 1);
    }, cleanupMs);
    return true;
  } catch (e) {
    // On any error, try the HTMLAudio fallback (recorded sound)
    console.debug(
      "playAssistantTone: error generating WebAudio tone, falling back",
      e,
    );
    try {
      return playHtmlFallback();
    } catch (ee) {
      console.debug("playAssistantTone: HTML fallback also failed", ee);
    }
  }
  return false;
}

// Play a short recorded sound as a fallback (expects /static/ting.mp3 to exist)
function playHtmlFallback() {
  try {
    // Prefer an embedded data URI; fallback to /static/ting.mp3 if generation failed
    const src = _embeddedTingUri || _makeBeepDataURI() || "/static/ting.mp3";
    console.debug("playHtmlFallback: attempting to play", src);
    // Create a fresh audio instance so rapid successive replies won't cut
    // off an already-playing sound. We still keep a reference in
    // `_activeAudioPlayers` to avoid GC during playback.
    const a = new Audio(src);
    a.preload = "auto";
    try {
      a.volume = 1.0;
    } catch (e) {}
    _activeAudioPlayers.push(a);
    const cleanup = () => {
      const i = _activeAudioPlayers.indexOf(a);
      if (i >= 0) _activeAudioPlayers.splice(i, 1);
      try {
        a.src = "";
      } catch (e) {}
    };
    a.addEventListener("ended", cleanup);
    a.addEventListener("error", cleanup);
    try {
      a.currentTime = 0;
    } catch (e) {}
    return a
      .play()
      .then(() => {
        console.debug("playHtmlFallback: played /static/ting.mp3 successfully");
        return true;
      })
      .catch((err) => {
        console.debug("playHtmlFallback: failed to play /static/ting.mp3", err);
        // If play fails, cleanup reference
        cleanup();
        // Try direct WebAudio buffer-based tone as a last-resort fallback
        try {
          return playGeneratedBufferTone();
        } catch (e) {
          return false;
        }
      });
  } catch (e) {
    console.debug("playHtmlFallback: unexpected error", e);
    return Promise.resolve(false);
  }
}

// Ensure an AudioContext is created/resumed on first user gesture so the
// later asynchronous reply can play a tone. We also attach a one-time
// pointerdown listener to attempt unlocking in other interaction cases.
function ensureAudioUnlocked() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    if (!_audioContext) {
      _audioContext = new AC();
    }
    // resume if suspended (may reject if not a user gesture; that's fine)
    if (_audioContext.state === "suspended") {
      // Try to resume immediately (works if called inside a user gesture).
      try {
        _audioContext.resume();
      } catch (e) {}
      // Additionally create and play a very short silent oscillator within
      // the user gesture to unlock audio autoplay policies on some browsers.
      try {
        const _o = _audioContext.createOscillator();
        const _g = _audioContext.createGain();
        _g.gain.value = 0;
        _o.connect(_g);
        _g.connect(_audioContext.destination);
        const now = _audioContext.currentTime;
        _o.start(now);
        _o.stop(now + 0.01);
        // cleanup shortly after
        setTimeout(() => {
          try {
            _o.disconnect();
          } catch (e) {}
          try {
            _g.disconnect();
          } catch (e) {}
        }, 100);
      } catch (e) {}
    }
    // Also create and preload the HTMLAudio fallback so it's ready to play
    try {
      if (!_fallbackAudio) {
        // Prefer an embedded runtime-generated data URI so the client
        // doesn't need to fetch an external asset.
        try {
          if (!_embeddedTingUri) _embeddedTingUri = _makeBeepDataURI();
          // Expose for debugging from the console
          try {
            window._embeddedTingUri = _embeddedTingUri;
          } catch (e) {}
        } catch (e) {}
        const src = _embeddedTingUri || "/static/ting.mp3";
        _fallbackAudio = new Audio(src);
        try {
          _fallbackAudio.volume = 1.0;
        } catch (e) {}
        _fallbackAudio.preload = "auto";
        // try a quick unlock-play/pause sequence to satisfy autoplay policies
        _fallbackAudio
          .play()
          .then(() => {
            try {
              _fallbackAudio.pause();
              _fallbackAudio.currentTime = 0;
            } catch (e) {}
          })
          .catch(() => {});
      }
    } catch (e) {}

    // Expose a small test helper to play the embedded ting from the console
    try {
      window.testTing = async function () {
        try {
          const src =
            window._embeddedTingUri || _embeddedTingUri || "/static/ting.mp3";
          const a = new Audio(src);
          try {
            a.volume = 1.0;
          } catch (e) {}
          await a.play();
          console.log("testTing: played");
        } catch (e) {
          console.error("testTing: failed", e);
        }
      };
    } catch (e) {}
  } catch (e) {
    // ignore
  }
}

// Try to unlock on the next user pointer interaction as a fallback
try {
  document.addEventListener(
    "pointerdown",
    function _unlockOnce() {
      try {
        ensureAudioUnlocked();
      } catch (e) {}
      document.removeEventListener("pointerdown", _unlockOnce);
    },
    { once: true, capture: true },
  );
} catch (e) {}

// Emergency Map Modal
function showEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  const iframe = document.getElementById("emergencyMapIframe");
  const openLink = document.getElementById("openMapsLink");

  if (!modal || !iframe || !openLink) return;

  // Avoid auto-opening repeatedly in the same browser session once the
  // user has already been shown emergency resources. We store a short
  // session flag in `sessionStorage` so follow-up questions don't keep
  // re-opening the modal.
  try {
    if (sessionStorage.getItem("emergencyModalShown") === "1") {
      return;
    }
  } catch (e) {
    // sessionStorage may be unavailable in some embed contexts; ignore
  }

  iframe.src =
    "https://www.google.com/maps?q=psychology+center+near+me&output=embed";
  openLink.href =
    "https://www.google.com/maps/search/psychology+center+near+me";

  modal.classList.remove("hidden");

  try {
    sessionStorage.setItem("emergencyModalShown", "1");
  } catch (e) {}
}

function hideEmergencyMap() {
  const modal = document.getElementById("emergencyMapModal");
  if (modal) modal.classList.add("hidden");
}

// Events
askBtn.addEventListener("click", askQuestion);

// Clear conversation handler
const clearBtn = document.getElementById("clearBtn");
if (clearBtn) {
  // initial visibility
  try {
    updateClearButtonVisibility();
  } catch (e) {}
  clearBtn.addEventListener("click", () => {
    // Show modal confirmation instead of native confirm()
    showConfirmClearModal();
  });
}

// Show the clear confirmation modal (mobile-friendly)
function showConfirmClearModal() {
  try {
    const modal = document.getElementById("confirmClearModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    // prevent background scroll (mobile)
    document.body.style.overflow = "hidden";
  } catch (e) {}
}

function hideConfirmClearModal() {
  try {
    const modal = document.getElementById("confirmClearModal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  } catch (e) {}
}

// Perform the actual clear operation (called from modal Confirm)
async function performClearConversation() {
  try {
    const cb = document.getElementById("clearBtn");
    if (cb) cb.setAttribute("disabled", "disabled");
    const r = await fetch("/api/clear_conversation", { method: "POST" });
    if (!r.ok) throw new Error(`status ${r.status}`);
    // Clear local chat view and history
    try {
      chatHistory.length = 0;
      try {
        sessionStorage.removeItem("emergencyModalShown");
      } catch (e) {}
      try {
        updateClearButtonVisibility();
      } catch (e) {}
      // Render placeholder view
      renderConversationUI(chatHistory);
      statusEl.textContent = "History cleared.";
    } catch (e) {
      console.warn("local clear failed", e);
    }
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Failed to clear conversation.";
  } finally {
    const cb = document.getElementById("clearBtn");
    if (cb) cb.removeAttribute("disabled");
  }
}

// Wire modal buttons
document.addEventListener("DOMContentLoaded", function () {
  try {
    const confirmBtn = document.getElementById("confirmClearBtn");
    const cancelBtn = document.getElementById("cancelClearBtn");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        hideConfirmClearModal();
        await performClearConversation();
      });
    }
    if (cancelBtn) {
      cancelBtn.addEventListener("click", (e) => {
        e.preventDefault();
        hideConfirmClearModal();
      });
    }
  } catch (e) {
    console.warn("failed wiring confirm modal", e);
  }
});

// Show or hide the Clear button depending on whether we have any chat history
function updateClearButtonVisibility() {
  try {
    const cb = document.getElementById("clearBtn");
    if (!cb) return;
    const has = Array.isArray(chatHistory) && chatHistory.length > 0;
    cb.style.display = has ? "inline-flex" : "none";
  } catch (e) {
    // ignore
  }
}

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
    q &&
    q.offsetParent !== null &&
    getComputedStyle(q).display !== "none"
  );

  privacy.style.display = isVisible ? "none" : "";
}

// Observe footer attribute changes (style/class) to detect visibility toggles
const chatFooter = document.getElementById("chatFooter");
if (chatFooter) {
  const mo = new MutationObserver(updatePrivacyNoteVisibility);
  mo.observe(chatFooter, {
    attributes: true,
    attributeFilter: ["style", "class"],
  });
}

// Also run on load and on window events that may change layout
window.addEventListener("load", updatePrivacyNoteVisibility);
window.addEventListener("resize", updatePrivacyNoteVisibility);
// Run once now
updatePrivacyNoteVisibility();

// Load persisted conversation from the server on page load (if present)
async function loadConversationFromServer() {
  try {
    const r = await fetch("/api/conversation");
    if (!r.ok) return;
    const data = await r.json();
    const conv = Array.isArray(data.conversation) ? data.conversation : [];
    // Replace local history and render the conversation (or placeholder)
    try {
      chatHistory.length = 0;
      conv.forEach((m) => chatHistory.push(m));
    } catch (e) {
      console.warn("failed applying server conversation", e);
    }

    // Render conversation UI (shows placeholder if empty)
    try {
      renderConversationUI(chatHistory);
    } catch (e) {
      console.warn("renderConversationUI failed on load", e);
    }
  } catch (e) {
    console.warn("loadConversationFromServer failed", e);
  }
}

window.addEventListener("load", loadConversationFromServer);
// Audio unlock is performed on Ask (user gesture) via `ensureAudioUnlocked()`
// called at the start of `askQuestion()` so an explicit Enable button is
// not required.

// Render conversation UI from history array (or show placeholder)
function renderConversationUI(historyArray) {
  try {
    if (introEl) introEl.classList.add("hidden");
    if (resultEl) resultEl.classList.remove("hidden");

    if (!chatMessages) return;
    chatMessages.innerHTML = "";

    const has = Array.isArray(historyArray) && historyArray.length > 0;
    if (!has) {
      const placeholder = document.createElement("div");
      placeholder.className = "text-sm text-gray-500 p-6 text-center w-full";
      placeholder.textContent = "No conversations yet";
      chatMessages.appendChild(placeholder);
      severityContainer.innerHTML = "";
      return;
    }

    historyArray.forEach((m) => {
      renderMessage(m.role || "assistant", m.content || "");
    });
    // ensure Clear button visibility updated
    try {
      updateClearButtonVisibility();
    } catch (e) {}
  } catch (e) {
    console.warn("renderConversationUI failed", e);
  }
}
