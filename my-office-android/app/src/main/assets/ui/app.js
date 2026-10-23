// MyOffice Mobile v1.5.0 — Flagship Mobile Office Suite Controller
// Multi-touch Pinch-Zoom · Continuous PDF · Sticky Sheets · Filmstrip Slides · Auto-Save
(function () {
  const pendingCalls = new Map();
  let reqSeq = 0;

  window.__resolveNativeCall = function (reqId, jsonStr) {
    const waiter = pendingCalls.get(reqId);
    if (!waiter) return;
    pendingCalls.delete(reqId);
    try {
      const parsed = JSON.parse(jsonStr);
      if (parsed && parsed.ok === false) waiter.reject(new Error(parsed.error || "Operation failed"));
      else waiter.resolve(parsed);
    } catch (err) {
      waiter.reject(err);
    }
  };

  function nativeCall(action, args = {}) {
    return new Promise((resolve, reject) => {
      if (!window.AndroidNative) {
        reject(new Error("Native bridge unavailable"));
        return;
      }
      const id = "r" + (++reqSeq);
      pendingCalls.set(id, { resolve, reject });
      window.AndroidNative.callAsync(id, action, JSON.stringify(args));
    });
  }

  const I18N = {
    en: {
      dir: "ltr",
      docsCount: (n) => `${n} ${n === 1 ? "document" : "documents"}`,
      screenFiles: "Workspace",
      screenSync: "Two-Way Sync",
      screenCollab: "Collaborate",
      screenTools: "Suite Tools",
      screenAi: "Assistant",
      searchPlaceholder: "Search documents, sheets, PDFs...",
      writer: "Writer",
      sheets: "Sheets",
      slides: "Slides",
      scan: "Scan",
      importBtn: "Import",
      newBtn: "New",
      fltAll: "All",
      fltStarred: "Starred",
      fltPhotos: "Images",
      fltVault: "Vault",
      navHome: "Files",
      navSync: "Sync",
      navCollab: "Collab",
      navTools: "Tools",
      navAi: "Assistant",
      syncPc: "PC Wi-Fi",
      syncHotspot: "Hotspot",
      syncRouter: "Router",
      pcSyncTitle: "Pair & Sync with Windows PC",
      pcSyncDesc: "Open MyOffice on your PC (Ctrl+Alt+Y) and scan its QR code or find your PC on Wi-Fi without internet.",
      scanQr: "Scan PC QR",
      findPc: "Find PC",
      lblPcUrl: "PC Address or Pairing Link",
      lblPin: "6-Digit Pairing PIN",
      pairSyncNow: "Pair & Sync Now",
      hotspotTitle: "Host Sync Server on This Phone",
      hotspotDesc: "Turn this phone into the sync server when using Mobile Hotspot so your laptop connects directly to your phone.",
      startServer: "Start Server",
      stopServer: "Stop Server",
      gmailTitle: "Automatic Sync via Gmail",
      gmailDesc: "Syncs two-way over the internet with Desktop MyOffice using your Gmail address and a 16-character Google App Password.",
      syncGmailBtn: "Save & Sync via Gmail",
      routerTitle: "Wi-Fi Router USB / NAS Backup",
      routerDesc: "Sync directly with a USB drive plugged into your home or office Wi-Fi router over FTP.",
      syncRouterBtn: "Sync with Router Storage",
      optVaultSync: "Encrypt remote copies with Hardware Vault (.movault)",
      optAutoSync: "Automatic background sync every 90 seconds",
      collabTitle: "Live Co-Editing & Chat",
      collabDesc: "Co-edit documents in real time with Windows PC and team members over Wi-Fi or Hotspot.",
      yourName: "Your Name",
      roomLink: "Room Link",
      joinRoom: "Connect to Live Room",
      sendBtn: "Send",
      openShared: "Open Shared File",
      leaveBtn: "Disconnect",
      utilities: "Utilities",
      tScanTitle: "Scan Document with Camera",
      tScanSub: "Capture page image & convert cleanly to PDF",
      tImgPdf: "Images to PDF",
      tMerge: "Merge PDFs",
      tSplit: "Split or Extract Pages",
      tCompress: "Compress PDF",
      tProtect: "Protect PDF (AES-256)",
      tUnlock: "Unlock Encrypted PDF",
      tWatermark: "Add Diagonal Watermark",
      tStamp: "Apply Approval Stamp",
      tPageNum: "Add Page Numbers",
      tExtract: "Extract PDF Text to File",
      tFlatten: "Flatten PDF Forms",
      aiTitle: "Document Assistant",
      modelSettings: "Model Settings",
      aiDesc: "Draft, translate between English, Urdu & Hindi, or query any workspace PDF with page citations.",
      groundPdf: "Reference PDF (Optional Page Search)",
      instruction: "Instruction or Question",
      runAssistant: "Run Assistant",
      prefsTitle: "Preferences & Device",
      prefLang: "Interface Language",
      prefTheme: "Appearance",
      themeLight: "Paper Light",
      themeDark: "Carbon Dark"
    },
    ur: {
      dir: "rtl",
      docsCount: (n) => `${n} دستاویزات`,
      screenFiles: "ورک اسپیس",
      screenSync: "ٹو وے سنک",
      screenCollab: "مشترکہ کام",
      screenTools: "آفس ٹولز",
      screenAi: "معاون",
      searchPlaceholder: "دستاویزات، شیٹس اور PDF تلاش کریں...",
      writer: "رائٹر",
      sheets: "شیٹس",
      slides: "سلائیڈز",
      scan: "اسکین",
      importBtn: "کھولیں",
      newBtn: "نئی فائل",
      fltAll: "تمام",
      fltStarred: "پسندیدہ",
      fltPhotos: "تصاویر",
      fltVault: "والٹ",
      navHome: "فائلیں",
      navSync: "سنک",
      navCollab: "لائیو روم",
      navTools: "ٹولز",
      navAi: "معاون",
      syncPc: "پی سی وائی فائی",
      syncHotspot: "ہاٹ اسپاٹ",
      syncRouter: "راؤٹر",
      pcSyncTitle: "کمپیوٹر کے ساتھ فوری سنک",
      pcSyncDesc: "اپنے کمپیوٹر پر MyOffice کھولیں (Ctrl+Alt+Y) اور بغیر انٹرنیٹ QR کوڈ اسکین کریں یا وائی فائی پر تلاش کریں۔",
      scanQr: "QR اسکین کریں",
      findPc: "کمپیوٹر تلاش کریں",
      lblPcUrl: "کمپیوٹر ایڈریس یا لنک",
      lblPin: "6 ہندسوں کا پن کوڈ",
      pairSyncNow: "جڑیں اور سنک کریں",
      hotspotTitle: "فون ہاٹ اسپاٹ سرور",
      hotspotDesc: "موبائل ہاٹ اسپاٹ استعمال کرتے وقت فون پر سنک سرور چلائیں تاکہ لیپ ٹاپ براہ راست جڑ سکے۔",
      startServer: "سرور چلائیں",
      stopServer: "بند کریں",
      gmailTitle: "جی میل کلاؤڈ آٹو سنک",
      gmailDesc: "جی میل ایڈریس اور 16 ہندسوں کے ایپ پاس ورڈ سے دنیا بھر میں کمپیوٹر اور فون سنک رکھیں۔",
      syncGmailBtn: "جی میل سے سنک کریں",
      routerTitle: "وائی فائی راؤٹر USB بیک اپ",
      routerDesc: "راؤٹر میں لگی USB ڈرائیو کے ساتھ براہ راست فائل سنک کریں۔",
      syncRouterBtn: "راؤٹر USB سنک کریں",
      optVaultSync: "والٹ سیکیورٹی (.movault) کے ساتھ محفوظ کریں",
      optAutoSync: "ہر 90 سیکنڈ بعد خودکار سنک",
      collabTitle: "لائیو ایڈیٹنگ اور ٹیم چیٹ",
      collabDesc: "کمپیوٹر اور موبائل پر ایک ہی وقت میں دستاویز ایڈٹ اور چیٹ کریں۔",
      yourName: "آپ کا نام",
      roomLink: "روم لنک",
      joinRoom: "لائیو روم میں شامل ہوں",
      sendBtn: "بھیجیں",
      openShared: "مشترکہ فائل کھولیں",
      leaveBtn: "الگ ہوں",
      utilities: "مزید ٹولز",
      tScanTitle: "کیمرے سے دستاویز اسکین کریں",
      tScanSub: "صفحے کی تصویر لیں اور صاف PDF بنائیں",
      tImgPdf: "تصاویر سے PDF بنائیں",
      tMerge: "PDF فائلیں یکجا کریں",
      tSplit: "PDF صفحات الگ کریں",
      tCompress: "PDF سائز کم کریں",
      tProtect: "PDF لاک لگائیں (AES-256)",
      tUnlock: "PDF پاس ورڈ ہٹائیں",
      tWatermark: "واٹر مارک لگائیں",
      tStamp: "منظوری مہر (Stamp) لگائیں",
      tPageNum: "صفحہ نمبر درج کریں",
      tExtract: "PDF متن نکالیں",
      tFlatten: "PDF فارم لاک کریں",
      aiTitle: "دستاویزی معاون",
      modelSettings: "ماڈل سیٹنگز",
      aiDesc: "خلاصہ بنائیں، اردو/انگریزی/ہندی ترجمہ کریں یا PDF سے سوالات پوچھیں۔",
      groundPdf: "حوالہ PDF منتخب کریں",
      instruction: "ہدایت یا سوال لکھیں",
      runAssistant: "جواب حاصل کریں",
      prefsTitle: "ترجیحات اور ڈیوائس",
      prefLang: "زبان منتخب کریں",
      prefTheme: "تھیم",
      themeLight: "روشن کاغذ",
      themeDark: "ڈارک موڈ"
    },
    hi: {
      dir: "ltr",
      docsCount: (n) => `${n} दस्तावेज़`,
      screenFiles: "वर्कस्पेस",
      screenSync: "टू-वे सिंक",
      screenCollab: "कोलैब",
      screenTools: "टूल्स",
      screenAi: "सहायक",
      searchPlaceholder: "दस्तावेज़, शीट्स व PDF खोजें...",
      writer: "राइटर",
      sheets: "शीट्स",
      slides: "स्लाइड्स",
      scan: "स्कैन",
      importBtn: "इम्पोर्ट",
      newBtn: "नया",
      fltAll: "सभी",
      fltStarred: "स्टार की गई",
      fltPhotos: "फ़ोटो",
      fltVault: "वॉल्ट",
      navHome: "फ़ाइलें",
      navSync: "सिंक",
      navCollab: "कोलैब",
      navTools: "टूल्स",
      navAi: "सहायक",
      syncPc: "PC वाई-फ़ाई",
      syncHotspot: "हॉटस्पॉट",
      syncRouter: "राउटर",
      pcSyncTitle: "Windows PC के साथ पेयर और सिंक",
      pcSyncDesc: "PC पर MyOffice खोलें (Ctrl+Alt+Y) और बिना इंटरनेट QR कोड स्कैन करें या PC खोजें।",
      scanQr: "QR स्कैन करें",
      findPc: "PC खोजें",
      lblPcUrl: "PC पता या लिंक",
      lblPin: "6-अंकीय पिन",
      pairSyncNow: "पेयर और सिंक करें",
      hotspotTitle: "फ़ोन हॉटस्पॉट सर्वर",
      hotspotDesc: "मोबाइल हॉटस्पॉट पर इस फ़ोन को सिंक सर्वर बनाएँ ताकि लैपटॉप सीधे जुड़ सके।",
      startServer: "सर्वर शुरू करें",
      stopServer: "बंद करें",
      gmailTitle: "Gmail क्लाउड ऑटो-सिंक",
      gmailDesc: "Gmail पते और 16-अंकीय App Password से कहीं से भी PC और फ़ोन सिंक करें।",
      syncGmailBtn: "Gmail से सिंक करें",
      routerTitle: "वाई-फ़ाई राउटर USB बैकअप",
      routerDesc: "राउटर से जुड़ी USB ड्राइव पर सीधे फ़ाइल सिंक करें।",
      syncRouterBtn: "राउटर स्टोरेज सिंक करें",
      optVaultSync: "Hardware Vault (.movault) से एन्क्रिप्ट करें",
      optAutoSync: "हर 90 सेकंड में ऑटो-सिंक",
      collabTitle: "लाइव सह-संपादन और चैट",
      collabDesc: "PC और मोबाइल पर एक साथ दस्तावेज़ संपादित करें और चैट करें।",
      yourName: "आपका नाम",
      roomLink: "रूम लिंक",
      joinRoom: "लाइव रूम से जुड़ें",
      sendBtn: "भेजें",
      openShared: "साझा फ़ाइल खोलें",
      leaveBtn: "हटें",
      utilities: "उपयोगिताएँ",
      tScanTitle: "कैमरे से दस्तावेज़ स्कैन करें",
      tScanSub: "पृष्ठ फ़ोटो लें और साफ़ PDF बनाएँ",
      tImgPdf: "फ़ोटो से PDF बनाएँ",
      tMerge: "PDF जोड़ें",
      tSplit: "PDF पृष्ठ अलग करें",
      tCompress: "PDF छोटा करें",
      tProtect: "PDF पासवर्ड सुरक्षा (AES-256)",
      tUnlock: "PDF अनलॉक करें",
      tWatermark: "वॉटरमार्क जोड़ें",
      tStamp: "अप्रूवल स्टैम्प लगाएँ",
      tPageNum: "पृष्ठ संख्या जोड़ें",
      tExtract: "PDF पाठ निकालें",
      tFlatten: "PDF फ़ॉर्म लॉक करें",
      aiTitle: "दस्तावेज़ सहायक",
      modelSettings: "मॉडल सेटिंग्स",
      aiDesc: "सारांश बनाएँ, हिंदी/उर्दू/अंग्रेज़ी अनुवाद करें या PDF से प्रश्न पूछें।",
      groundPdf: "संदर्भ PDF चुनें",
      instruction: "निर्देश या प्रश्न",
      runAssistant: "उत्तर प्राप्त करें",
      prefsTitle: "प्राथमिकताएँ और डिवाइस",
      prefLang: "भाषा चुनें",
      prefTheme: "दिखावट",
      themeLight: "पेपर लाइट",
      themeDark: "कार्बन डार्क"
    }
  };

  const state = {
    lang: "en",
    theme: "light",
    activeTab: "home",
    filter: "all",
    files: [],
    config: {},
    version: "1.6.0",
    nativeVersion: "1.6.0",
    pendingOta: null,
    deviceInfo: { name: "Android Device", ip: "127.0.0.1", keyId: "—" },
    doc: null,
    autoSaveTimer: null,
    collabRoom: null,
    collabMeta: null,
    collabMessages: [],
    collabUnread: 0,
    photoFilter: { bright: 100, contrast: 100, sat: 100, gray: 0, rotate: 0 },
    slideShowIdx: 0,
    sheetPromptResolver: null
  };

  function svgIcon(name, cls = "ico") {
    return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  }

  function toast(msg) {
    const el = document.getElementById("toastEl");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtSize(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }

  function fmtDateShort(ms) {
    try {
      return new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch (_) {
      return "";
    }
  }

  function formatSpec(type, ext) {
    const map = {
      writer: { label: "DOCX", fg: "var(--fmt-writer)", bg: "var(--fmt-writer-bg)" },
      sheet: { label: "XLSX", fg: "var(--fmt-sheet)", bg: "var(--fmt-sheet-bg)" },
      slide: { label: "PPTX", fg: "var(--fmt-slide)", bg: "var(--fmt-slide-bg)" },
      pdf: { label: "PDF", fg: "var(--fmt-pdf)", bg: "var(--fmt-pdf-bg)" },
      photos: { label: "IMG", fg: "var(--fmt-photos)", bg: "var(--fmt-photos-bg)" },
      vault: { label: "VAULT", fg: "var(--fmt-vault)", bg: "var(--fmt-vault-bg)" }
    };
    if (map[type]) return map[type];
    return { label: (ext || "FILE").toUpperCase().slice(0, 4), fg: "var(--ink-secondary)", bg: "var(--surface-subtle)" };
  }

  function applyTheme(theme, persist = false) {
    state.theme = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", state.theme);
    try {
      if (window.AndroidNative && typeof window.AndroidNative.applyNativeTheme === "function") {
        window.AndroidNative.applyNativeTheme(state.theme);
      }
    } catch (_) {}
    if (persist) nativeCall("saveSettings", { theme: state.theme }).catch(() => {});
  }

  function updateHeaderTitle() {
    const pack = I18N[state.lang] || I18N.en;
    const keyMap = {
      home: "screenFiles",
      sync: "screenSync",
      collab: "screenCollab",
      tools: "screenTools",
      ai: "screenAi"
    };
    const titleEl = document.getElementById("topTitle");
    if (titleEl) titleEl.textContent = pack[keyMap[state.activeTab]] || "MyOffice";
    const subEl = document.getElementById("topSub");
    if (subEl) {
      if (state.activeTab === "sync") {
        subEl.textContent = `${state.deviceInfo.name} · ${state.deviceInfo.ip}`;
      } else {
        subEl.textContent = pack.docsCount(state.files.length);
      }
    }
  }

  function applyLang(persist = false) {
    const pack = I18N[state.lang] || I18N.en;
    document.documentElement.lang = state.lang;
    document.documentElement.dir = pack.dir;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const k = el.getAttribute("data-i18n");
      if (pack[k] && typeof pack[k] === "string") el.textContent = pack[k];
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const k = el.getAttribute("data-i18n-placeholder");
      if (pack[k]) el.setAttribute("placeholder", pack[k]);
    });
    const badgeEl = document.getElementById("langBadgeLabel");
    if (badgeEl) badgeEl.textContent = state.lang === "ur" ? "اردو" : state.lang === "hi" ? "हिन्दी" : "EN";
    updateHeaderTitle();
    if (persist) nativeCall("saveSettings", { lang: state.lang }).catch(() => {});
  }

  // ==================== NATIVE-FEEL SWIPEABLE BOTTOM SHEETS ====================
  function showModal(innerHtml) {
    const sheet = document.getElementById("modalSheet");
    sheet.style.transform = "";
    sheet.innerHTML = `<div class="sheet-handle"></div>${innerHtml}`;
    document.getElementById("modalBackdrop").classList.remove("hidden");
  }

  function closeModal() {
    document.getElementById("modalBackdrop").classList.add("hidden");
    if (state.sheetPromptResolver) {
      const fn = state.sheetPromptResolver;
      state.sheetPromptResolver = null;
      fn(null);
    }
  }

  function askInputSheet({ title, label, defaultValue = "", placeholder = "", type = "text", confirmText = "Continue" }) {
    return new Promise((resolve) => {
      state.sheetPromptResolver = resolve;
      showModal(`
        <h3 class="modal-title">${esc(title)}</h3>
        ${label ? `<label class="field-label">${esc(label)}</label>` : ""}
        <input class="input" id="sheetPromptField" type="${esc(type)}" value="${esc(defaultValue)}" placeholder="${esc(placeholder)}"
               onkeydown="if(event.key==='Enter')App._submitPromptSheet()" />
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px;">
          <button class="btn btn-subtle" onclick="App.closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="App._submitPromptSheet()">${esc(confirmText)}</button>
        </div>
      `);
      setTimeout(() => {
        const inp = document.getElementById("sheetPromptField");
        if (inp) { inp.focus(); inp.select(); }
      }, 60);
    });
  }

  function askConfirmSheet({ title, message, confirmText = "Delete", danger = false }) {
    return new Promise((resolve) => {
      state.sheetPromptResolver = (val) => resolve(Boolean(val));
      showModal(`
        <h3 class="modal-title">${esc(title)}</h3>
        <p class="section-desc">${esc(message)}</p>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px;">
          <button class="btn btn-subtle" onclick="App.closeModal()">Cancel</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" onclick="App._confirmSheetYes()">${esc(confirmText)}</button>
        </div>
      `);
    });
  }

  // ==================== WORKSPACE LIST WITH LONG-PRESS GESTURE ====================
  async function refreshWorkspace() {
    const res = await nativeCall("listFiles");
    state.files = res.files || [];
    renderFiles();
    refreshPdfDropdown();
    updateHeaderTitle();
  }

  function refreshPdfDropdown() {
    const sel = document.getElementById("aiPdfSelect");
    if (!sel) return;
    const pdfs = state.files.filter((f) => f.ext === "pdf");
    sel.innerHTML = `<option value="">General Writing & Document Task</option>` +
      pdfs.map((f) => `<option value="${esc(f.rel)}">${esc(f.name)}</option>`).join("");
  }

  function stripKnownExt(name) {
    return String(name || "").replace(/\.(docx|xlsx|pptx|pdf|movault|movaultkey|txt|md|csv|png|jpe?g|webp)$/i, "");
  }

  function renderFiles() {
    const q = (document.getElementById("searchInput")?.value || "").trim().toLowerCase();
    const listEl = document.getElementById("fileList");
    const filtered = state.files.filter((f) => {
      if (state.filter === "starred" && !f.starred) return false;
      if (state.filter !== "all" && state.filter !== "starred" && f.type !== state.filter) return false;
      if (q && !f.name.toLowerCase().includes(q) && !f.rel.toLowerCase().includes(q)) return false;
      return true;
    });

    if (!filtered.length) {
      listEl.innerHTML = `
        <div class="empty-state">
          ${svgIcon("folder", "ico ico-lg")}
          <h4>No documents in this view</h4>
          <p>Tap + New below to create a document, workbook, slide deck, or camera scan.</p>
          <button class="btn btn-primary" onclick="App.openCreateSheet()">${svgIcon("plus", "ico ico-sm")} Create or Import</button>
        </div>
      `;
      return;
    }

    listEl.innerHTML = filtered.map((f) => {
      const spec = formatSpec(f.type, f.ext);
      const displayTitle = stripKnownExt(f.name) || f.name;
      const folderPrefix = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) + " · " : "";
      const metaLine = `${folderPrefix}${(f.ext || "FILE").toUpperCase()} · ${fmtSize(f.size)} · ${fmtDateShort(f.mtime)}`;
      return `
        <div class="file-row" data-rel="${esc(f.rel)}" onclick="App.openFile('${esc(f.rel)}')">
          <div class="file-badge" style="color:${spec.fg};background:${spec.bg}">${esc(spec.label)}</div>
          <div class="file-body">
            <div class="file-title">
              ${f.starred ? `<span class="star-mark">${svgIcon("star", "ico ico-sm")}</span>` : ""}
              <span>${esc(displayTitle)}</span>
            </div>
            <div class="file-sub">${esc(metaLine)}</div>
          </div>
          <button class="row-more-btn" onclick="event.stopPropagation();App.openFileMenu('${esc(f.rel)}')" aria-label="Document options">
            ${svgIcon("more")}
          </button>
        </div>
      `;
    }).join("");

    // Attach natural Android 480ms long-press gesture on file rows
    listEl.querySelectorAll(".file-row").forEach((row) => {
      let pressTimer = null;
      row.addEventListener("touchstart", () => {
        pressTimer = setTimeout(() => {
          pressTimer = null;
          App.openFileMenu(row.dataset.rel);
        }, 480);
      }, { passive: true });
      row.addEventListener("touchmove", () => clearTimeout(pressTimer), { passive: true });
      row.addEventListener("touchend", () => clearTimeout(pressTimer), { passive: true });
    });
  }

  // ==================== DOCUMENT LIFECYCLE & AUTO-SAVE ====================
  async function openFile(rel, extra = {}) {
    try {
      const doc = await nativeCall("openDocument", Object.assign({ path: rel }, extra));
      state.doc = doc;
      if (doc.unlockedFromVault) toast(`Unlocked ${doc.name}`);
      renderActiveEditor();
      await refreshWorkspace();
    } catch (e) {
      if (String(e.message).toLowerCase().includes("password") || String(e.message).toLowerCase().includes("decrypt")) {
        const pw = await askInputSheet({
          title: "Protected Document",
          label: "Enter password or recovery passphrase",
          type: "password",
          confirmText: "Unlock"
        });
        if (pw !== null) return openFile(rel, { password: pw, passphrase: pw });
        return;
      }
      toast(e.message);
    }
  }

  async function flushPendingAutoSave() {
    clearTimeout(state.autoSaveTimer);
    state.autoSaveTimer = null;
    if (!state.doc || !state.doc._dirty) return;
    state.doc._dirty = false;
    try {
      if (state.doc.kind === "writer") await saveWriterNow(true);
      else if (state.doc.kind === "sheet") await saveSheetNow(true);
      else if (state.doc.kind === "slide") await saveSlidesNow(true);
    } catch (_) {}
  }

  function scheduleAutoSave(statusElId) {
    if (!state.doc) return;
    state.doc._dirty = true;
    const st = statusElId && document.getElementById(statusElId);
    if (st) st.textContent = "Saving...";
    clearTimeout(state.autoSaveTimer);
    state.autoSaveTimer = setTimeout(async () => {
      await flushPendingAutoSave();
      const stDone = statusElId && document.getElementById(statusElId);
      if (stDone) stDone.textContent = "All changes saved";
    }, 850);
  }

  async function closeEditor() {
    await flushPendingAutoSave();
    state.doc = null;
    const ov = document.getElementById("editorOverlay");
    ov.classList.remove("immersive-chrome");
    ov.classList.add("hidden");
    ov.innerHTML = "";
    refreshWorkspace();
  }

  function renderActiveEditor() {
    const d = state.doc;
    const ov = document.getElementById("editorOverlay");
    ov.classList.remove("hidden", "immersive-chrome");
    if (d.kind === "writer") renderWriterEditor(ov, d);
    else if (d.kind === "sheet") renderSheetEditor(ov, d);
    else if (d.kind === "slide") renderSlideEditor(ov, d);
    else if (d.kind === "pdf") renderPdfEditor(ov, d);
    else if (d.kind === "photos") renderPhotosEditor(ov, d);
    injectEditorCollabPill(ov);
  }

  function injectEditorCollabPill(ov) {
    if (!state.collabRoom || !ov) return;
    const actions = ov.querySelector(".top-actions");
    if (!actions || document.getElementById("btnEditorCollabChat")) return;
    const btn = document.createElement("button");
    btn.id = "btnEditorCollabChat";
    btn.className = "chip active";
    btn.style.cssText = "height:34px;padding:0 10px;display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;margin-right:4px;";
    btn.onclick = () => App.openLiveCollabChatSheet();
    const cnt = state.collabUnread > 0 ? ` (${state.collabUnread})` : "";
    btn.innerHTML = `${svgIcon("users", "ico ico-sm")}<span id="lblEditorCollabUnread">Chat${cnt}</span>`;
    actions.insertBefore(btn, actions.firstChild);
  }

  // ==================== 1. FLAGSHIP WRITER EDITOR ====================
  // Mobile Reflow / Print A4 Toggle + Pinch-Zoom + Auto-Save + Thumb Bottom Formatting Bar
  function renderWriterEditor(ov, d) {
    d.pageLayout = d.pageLayout || "mobile";
    ov.innerHTML = `
      <div class="topbar">
        <div class="brand-group">
          <button class="icon-btn" onclick="App.closeEditor()" aria-label="Back">${svgIcon("back")}</button>
          <div class="title-stack">
            <div class="top-title">${esc(stripKnownExt(d.name))}</div>
            <div class="top-sub" id="writerSaveState">Saved</div>
          </div>
        </div>
        <div class="top-actions">
          <button class="icon-btn" onclick="document.execCommand('undo')" aria-label="Undo">${svgIcon("undo", "ico ico-sm")}</button>
          <button class="icon-btn" onclick="document.execCommand('redo')" aria-label="Redo">${svgIcon("redo", "ico ico-sm")}</button>
          <button class="icon-btn ${d.pageLayout === "a4" ? "active-tool" : ""}" id="btnWriterLayout" onclick="App.toggleWriterPageLayout()" aria-label="Toggle Mobile / Print View">
            ${svgIcon("book", "ico ico-sm")}
          </button>
          <button class="icon-btn" onclick="App.openWriterMoreSheet()" aria-label="Document actions">${svgIcon("more")}</button>
        </div>
      </div>

      <div class="writer-scroll-host" id="writerViewport">
        <div id="writerSurface" class="writer-surface ${d.pageLayout === "a4" ? "a4-layout" : ""}" contenteditable="true" oninput="App.onWriterInput()">${d.html || "<p></p>"}</div>
      </div>

      <div class="editor-bottom-bar">
        <button class="dock-btn" onclick="document.execCommand('bold');App.onWriterInput()">${svgIcon("bold", "ico ico-sm")}<span>Bold</span></button>
        <button class="dock-btn" onclick="document.execCommand('italic');App.onWriterInput()">${svgIcon("italic", "ico ico-sm")}<span>Italic</span></button>
        <button class="dock-btn" onclick="document.execCommand('underline');App.onWriterInput()">${svgIcon("underline", "ico ico-sm")}<span>Line</span></button>
        <button class="dock-btn" onclick="App.cycleWriterHeading()">${svgIcon("type", "ico ico-sm")}<span>Heading</span></button>
        <button class="dock-btn" onclick="document.execCommand('insertUnorderedList');App.onWriterInput()">${svgIcon("list", "ico ico-sm")}<span>Bullets</span></button>
        <button class="dock-btn" onclick="App.toggleWriterRtl()">${svgIcon("align", "ico ico-sm")}<span>RTL</span></button>
        <button class="dock-btn" onclick="App.writerInsertTable()">${svgIcon("sheet", "ico ico-sm")}<span>Table</span></button>
      </div>
    `;
    window.MyOfficeGestures.attachZoomSurface(
      document.getElementById("writerViewport"),
      document.getElementById("writerSurface"),
      { mode: "cssZoom", minScale: 0.75, maxScale: 2.6, doubleTapScale: 1.5 }
    );
  }

  function collectWriterBlocks() {
    const surf = document.getElementById("writerSurface");
    if (!surf) return [{ type: "p", text: "" }];
    const blocks = [];
    Array.from(surf.children).forEach((node) => {
      const tag = node.tagName.toUpperCase();
      if (tag === "TABLE") {
        const rows = Array.from(node.querySelectorAll("tr")).map((tr) =>
          Array.from(tr.querySelectorAll("td,th")).map((td) => td.innerText.trim())
        );
        blocks.push({ type: "table", rows });
      } else {
        const heading = tag === "H1" ? 1 : tag === "H2" ? 2 : tag === "H3" ? 3 : 0;
        const rtl = node.dir === "rtl" || /[\u0600-\u06FF]/.test(node.innerText || "");
        blocks.push({
          type: "p",
          heading,
          rtl,
          align: rtl ? "right" : "left",
          text: node.innerText || ""
        });
      }
    });
    if (!blocks.length) blocks.push({ type: "p", text: surf.innerText || "" });
    return blocks;
  }

  async function saveWriterNow(silent = false) {
    const surf = document.getElementById("writerSurface");
    if (!surf || !state.doc) return;
    const blocks = collectWriterBlocks();
    await nativeCall("saveDocument", {
      kind: "writer",
      rel: state.doc.rel,
      blocks,
      plainText: surf.innerText || "",
      html: surf.innerHTML
    });
    if (state.collabRoom) {
      await nativeCall("pushCollabSnapshot", { room: state.collabRoom, path: state.doc.rel, kind: "writer" });
    }
    if (!silent) toast("Saved " + state.doc.name);
  }

  // ==================== 2. FLAGSHIP SPREADSHEETS EDITOR ====================
  // Sticky Column & Row Headers + Freeze Row 1 + 2-Finger Pinch-Zoom + Live SUM/AVG Strip + fx Bar
  function computeSheetStats(cells) {
    const nums = [];
    Object.values(cells || {}).forEach((c) => {
      const v = parseFloat(String(c?.value ?? "").replace(/,/g, ""));
      if (!Number.isNaN(v) && Number.isFinite(v)) nums.push(v);
    });
    if (!nums.length) return "Tap any cell to edit · Pinch with 2 fingers to zoom";
    const sum = nums.reduce((a, b) => a + b, 0);
    const avg = sum / nums.length;
    return `SUM: ${sum.toLocaleString(undefined, { maximumFractionDigits: 2 })} · AVG: ${avg.toLocaleString(undefined, { maximumFractionDigits: 2 })} · COUNT: ${nums.length}`;
  }

  function renderSheetEditor(ov, d) {
    d.activeSheetIdx = d.activeSheetIdx || 0;
    d.selectedRef = d.selectedRef || "A1";
    d.freezeTopRow = d.freezeTopRow !== false;
    const sheet = d.sheets[d.activeSheetIdx] || { name: "Sheet1", cells: {} };
    window.MyOfficeFormula.evaluateSheet(sheet.cells);

    const rows = 32, cols = 10;
    let thead = "<tr><th></th>";
    for (let c = 0; c < cols; c++) thead += `<th>${window.MyOfficeFormula.idxToCol(c)}</th>`;
    thead += "</tr>";

    let tbody = "";
    for (let r = 0; r < rows; r++) {
      const trCls = (r === 0 && d.freezeTopRow) ? "frozen-row" : "";
      tbody += `<tr class="${trCls}"><td class="row-hdr">${r + 1}</td>`;
      for (let c = 0; c < cols; c++) {
        const ref = window.MyOfficeFormula.idxToCol(c) + (r + 1);
        const cell = sheet.cells[ref];
        const cls = ref === d.selectedRef ? "selected-cell" : "";
        const isNum = cell && cell.value !== "" && !Number.isNaN(Number(String(cell.value).replace(/,/g, "")));
        const style = [
          cell && cell.bold ? "font-weight:700" : "",
          isNum ? "text-align:right" : ""
        ].filter(Boolean).join(";");
        tbody += `<td class="${cls}" style="${style}" data-ref="${ref}" onclick="App.selectCell('${ref}')">${esc(cell ? cell.value : "")}</td>`;
      }
      tbody += "</tr>";
    }

    const curRaw = sheet.cells[d.selectedRef]?.raw ?? "";
    const tabsHtml = d.sheets.map((s, idx) =>
      `<button class="btn btn-compact ${idx === d.activeSheetIdx ? "btn-primary" : "btn-subtle"}" onclick="App.switchSheetTab(${idx})">${esc(s.name)}</button>`
    ).join("");

    ov.innerHTML = `
      <div class="topbar">
        <div class="brand-group">
          <button class="icon-btn" onclick="App.closeEditor()" aria-label="Back">${svgIcon("back")}</button>
          <div class="title-stack">
            <div class="top-title">${esc(stripKnownExt(d.name))}</div>
            <div class="top-sub" id="sheetSaveState">All changes saved</div>
          </div>
        </div>
        <div class="top-actions">
          <button class="icon-btn ${d.freezeTopRow ? "active-tool" : ""}" onclick="App.toggleFreezeHeaderRow()" aria-label="Freeze top row">
            ${svgIcon("layers", "ico ico-sm")}
          </button>
          <button class="icon-btn" onclick="App.openSheetChartModal()" aria-label="Chart">${svgIcon("chart", "ico ico-sm")}</button>
          <button class="icon-btn" onclick="App.shareDoc('${esc(d.rel)}')" aria-label="Share">${svgIcon("share", "ico ico-sm")}</button>
        </div>
      </div>

      <div class="formula-bar">
        <span class="cell-ref-badge" id="cellRefBadge">${d.selectedRef}</span>
        <button class="btn btn-compact btn-subtle" style="padding:0 9px;font-style:italic;font-weight:800;" onclick="App.openFormulaHelperSheet()">fx</button>
        <input class="input" id="formulaInput" value="${esc(curRaw)}" placeholder="Value or =SUM(B2:B10)" onkeydown="if(event.key==='Enter')App.commitCellEdit()" />
        <button class="icon-btn" style="width:36px;height:36px;" onclick="App.commitCellEdit()" aria-label="Confirm">${svgIcon("check", "ico ico-sm")}</button>
      </div>

      <div class="sheet-calc-strip" id="sheetCalcStrip">${esc(computeSheetStats(sheet.cells))}</div>

      <div class="sheet-wrap" id="sheetViewport">
        <table class="sheet-table" id="sheetGridTable"><thead>${thead}</thead><tbody>${tbody}</tbody></table>
      </div>

      <div class="sheet-tabs-strip">
        ${tabsHtml}
        <button class="btn btn-compact btn-subtle" onclick="App.addSheetTab()">${svgIcon("plus", "ico ico-sm")}</button>
        <div style="flex:1"></div>
        <button class="btn btn-compact btn-subtle" onclick="App.toggleSelectedCellBold()">${svgIcon("bold", "ico ico-sm")}</button>
      </div>
    `;

    window.MyOfficeGestures.attachZoomSurface(
      document.getElementById("sheetViewport"),
      document.getElementById("sheetGridTable"),
      { mode: "cssZoom", minScale: 0.65, maxScale: 2.2, doubleTapScale: 1.35 }
    );
  }

  function selectCell(ref) {
    state.doc.selectedRef = ref;
    const sheet = state.doc.sheets[state.doc.activeSheetIdx];
    document.getElementById("cellRefBadge").textContent = ref;
    document.getElementById("formulaInput").value = sheet.cells[ref]?.raw ?? "";
    document.querySelectorAll(".sheet-table td.selected-cell").forEach((td) => td.classList.remove("selected-cell"));
    const target = document.querySelector(`.sheet-table td[data-ref="${ref}"]`);
    if (target) target.classList.add("selected-cell");
  }

  async function commitCellEdit() {
    const val = document.getElementById("formulaInput").value;
    const sheet = state.doc.sheets[state.doc.activeSheetIdx];
    const ref = state.doc.selectedRef;
    if (val === "") delete sheet.cells[ref];
    else sheet.cells[ref] = Object.assign(sheet.cells[ref] || {}, { raw: val, value: val });
    window.MyOfficeFormula.evaluateSheet(sheet.cells);
    if (state.collabRoom) {
      nativeCall("pushCollabSheetCell", { room: state.collabRoom, sheet: sheet.name, cell: ref, value: val }).catch(() => {});
    }
    renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
    scheduleAutoSave("sheetSaveState");
  }

  async function saveSheetNow(silent = false) {
    if (!state.doc) return;
    await nativeCall("saveDocument", { kind: "sheet", rel: state.doc.rel, sheets: state.doc.sheets });
    if (state.collabRoom) {
      await nativeCall("pushCollabSnapshot", { room: state.collabRoom, path: state.doc.rel, kind: "sheet" });
    }
    if (!silent) toast("Saved " + state.doc.name);
  }

  // ==================== 3. FLAGSHIP PRESENTATION EDITOR ====================
  // Direct On-Slide WYSIWYG Canvas + Pinch-Zoom + Swipe + Miniature Thumbnail Filmstrip + Laser Presenter
  function renderSlideEditor(ov, d) {
    d.activeSlide = Math.min(d.activeSlide || 0, d.slides.length - 1);
    d.slideTheme = d.slideTheme || "light";
    const s = d.slides[d.activeSlide];

    const thumbsHtml = d.slides.map((sl, idx) => `
      <button class="slide-thumb ${idx === d.activeSlide ? "active" : ""}" onclick="App.jumpSlide(${idx})">
        <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc(sl.title || "Untitled")}</div>
        <div style="font-weight:400;color:#78756F;font-size:7.5px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${esc((sl.bullets && sl.bullets[0]) || "")}</div>
        <span class="slide-thumb-num">${idx + 1}</span>
      </button>
    `).join("");

    ov.innerHTML = `
      <div class="topbar">
        <div class="brand-group">
          <button class="icon-btn" onclick="App.closeEditor()" aria-label="Back">${svgIcon("back")}</button>
          <div class="title-stack">
            <div class="top-title">${esc(stripKnownExt(d.name))}</div>
            <div class="top-sub" id="slideSaveState">Slide ${d.activeSlide + 1} of ${d.slides.length}</div>
          </div>
        </div>
        <div class="top-actions">
          <button class="icon-btn" onclick="App.startSlideshow()" aria-label="Present Slideshow">${svgIcon("play", "ico ico-sm")}</button>
          <button class="icon-btn" onclick="App.shareDoc('${esc(d.rel)}')" aria-label="Share">${svgIcon("share", "ico ico-sm")}</button>
        </div>
      </div>

      <div class="slide-stage-viewport" id="slideStageViewport">
        <div class="slide-canvas-16x9" id="slideCanvasCard" data-slide-theme="${esc(d.slideTheme)}">
          <div class="slide-canvas-title" id="slideTitleEditable" contenteditable="true" oninput="App.syncSlideCanvasEdits()">${esc(s.title)}</div>
          <ul class="slide-canvas-bullets" id="slideBulletsEditable" contenteditable="true" oninput="App.syncSlideCanvasEdits()">
            ${(s.bullets && s.bullets.length ? s.bullets : ["Tap directly here to edit bullet points"]).map((b) => `<li>${esc(b)}</li>`).join("")}
          </ul>
        </div>
      </div>

      <div class="slide-filmstrip">
        ${thumbsHtml}
        <button class="btn btn-compact btn-primary" style="height:50px;min-width:46px;" onclick="App.addNewSlide()" aria-label="Add slide">${svgIcon("plus")}</button>
      </div>

      <div class="editor-bottom-bar">
        <button class="dock-btn" onclick="App.addSlideBullet()">${svgIcon("list", "ico ico-sm")}<span>Point</span></button>
        <button class="dock-btn" onclick="App.cycleSlideTheme()">${svgIcon("moon", "ico ico-sm")}<span>Theme</span></button>
        <button class="dock-btn" onclick="App.openSpeakerNotesSheet()">${svgIcon("doc", "ico ico-sm")}<span>Notes</span></button>
        <button class="dock-btn" onclick="App.duplicateSlide()">${svgIcon("copy", "ico ico-sm")}<span>Duplicate</span></button>
        <button class="dock-btn" onclick="App.startSlideshow()">${svgIcon("play", "ico ico-sm")}<span>Present</span></button>
      </div>
    `;

    const vp = document.getElementById("slideStageViewport");
    window.MyOfficeGestures.attachZoomSurface(
      vp,
      document.getElementById("slideCanvasCard"),
      { mode: "cssZoom", minScale: 0.8, maxScale: 2.8, doubleTapScale: 1.6 }
    );
    window.MyOfficeGestures.attachHorizontalSwipe(vp, () => App.stepSlide(-1), () => App.stepSlide(1));
  }

  function syncSlideCanvasEdits() {
    const s = state.doc.slides[state.doc.activeSlide];
    const titleEl = document.getElementById("slideTitleEditable");
    const listEl = document.getElementById("slideBulletsEditable");
    if (titleEl) s.title = titleEl.innerText.trim();
    if (listEl) {
      const items = Array.from(listEl.querySelectorAll("li")).map((li) => li.innerText.trim()).filter(Boolean);
      s.bullets = items.length ? items : [listEl.innerText.trim()].filter(Boolean);
    }
    scheduleAutoSave("slideSaveState");
  }

  async function saveSlidesNow(silent = false) {
    if (!state.doc) return;
    await nativeCall("saveDocument", { kind: "slide", rel: state.doc.rel, slides: state.doc.slides });
    if (state.collabRoom) {
      await nativeCall("pushCollabSnapshot", { room: state.collabRoom, path: state.doc.rel, kind: "slide" });
    }
    if (!silent) toast("Saved " + state.doc.name);
  }

  function startSlideshow() {
    state.slideShowIdx = state.doc.activeSlide || 0;
    renderSlideshowFrame();
  }

  function renderSlideshowFrame() {
    const box = document.getElementById("slideshowOverlay");
    box.classList.remove("hidden");
    const s = state.doc.slides[state.slideShowIdx];
    box.innerHTML = `
      <div style="max-width:820px;width:100%;">
        <h1 style="font-size:28px;font-weight:800;margin-bottom:18px;letter-spacing:-0.02em;">${esc(s.title)}</h1>
        <ul style="font-size:19px;line-height:1.75;color:#E5E5E0;">
          ${(s.bullets || []).map((b) => `<li>${esc(b)}</li>`).join("")}
        </ul>
      </div>
      <div id="laserPointerDot" class="laser-dot hidden"></div>
      <div style="position:fixed;bottom:22px;left:20px;right:20px;display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:13px;color:#A8A29E;font-variant-numeric:tabular-nums;">Swipe slide · Hold finger for laser (${state.slideShowIdx + 1} / ${state.doc.slides.length})</span>
        <button class="btn btn-compact btn-subtle" onclick="document.getElementById('slideshowOverlay').classList.add('hidden')">Exit</button>
      </div>
    `;
    const dot = document.getElementById("laserPointerDot");
    box.ontouchmove = (e) => {
      if (e.touches.length === 1) {
        dot.style.left = e.touches[0].clientX + "px";
        dot.style.top = e.touches[0].clientY + "px";
        dot.classList.remove("hidden");
      }
    };
    box.ontouchend = () => dot.classList.add("hidden");
    window.MyOfficeGestures.attachHorizontalSwipe(box, () => App.moveSlideshow(-1), () => App.moveSlideshow(1));
  }

  // ==================== 4. FLAGSHIP PDF VIEWER & STUDIO ====================
  // Continuous Vertical Multi-Page Stream + 2-Finger Pinch-to-Zoom + Double-Tap Smart Zoom +
  // Crisp High-DPI Progressive Re-Rasterization + Mobile Reflow + Day/Sepia/Night + ZERO Zoom Buttons!
  function renderPdfEditor(ov, d) {
    d.curPage = d.curPage || 0;
    d.zoom = 1.0;
    d.reflow = Boolean(d.reflow);
    d.readingMode = d.readingMode || "day";
    d._pageCache = d._pageCache || { 0: d.firstPageImage || "" };

    let contentHtml = "";
    if (d.reflow) {
      const blocks = (d.pagesText || []).map((txt, idx) => `
        <div class="pdf-reflow-divider">Page ${idx + 1} of ${d.pageCount}</div>
        <div>${esc(txt || "(Scanned image page — switch to Page View for full graphic layout)").replace(/\n/g, "<br/>")}</div>
      `).join("");
      contentHtml = `<div class="pdf-reflow-reader" id="pdfZoomTarget">${blocks}</div>`;
    } else {
      const cards = [];
      for (let i = 0; i < d.pageCount; i++) {
        const src = d._pageCache[i] || "";
        cards.push(`
          <div class="pdf-page-card" data-page-idx="${i}">
            <img id="pdfImg_${i}" src="${src}" alt="Page ${i + 1}" />
          </div>
        `);
      }
      contentHtml = `<div class="pdf-pages-column" id="pdfZoomTarget">${cards.join("")}</div>`;
    }

    ov.innerHTML = `
      <div class="topbar">
        <div class="brand-group">
          <button class="icon-btn" onclick="App.closeEditor()" aria-label="Back">${svgIcon("back")}</button>
          <div class="title-stack">
            <div class="top-title">${esc(stripKnownExt(d.name))}</div>
            <div class="top-sub">${d.reflow ? "Mobile Reflow View" : "Pinch or double-tap to zoom"}</div>
          </div>
        </div>
        <div class="top-actions">
          <button class="icon-btn" onclick="AndroidNative.printCurrentWebDocument('${esc(d.name)}')" aria-label="Print">${svgIcon("print", "ico ico-sm")}</button>
          <button class="icon-btn" onclick="App.shareDoc('${esc(d.rel)}')" aria-label="Share">${svgIcon("share", "ico ico-sm")}</button>
        </div>
      </div>

      <div class="pdf-viewport" id="pdfViewport" data-reading-mode="${esc(d.readingMode)}">
        ${contentHtml}
      </div>

      <button class="pdf-page-pill" id="pdfPagePill" onclick="App.openPdfJumpSheet()">
        ${d.curPage + 1} / ${d.pageCount}
      </button>

      <div class="editor-bottom-bar">
        <button class="dock-btn ${d.reflow ? "active" : ""}" onclick="App.togglePdfReflow()">
          ${svgIcon("book", "ico ico-sm")}<span>Reflow</span>
        </button>
        <button class="dock-btn ${d.readingMode !== "day" ? "active" : ""}" onclick="App.cyclePdfReadingMode()">
          ${svgIcon("moon", "ico ico-sm")}<span>${d.readingMode === "day" ? "Comfort" : d.readingMode.toUpperCase()}</span>
        </button>
        <button class="dock-btn" onclick="App.readPdfPageAloud()">
          ${svgIcon("volume", "ico ico-sm")}<span>Listen</span>
        </button>
        <button class="dock-btn" onclick="App.openPdfAnnotateToolsSheet()">
          ${svgIcon("pen", "ico ico-sm")}<span>Tools</span>
        </button>
        <button class="dock-btn" onclick="App.chatWithCurrentPdf()">
          ${svgIcon("search", "ico ico-sm")}<span>Ask PDF</span>
        </button>
      </div>
    `;

    const vp = document.getElementById("pdfViewport");
    const target = document.getElementById("pdfZoomTarget");

    // Attach 60fps 2-finger focal pinch-to-zoom + double-tap smart zoom + single-tap immersive mode
    d._zoomApi = window.MyOfficeGestures.attachZoomSurface(vp, target, {
      mode: d.reflow ? "cssZoom" : "width",
      minScale: 1.0,
      maxScale: 4.5,
      doubleTapScale: 2.25,
      onSingleTap: () => ov.classList.toggle("immersive-chrome"),
      onScaleSettled: (settledScale) => {
        d.zoom = settledScale;
        if (!d.reflow) rasterizeVisiblePdfPages(settledScale);
      }
    });

    if (!d.reflow) {
      vp.addEventListener("scroll", () => {
        updateActivePdfPageIndicator(vp);
        clearTimeout(vp._scrollRenderT);
        vp._scrollRenderT = setTimeout(() => rasterizeVisiblePdfPages(d.zoom || 1.0), 120);
      }, { passive: true });
      rasterizeVisiblePdfPages(1.0);
    }
  }

  function updateActivePdfPageIndicator(vp) {
    const d = state.doc;
    if (!d || d.kind !== "pdf") return;
    const midY = vp.scrollTop + vp.clientHeight * 0.35;
    const cards = vp.querySelectorAll(".pdf-page-card");
    cards.forEach((card) => {
      if (card.offsetTop <= midY) d.curPage = Number(card.dataset.pageIdx || 0);
    });
    const pill = document.getElementById("pdfPagePill");
    if (pill) pill.textContent = `${d.curPage + 1} / ${d.pageCount}`;
  }

  async function rasterizeVisiblePdfPages(zoomFactor) {
    const d = state.doc;
    const vp = document.getElementById("pdfViewport");
    if (!d || d.kind !== "pdf" || !vp) return;
    const desiredDpiScale = Math.min(3.0, Math.max(1.65, zoomFactor * 1.55));
    const cards = Array.from(vp.querySelectorAll(".pdf-page-card"));
    const topEdge = vp.scrollTop - vp.clientHeight;
    const botEdge = vp.scrollTop + vp.clientHeight * 2;

    for (const card of cards) {
      if (!state.doc || state.doc !== d) return;
      const idx = Number(card.dataset.pageIdx);
      const inWindow = card.offsetTop + card.offsetHeight >= topEdge && card.offsetTop <= botEdge;
      if (!inWindow && idx > 2) continue;
      const renderedScale = d._renderedScale?.[idx] || (idx === 0 && d.firstPageImage ? 1.75 : 0);
      if (renderedScale >= desiredDpiScale - 0.25 && document.getElementById("pdfImg_" + idx)?.getAttribute("src")) {
        continue;
      }
      try {
        const res = await nativeCall("renderPdfPage", { path: d.rel, pageIndex: idx, scale: desiredDpiScale });
        d._renderedScale = d._renderedScale || {};
        d._renderedScale[idx] = desiredDpiScale;
        d._pageCache[idx] = res.dataUri;
        const imgEl = document.getElementById("pdfImg_" + idx);
        if (imgEl) imgEl.src = res.dataUri;
      } catch (_) {}
    }
  }

  // ==================== 5. FLAGSHIP PHOTOS & SCANNER STUDIO ====================
  // Full 2-Finger Pinch-to-Zoom + Double-Tap Zoom + Clean Bottom Tool Dock
  function renderPhotosEditor(ov, d) {
    state.photoFilter = { bright: 100, contrast: 100, sat: 100, gray: 0, rotate: 0 };
    ov.innerHTML = `
      <div class="topbar">
        <div class="brand-group">
          <button class="icon-btn" onclick="App.closeEditor()" aria-label="Back">${svgIcon("back")}</button>
          <div class="title-stack">
            <div class="top-title">${esc(stripKnownExt(d.name))}</div>
            <div class="top-sub">Pinch or double-tap to inspect detail</div>
          </div>
        </div>
        <div class="top-actions">
          <button class="btn btn-compact btn-subtle" onclick="App.convertPhotoToPdf()">To PDF</button>
          <button class="btn btn-compact btn-primary" onclick="App.saveEditedPhoto()">Save</button>
        </div>
      </div>

      <div class="pdf-viewport" id="photoViewport" style="display:flex;align-items:center;justify-content:center;">
        <div id="photoZoomTarget" style="width:calc(100% - 8px);text-align:center;">
          <canvas id="photoCanvas" style="width:100%;height:auto;border-radius:8px;box-shadow:var(--elevation-page);"></canvas>
        </div>
      </div>

      <div class="editor-bottom-bar">
        <button class="dock-btn" onclick="App.applyPhotoPreset(100,100,100,0)">${svgIcon("image", "ico ico-sm")}<span>Original</span></button>
        <button class="dock-btn" onclick="App.applyPhotoPreset(116,158,0,100)">${svgIcon("doc", "ico ico-sm")}<span>Scan B&W</span></button>
        <button class="dock-btn" onclick="App.applyPhotoPreset(106,126,125,0)">${svgIcon("star", "ico ico-sm")}<span>Vivid</span></button>
        <button class="dock-btn" onclick="App.rotatePhoto90()">${svgIcon("rotate", "ico ico-sm")}<span>Rotate</span></button>
        <button class="dock-btn" onclick="App.openPhotoSlidersSheet()">${svgIcon("sliders", "ico ico-sm")}<span>Adjust</span></button>
      </div>
    `;
    const img = new Image();
    img.onload = () => {
      state.doc._imgObj = img;
      redrawPhotoCanvas();
    };
    img.src = d.dataUri;

    window.MyOfficeGestures.attachZoomSurface(
      document.getElementById("photoViewport"),
      document.getElementById("photoZoomTarget"),
      { mode: "width", minScale: 1.0, maxScale: 4.5, doubleTapScale: 2.5 }
    );
  }

  function redrawPhotoCanvas() {
    const img = state.doc?._imgObj;
    const cv = document.getElementById("photoCanvas");
    if (!img || !cv) return;
    const f = state.photoFilter;
    const swapped = (f.rotate % 180) !== 0;
    cv.width = swapped ? img.height : img.width;
    cv.height = swapped ? img.width : img.height;
    const ctx = cv.getContext("2d");
    ctx.save();
    ctx.filter = `brightness(${f.bright}%) contrast(${f.contrast}%) saturate(${f.sat}%) grayscale(${f.gray}%)`;
    ctx.translate(cv.width / 2, cv.height / 2);
    ctx.rotate((f.rotate * Math.PI) / 180);
    ctx.drawImage(img, -img.width / 2, -img.height / 2);
    ctx.restore();
  }

  // ==================== NATIVE CALLBACKS ====================
  window.__onNativeFileImported = async function (raw) {
    const info = JSON.parse(raw);
    await refreshWorkspace();
    toast("Imported " + info.rel);
    if (info.autoOpen) openFile(info.rel);
  };

  window.__onNativeQrScanned = async function (raw) {
    const info = JSON.parse(raw);
    if (!info.ok || !info.text) {
      toast("No QR code detected — frame closer and retry");
      return;
    }
    handleIncomingUriOrQr(info.text);
  };

  window.__onNativeDeepLink = function (raw) {
    const info = JSON.parse(raw);
    if (info.uri) handleIncomingUriOrQr(info.uri);
  };

  function normalizeCollabMsg(rawMsg) {
    if (!rawMsg) return null;
    let m = rawMsg;
    if (typeof m === "string") {
      try { m = JSON.parse(m); } catch (_) { return null; }
    }
    if (!m || typeof m !== "object" || !m.text) return null;
    if (!m.id) m.id = `${m.sender || "peer"}:${m.text}:${m.ts || 0}`;
    return m;
  }

  function upsertCollabMessage(rawMsg) {
    const m = normalizeCollabMsg(rawMsg);
    if (!m) return false;
    if (state.collabMessages.some((x) => x.id === m.id)) return false;
    state.collabMessages.push(m);
    return true;
  }

  function updateEditorCollabBadge() {
    const lbl = document.getElementById("lblEditorCollabUnread");
    if (lbl) {
      lbl.textContent = state.collabUnread > 0 ? `Chat (${state.collabUnread})` : "Chat";
    }
  }

  function resolveSharedRelPath() {
    const m = state.collabMeta;
    if (!m) return "";
    if (m.relPath) return m.relPath;
    const fn = m.filename || m.fileName || "";
    return fn ? `Collaborations/${fn}` : "";
  }

  function renderSharedFileCard() {
    const box = document.getElementById("collabSharedFileBox");
    if (!box) return;
    const rel = resolveSharedRelPath();
    if (!state.collabRoom || !rel) {
      box.innerHTML = "";
      return;
    }
    const fname = state.collabMeta?.filename || state.collabMeta?.fileName || rel.split("/").pop();
    box.innerHTML = `
      <div class="action-list" style="border:1px solid var(--accent);background:var(--surface);">
        <div class="action-row" style="cursor:pointer;" onclick="App.openJoinedRoomFile()">
          ${svgIcon(extToIcon(fname.split(".").pop() || "docx"))}
          <div class="action-row-body">
            <div class="action-row-title">${esc(fname)}</div>
            <div class="action-row-sub">Live Shared File · Tap to view & co-edit</div>
          </div>
          <span class="chip active" style="flex-shrink:0;">Open</span>
        </div>
      </div>
    `;
  }

  async function handleIncomingUriOrQr(text) {
    const t = text.trim();
    if (t.startsWith("myoffice://pair") || t.includes("/api/v1") || /:4188\d|:8766/.test(t)) {
      App.switchTab("sync");
      document.getElementById("pcUrlInput").value = t;
      toast("Connecting to PC...");
      await App.pairAndSyncPcNow();
    } else if (
      t.startsWith("ws://") ||
      t.startsWith("wss://") ||
      t.startsWith("myoffice://join") ||
      t.includes("/f/") ||
      /:8765\b/.test(t) ||
      /^https?:\/\/\d+\.\d+\.\d+\.\d+:\d+/.test(t)
    ) {
      App.switchTab("collab");
      document.getElementById("collabLinkInput").value = t;
      await App.joinCollabRoomNow();
    } else {
      toast("Scanned: " + t);
    }
  }

  window.__onNativeCollabEvent = async function (raw) {
    const wrapper = typeof raw === "string" ? JSON.parse(raw) : (raw || {});
    const evt = wrapper.event || {};
    const t = evt.type || evt.event || "";
    if (t === "chat_push" && evt.message) {
      if (upsertCollabMessage(evt.message)) {
        renderCollabChat();
        const modalOpen = Boolean(document.getElementById("sheetCollabChatLog"));
        const editorOpen = state.doc && !document.getElementById("editorOverlay").classList.contains("hidden");
        if (editorOpen && !modalOpen) {
          state.collabUnread += 1;
          updateEditorCollabBadge();
          const m = normalizeCollabMsg(evt.message);
          if (m) toast(`${m.sender || "PC"}: ${m.text}`);
        }
      }
    } else if (t === "state") {
      if (evt.relPath && state.collabMeta) {
        state.collabMeta.relPath = evt.relPath;
        state.collabMeta.filename = evt.filename || state.collabMeta.filename;
        renderSharedFileCard();
      }
      if (Array.isArray(evt.chat)) {
        let added = 0;
        evt.chat.forEach((rawM) => {
          if (upsertCollabMessage(rawM)) added += 1;
        });
        if (added > 0 || state.collabMessages.length > 0) renderCollabChat();
      }
      if (typeof evt.writer_text === "string" && evt.writer_text && state.doc && state.doc.kind === "writer") {
        const surf = document.getElementById("writerSurface");
        if (surf && document.activeElement !== surf) surf.innerText = evt.writer_text;
      }
      if (evt.sheets && typeof evt.sheets === "object" && state.doc && state.doc.kind === "sheet") {
        let changedCells = false;
        (state.doc.sheets || []).forEach((sh) => {
          const remoteMap = evt.sheets[sh.name];
          if (remoteMap && typeof remoteMap === "object") {
            Object.entries(remoteMap).forEach(([cellRef, val]) => {
              if (!sh.cells[cellRef] || sh.cells[cellRef].raw !== String(val)) {
                sh.cells[cellRef] = { raw: String(val), value: String(val) };
                changedCells = true;
              }
            });
          }
        });
        if (changedCells) renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
      }
    } else if (t === "connected") {
      const b = document.getElementById("collabPeersBadge");
      if (b) {
        b.textContent = "Live";
        b.classList.add("live");
      }
    } else if (t === "file_push_notify") {
      if (evt.relPath && state.collabMeta) {
        state.collabMeta.relPath = evt.relPath;
        state.collabMeta.filename = evt.filename || evt.meta?.filename || state.collabMeta.filename;
      }
      await refreshWorkspace();
      renderSharedFileCard();
      const targetRel = evt.relPath || resolveSharedRelPath();
      if (targetRel && (!state.doc || state.doc.rel === targetRel)) {
        await openFile(targetRel);
      }
      toast(`Updated shared file (${evt.meta?.filename || evt.filename || "live"})`);
    } else if (t === "writer_push" && state.doc && state.doc.kind === "writer") {
      const surf = document.getElementById("writerSurface");
      if (surf && document.activeElement !== surf) surf.innerText = evt.writer_text || "";
    } else if (t === "sheet_push" && state.doc && state.doc.kind === "sheet") {
      const sheet = state.doc.sheets.find((s) => s.name === evt.sheet) || state.doc.sheets[0];
      if (sheet) {
        sheet.cells[evt.cell] = { raw: evt.value, value: evt.value };
        renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
        injectEditorCollabPill(document.getElementById("editorOverlay"));
      }
    }
  };

  window.__onNativeAutoSync = async function (raw) {
    const rep = JSON.parse(raw);
    await refreshWorkspace();
    toast("Synced: " + rep.summary);
  };

  window.__handleAndroidBack = function () {
    const ss = document.getElementById("slideshowOverlay");
    if (!ss.classList.contains("hidden")) { ss.classList.add("hidden"); return true; }
    const modal = document.getElementById("modalBackdrop");
    if (!modal.classList.contains("hidden")) { closeModal(); return true; }
    if (state.doc) { closeEditor(); return true; }
    if (state.activeTab !== "home") { App.switchTab("home"); return true; }
    return false;
  };

  function renderChatHtmlList() {
    if (!state.collabMessages.length) {
      return `<div style="font-size:13px;color:var(--ink-muted);">Connected. Messages appear instantly on both PC and phone.</div>`;
    }
    return state.collabMessages.map((m) => `
      <div class="chat-msg">
        <div class="chat-sender" style="color:${esc(m.color || "var(--accent)")};">${esc(m.sender || "Participant")}</div>
        <div>${esc(m.text || "")}</div>
      </div>
    `).join("");
  }

  function renderCollabChat() {
    const html = renderChatHtmlList();
    const box = document.getElementById("collabChatLog");
    if (box) {
      box.innerHTML = html;
      box.scrollTop = box.scrollHeight;
    }
    const modalBox = document.getElementById("sheetCollabChatLog");
    if (modalBox) {
      modalBox.innerHTML = html;
      modalBox.scrollTop = modalBox.scrollHeight;
    }
  }

  // ==================== PREFERENCES & CREATE SHEETS ====================
  function openPreferencesSheet() {
    const pack = I18N[state.lang] || I18N.en;
    showModal(`
      <h3 class="modal-title">${esc(pack.prefsTitle)}</h3>
      <label class="field-label">${esc(pack.prefLang)}</label>
      <div class="segmented" style="margin-bottom:14px;">
        <button class="seg-btn ${state.lang === "en" ? "active" : ""}" onclick="App.selectLanguage('en')">English</button>
        <button class="seg-btn ${state.lang === "ur" ? "active" : ""}" onclick="App.selectLanguage('ur')">اردو</button>
        <button class="seg-btn ${state.lang === "hi" ? "active" : ""}" onclick="App.selectLanguage('hi')">हिन्दी</button>
      </div>

      <label class="field-label">${esc(pack.prefTheme)}</label>
      <div class="segmented" style="margin-bottom:14px;">
        <button class="seg-btn ${state.theme === "light" ? "active" : ""}" onclick="App.selectTheme('light')">${esc(pack.themeLight)}</button>
        <button class="seg-btn ${state.theme === "dark" ? "active" : ""}" onclick="App.selectTheme('dark')">${esc(pack.themeDark)}</button>
      </div>

      <div class="action-list" style="margin-bottom:6px;border:1px solid var(--line);">
        <button class="action-row" onclick="App.openOtaUpdateSheet()">
          ${svgIcon("sync", "ico")}
          <div class="action-row-body">
            <div class="action-row-title">GitHub Auto-Update (OTA) · v${esc(state.version)}</div>
            <div class="action-row-sub">${esc(state.config.updateRepoUrl || "Tap to connect your GitHub update repo")}</div>
          </div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.triggerQuickSync()">
          ${svgIcon("sync", "ico")}
          <div class="action-row-body">
            <div class="action-row-title">Sync Documents Now</div>
            <div class="action-row-sub">${esc(state.deviceInfo.name)} · ${esc(state.deviceInfo.ip)}</div>
          </div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.runSelfDiagnostics()">
          ${svgIcon("check", "ico")}
          <div class="action-row-body">
            <div class="action-row-title">System Health Self-Test</div>
            <div class="action-row-sub">Vault Key ID: ${esc(state.deviceInfo.keyId)}</div>
          </div>
        </button>
      </div>
    `);
  }

  function openCreateSheet() {
    showModal(`
      <h3 class="modal-title">Create or Capture</h3>
      <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
        <button class="action-row" onclick="App.closeModal();App.createNewDoc('writer')">
          <svg class="ico" style="color:var(--fmt-writer)"><use href="#i-doc"/></svg>
          <div class="action-row-body"><div class="action-row-title">Document (.docx)</div><div class="action-row-sub">Rich text, Mobile View / A4 & auto-save</div></div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.createNewDoc('sheet')">
          <svg class="ico" style="color:var(--fmt-sheet)"><use href="#i-sheet"/></svg>
          <div class="action-row-body"><div class="action-row-title">Spreadsheet (.xlsx)</div><div class="action-row-sub">Frozen headers, live formulas & charts</div></div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.createNewDoc('slide')">
          <svg class="ico" style="color:var(--fmt-slide)"><use href="#i-slide"/></svg>
          <div class="action-row-body"><div class="action-row-title">Presentation (.pptx)</div><div class="action-row-sub">Slide canvas, filmstrip & laser presenter</div></div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.createNewDoc('pdf')">
          <svg class="ico" style="color:var(--fmt-pdf)"><use href="#i-pdf"/></svg>
          <div class="action-row-body"><div class="action-row-title">Blank PDF (.pdf)</div><div class="action-row-sub">Clean printable page document</div></div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.openCameraScan()">
          <svg class="ico" style="color:var(--fmt-pdf)"><use href="#i-scan"/></svg>
          <div class="action-row-body"><div class="action-row-title">Scan with Camera</div><div class="action-row-sub">Capture page & convert cleanly to PDF</div></div>
        </button>
        <button class="action-row" onclick="App.closeModal();App.openFileImport()">
          <svg class="ico" style="color:var(--fmt-photos)"><use href="#i-folder"/></svg>
          <div class="action-row-body"><div class="action-row-title">Import File / USB OTG</div><div class="action-row-sub">Open DOCX, XLSX, PPTX, PDF, Image or Vault</div></div>
        </button>
        <button class="action-row" onclick="App.openTemplateGallery()">
          <svg class="ico" style="color:var(--accent)"><use href="#i-layers"/></svg>
          <div class="action-row-body"><div class="action-row-title">Start from Template</div><div class="action-row-sub">Invoice, CV, Urdu Application, Pitch Deck</div></div>
        </button>
      </div>
    `);
  }

  // ==================== PUBLIC APP API ====================
  window.App = {
    _submitPromptSheet() {
      const val = document.getElementById("sheetPromptField")?.value ?? "";
      const fn = state.sheetPromptResolver;
      state.sheetPromptResolver = null;
      document.getElementById("modalBackdrop").classList.add("hidden");
      if (fn) fn(val);
    },
    _confirmSheetYes() {
      const fn = state.sheetPromptResolver;
      state.sheetPromptResolver = null;
      document.getElementById("modalBackdrop").classList.add("hidden");
      if (fn) fn(true);
    },

    switchTab(tab) {
      state.activeTab = tab;
      ["home", "sync", "collab", "tools", "ai"].forEach((t) => {
        document.getElementById("tab" + t.charAt(0).toUpperCase() + t.slice(1)).classList.toggle("hidden", t !== tab);
      });
      document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.tab === tab));
      document.getElementById("fabNew").classList.toggle("hidden", tab !== "home");
      updateHeaderTitle();
    },

    setSyncMode(mode, btnEl) {
      document.querySelectorAll("#syncModeSeg .seg-btn").forEach((b) => b.classList.remove("active"));
      btnEl.classList.add("active");
      ["pc", "hotspot", "gmail", "router"].forEach((m) => {
        document.getElementById("syncPane" + m.charAt(0).toUpperCase() + m.slice(1)).classList.toggle("hidden", m !== mode);
      });
    },

    setToolsCat(cat, btnEl) {
      document.querySelectorAll("#toolsCatSeg .seg-btn").forEach((b) => b.classList.remove("active"));
      btnEl.classList.add("active");
      document.getElementById("toolsPanePdf").classList.toggle("hidden", cat !== "pdf");
      document.getElementById("toolsPaneVault").classList.toggle("hidden", cat !== "vault");
      document.getElementById("toolsPaneUtil").classList.toggle("hidden", cat !== "util");
    },

    selectLanguage(lang) {
      state.lang = lang;
      applyLang(true);
      openPreferencesSheet();
    },

    selectTheme(theme) {
      applyTheme(theme, true);
      openPreferencesSheet();
    },

    onSearchInput() {
      const val = document.getElementById("searchInput").value;
      document.getElementById("btnSearchClear").classList.toggle("hidden", !val);
      renderFiles();
    },

    clearSearch() {
      document.getElementById("searchInput").value = "";
      document.getElementById("btnSearchClear").classList.add("hidden");
      renderFiles();
    },

    setFilter(f, chipEl) {
      state.filter = f;
      document.querySelectorAll("#filterRow .filter-tab").forEach((c) => c.classList.remove("active"));
      chipEl.classList.add("active");
      renderFiles();
    },

    async triggerQuickSync() {
      const btn = document.getElementById("btnQuickSync");
      btn.classList.add("spinning");
      try {
        const r = await nativeCall("runSyncNow", {});
        await refreshWorkspace();
        toast("Synced: " + r.report.summary);
      } catch (_) {
        App.switchTab("sync");
        toast("Scan PC QR or configure Gmail once to enable 1-tap sync");
      } finally {
        btn.classList.remove("spinning");
      }
    },

    openCreateSheet,
    openPreferencesSheet,
    renderFiles,
    openFile,
    closeEditor,
    closeModal,
    openFileImport: () => window.AndroidNative && window.AndroidNative.requestImportFile(),
    openCameraScan: () => window.AndroidNative && window.AndroidNative.requestCameraScan(),
    scanQrCamera: () => window.AndroidNative && window.AndroidNative.requestScanQrCamera(),
    scanQrGallery: () => window.AndroidNative && window.AndroidNative.requestScanQrGallery(),

    async createNewDoc(kind) {
      const stamp = new Date().toTimeString().slice(0, 5).replace(":", "");
      const defaultName = {
        writer: `Document_${stamp}.docx`,
        sheet: `Workbook_${stamp}.xlsx`,
        slide: `Presentation_${stamp}.pptx`,
        pdf: `Notes_${stamp}.pdf`
      }[kind];
      const name = await askInputSheet({
        title: "New " + (kind === "writer" ? "Document" : kind === "sheet" ? "Spreadsheet" : kind === "slide" ? "Presentation" : "PDF"),
        label: "File name",
        defaultValue: defaultName,
        confirmText: "Create"
      });
      if (!name || !name.trim()) return;
      const trimmed = name.trim();
      if (kind === "pdf") {
        const out = trimmed.endsWith(".pdf") ? trimmed : trimmed + ".pdf";
        await nativeCall("exportToPdf", { title: out.replace(/\.pdf$/i, ""), lines: [""], outName: out });
        await refreshWorkspace();
        openFile(out);
      } else if (kind === "writer") {
        const out = trimmed.endsWith(".docx") ? trimmed : trimmed + ".docx";
        await nativeCall("saveDocument", { kind: "writer", rel: out, blocks: [{ type: "p", heading: 1, text: out.replace(/\.docx$/i, "") }, { type: "p", text: "" }] });
        await refreshWorkspace();
        openFile(out);
      } else if (kind === "sheet") {
        const out = trimmed.endsWith(".xlsx") ? trimmed : trimmed + ".xlsx";
        await nativeCall("saveDocument", { kind: "sheet", rel: out, sheets: [{ name: "Sheet1", cells: {} }] });
        await refreshWorkspace();
        openFile(out);
      } else if (kind === "slide") {
        const out = trimmed.endsWith(".pptx") ? trimmed : trimmed + ".pptx";
        await nativeCall("saveDocument", { kind: "slide", rel: out, slides: [{ title: out.replace(/\.pptx$/i, ""), bullets: ["Key point"], notes: "" }] });
        await refreshWorkspace();
        openFile(out);
      }
    },

    openTemplateGallery() {
      showModal(`
        <h3 class="modal-title">Built-in Templates</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          <button class="action-row" onclick="App.spawnTemplate('invoice')">
            <svg class="ico" style="color:var(--fmt-sheet)"><use href="#i-sheet"/></svg>
            <div class="action-row-body"><div class="action-row-title">Commercial Tax Invoice</div><div class="action-row-sub">Spreadsheet with live automatic totals (.xlsx)</div></div>
          </button>
          <button class="action-row" onclick="App.spawnTemplate('resume')">
            <svg class="ico" style="color:var(--fmt-writer)"><use href="#i-doc"/></svg>
            <div class="action-row-body"><div class="action-row-title">Executive CV / Resume</div><div class="action-row-sub">Clean structured resume layout (.docx)</div></div>
          </button>
          <button class="action-row" onclick="App.spawnTemplate('urdu_letter')">
            <svg class="ico" style="color:var(--fmt-writer)"><use href="#i-doc"/></svg>
            <div class="action-row-body"><div class="action-row-title">Official Urdu Application</div><div class="action-row-sub">Right-to-left Urdu formal request (.docx)</div></div>
          </button>
          <button class="action-row" onclick="App.spawnTemplate('pitch')">
            <svg class="ico" style="color:var(--fmt-slide)"><use href="#i-slide"/></svg>
            <div class="action-row-body"><div class="action-row-title">Project & Pitch Presentation</div><div class="action-row-sub">Structured executive deck (.pptx)</div></div>
          </button>
        </div>
      `);
    },

    async spawnTemplate(id) {
      closeModal();
      if (id === "invoice") {
        const rel = "Invoice_" + (Date.now() % 10000) + ".xlsx";
        await nativeCall("saveDocument", {
          kind: "sheet", rel,
          sheets: [{
            name: "Invoice",
            cells: {
              A1: { raw: "Description", value: "Description", bold: true },
              B1: { raw: "Qty", value: "Qty", bold: true },
              C1: { raw: "Unit Price", value: "Unit Price", bold: true },
              D1: { raw: "Total", value: "Total", bold: true },
              A2: { raw: "Consulting Service", value: "Consulting Service" }, B2: { raw: "3", value: "3" }, C2: { raw: "15000", value: "15000" }, D2: { raw: "=B2*C2", value: "45000" },
              A3: { raw: "Deployment Package", value: "Deployment Package" }, B3: { raw: "1", value: "1" }, C3: { raw: "20000", value: "20000" }, D3: { raw: "=B3*C3", value: "20000" },
              C4: { raw: "Grand Total", value: "Grand Total", bold: true }, D4: { raw: "=SUM(D2:D3)", value: "65000", bold: true }
            }
          }]
        });
        openFile(rel);
      } else if (id === "urdu_letter") {
        const rel = "Urdu_Application_" + (Date.now() % 10000) + ".docx";
        await nativeCall("saveDocument", {
          kind: "writer", rel,
          blocks: [
            { type: "p", heading: 1, rtl: true, align: "right", text: "بخدمت جناب مینیجر صاحب،" },
            { type: "p", rtl: true, align: "right", text: "مودبانہ گزارش ہے کہ مجھے ضروری کام کے سلسلے میں دو یوم کی رخصت عنایت فرمائی جائے۔ عین نوازش ہوگی۔" }
          ]
        });
        openFile(rel);
      } else if (id === "resume") {
        const rel = "Executive_Resume_" + (Date.now() % 10000) + ".docx";
        await nativeCall("saveDocument", {
          kind: "writer", rel,
          blocks: [
            { type: "p", heading: 1, text: "YOUR FULL NAME" },
            { type: "p", text: "Senior Engineer · Lahore / Karachi · email@example.com" },
            { type: "p", heading: 2, text: "Experience & Achievements" },
            { type: "p", text: "Delivered cross-platform desktop and mobile software systems." }
          ]
        });
        openFile(rel);
      } else {
        const rel = "Presentation_" + (Date.now() % 10000) + ".pptx";
        await nativeCall("saveDocument", {
          kind: "slide", rel,
          slides: [
            { title: "Executive Overview", bullets: ["Key objectives and milestones", "Current operational metrics"] },
            { title: "Next Steps", bullets: ["Rollout schedule", "Resource allocation"] }
          ]
        });
        openFile(rel);
      }
    },

    openFileMenu(rel) {
      showModal(`
        <h3 class="modal-title">${esc(rel.split("/").pop())}</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          <button class="action-row" onclick="App.closeModal();App.openFile('${esc(rel)}')">
            ${svgIcon("folder")}<div class="action-row-body"><div class="action-row-title">Open</div></div>
          </button>
          <button class="action-row" onclick="App.starFile('${esc(rel)}')">
            ${svgIcon("star")}<div class="action-row-body"><div class="action-row-title">Toggle Starred</div></div>
          </button>
          <button class="action-row" onclick="App.shareDoc('${esc(rel)}')">
            ${svgIcon("share")}<div class="action-row-body"><div class="action-row-title">Share or Send Copy</div></div>
          </button>
          <button class="action-row" onclick="App.lockSingleFile('${esc(rel)}')">
            ${svgIcon("lock")}<div class="action-row-body"><div class="action-row-title">Seal in Hardware Vault (.movault)</div></div>
          </button>
          <button class="action-row" onclick="App.renameSingleFile('${esc(rel)}')">
            ${svgIcon("pen")}<div class="action-row-body"><div class="action-row-title">Rename</div></div>
          </button>
          <button class="action-row action-row-danger" onclick="App.deleteSingleFile('${esc(rel)}')">
            ${svgIcon("trash")}<div class="action-row-body"><div class="action-row-title">Delete</div></div>
          </button>
        </div>
      `);
    },

    async starFile(rel) { closeModal(); await nativeCall("toggleStar", { rel }); refreshWorkspace(); },
    async shareDoc(rel) { closeModal(); await nativeCall("shareFile", { path: rel }); },
    async lockSingleFile(rel) {
      closeModal();
      const pass = await askInputSheet({
        title: "Seal in Hardware Vault",
        label: "Optional recovery passphrase (leave blank for paired PC/device key)",
        type: "password",
        confirmText: "Encrypt"
      });
      if (pass === null) return;
      const r = await nativeCall("encryptToVault", { path: rel, passphrase: pass });
      await refreshWorkspace();
      toast("Sealed " + r.rel);
    },
    async renameSingleFile(rel) {
      closeModal();
      const nn = await askInputSheet({ title: "Rename File", label: "New name", defaultValue: rel.split("/").pop(), confirmText: "Rename" });
      if (!nn || !nn.trim()) return;
      await nativeCall("renameFile", { path: rel, newName: nn.trim() });
      refreshWorkspace();
    },
    async deleteSingleFile(rel) {
      closeModal();
      const ok = await askConfirmSheet({
        title: "Delete File",
        message: `Move "${rel.split("/").pop()}" permanently out of your workspace?`,
        confirmText: "Delete",
        danger: true
      });
      if (!ok) return;
      await nativeCall("deleteFile", { path: rel });
      refreshWorkspace();
    },

    // Writer Actions
    onWriterInput() {
      scheduleAutoSave("writerSaveState");
      if (state.collabRoom) {
        const txt = document.getElementById("writerSurface")?.innerText || "";
        nativeCall("pushCollabWriter", { room: state.collabRoom, text: txt }).catch(() => {});
      }
    },
    saveWriterNow,
    cycleWriterHeading() {
      state.doc._headingStep = ((state.doc._headingStep || 0) + 1) % 3;
      const tag = ["P", "H1", "H2"][state.doc._headingStep];
      document.execCommand("formatBlock", false, tag);
      App.onWriterInput();
    },
    toggleWriterPageLayout() {
      state.doc.pageLayout = state.doc.pageLayout === "a4" ? "mobile" : "a4";
      const surf = document.getElementById("writerSurface");
      const btn = document.getElementById("btnWriterLayout");
      surf.classList.toggle("a4-layout", state.doc.pageLayout === "a4");
      btn.classList.toggle("active-tool", state.doc.pageLayout === "a4");
      toast(state.doc.pageLayout === "a4" ? "Print A4 Layout" : "Mobile Reflow View");
    },
    openWriterMoreSheet() {
      const txt = document.getElementById("writerSurface")?.innerText || "";
      const words = txt.trim() ? txt.trim().split(/\s+/).length : 0;
      showModal(`
        <h3 class="modal-title">${esc(stripKnownExt(state.doc.name))}</h3>
        <div style="font-size:12.5px;color:var(--ink-muted);margin-bottom:12px;">${words} words · ${txt.length} characters</div>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          <button class="action-row" onclick="App.closeModal();App.saveWriterNow()">
            ${svgIcon("check")}<div class="action-row-body"><div class="action-row-title">Save Copy Now</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.writerExportPdf()">
            ${svgIcon("pdf")}<div class="action-row-body"><div class="action-row-title">Export as PDF</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.writerReadAloud()">
            ${svgIcon("volume")}<div class="action-row-body"><div class="action-row-title">Read Aloud (Voice)</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();AndroidNative.printCurrentWebDocument('${esc(state.doc.name)}')">
            ${svgIcon("print")}<div class="action-row-body"><div class="action-row-title">Print Document</div></div>
          </button>
          <button class="action-row" onclick="App.shareDoc('${esc(state.doc.rel)}')">
            ${svgIcon("share")}<div class="action-row-body"><div class="action-row-title">Share File</div></div>
          </button>
        </div>
      `);
    },
    toggleWriterRtl() {
      const surf = document.getElementById("writerSurface");
      surf.dir = surf.dir === "rtl" ? "ltr" : "rtl";
      App.onWriterInput();
    },
    writerInsertTable() {
      document.execCommand("insertHTML", false, "<table border='1' style='border-collapse:collapse;width:100%;margin:8px 0;'><tr><td>Header 1</td><td>Header 2</td></tr><tr><td>Item</td><td>Value</td></tr></table>");
      App.onWriterInput();
    },
    writerReadAloud() {
      const txt = document.getElementById("writerSurface")?.innerText || "";
      nativeCall("speakText", { text: txt, lang: /[\u0600-\u06FF]/.test(txt) ? "ur" : "en" });
      toast("Reading document aloud...");
    },
    async writerExportPdf() {
      const lines = (document.getElementById("writerSurface")?.innerText || "").split("\n");
      const out = state.doc.name.replace(/\.[^.]+$/, "") + ".pdf";
      await nativeCall("exportToPdf", { title: state.doc.name, lines, outName: out });
      await refreshWorkspace();
      toast("Exported " + out);
    },

    // Spreadsheet Actions
    selectCell,
    commitCellEdit,
    saveSheetNow,
    toggleFreezeHeaderRow() {
      state.doc.freezeTopRow = !state.doc.freezeTopRow;
      renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
      toast(state.doc.freezeTopRow ? "Frozen Row 1 header" : "Unfroze Row 1");
    },
    toggleSelectedCellBold() {
      const sheet = state.doc.sheets[state.doc.activeSheetIdx];
      const ref = state.doc.selectedRef || "A1";
      const cur = sheet.cells[ref] || { raw: "", value: "" };
      cur.bold = !cur.bold;
      sheet.cells[ref] = cur;
      renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
      scheduleAutoSave("sheetSaveState");
    },
    openFormulaHelperSheet() {
      const fns = [
        ["=SUM(B2:B10)", "Add a range of numbers"],
        ["=AVERAGE(B2:B10)", "Arithmetic mean of values"],
        ["=IF(B2>100, \"Pass\", \"Review\")", "Conditional logical check"],
        ["=MAX(B2:B10)", "Largest number in range"],
        ["=MIN(B2:B10)", "Smallest number in range"],
        ["=COUNT(B2:B10)", "Count numeric entries"]
      ];
      showModal(`
        <h3 class="modal-title">Insert Formula</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          ${fns.map(([fn, desc]) => `
            <button class="action-row" onclick="App.insertFormulaTemplate('${esc(fn)}')">
              <div class="action-row-body">
                <div class="action-row-title" style="font-family:monospace;">${esc(fn)}</div>
                <div class="action-row-sub">${esc(desc)}</div>
              </div>
            </button>
          `).join("")}
        </div>
      `);
    },
    insertFormulaTemplate(expr) {
      closeModal();
      const inp = document.getElementById("formulaInput");
      if (inp) { inp.value = expr; inp.focus(); }
    },
    switchSheetTab(idx) { state.doc.activeSheetIdx = idx; renderSheetEditor(document.getElementById("editorOverlay"), state.doc); },
    addSheetTab() {
      state.doc.sheets.push({ name: "Sheet" + (state.doc.sheets.length + 1), cells: {} });
      state.doc.activeSheetIdx = state.doc.sheets.length - 1;
      renderSheetEditor(document.getElementById("editorOverlay"), state.doc);
      scheduleAutoSave("sheetSaveState");
    },
    openSheetChartModal() {
      const sheet = state.doc.sheets[state.doc.activeSheetIdx];
      const labels = [], values = [];
      for (let r = 1; r <= 12; r++) {
        const a = sheet.cells["A" + r]?.value, b = sheet.cells["B" + r]?.value;
        if (b !== undefined && !Number.isNaN(parseFloat(b))) {
          labels.push(a || "R" + r);
          values.push(parseFloat(b));
        }
      }
      const svg = window.MyOfficeFormula.buildChartSvg(labels.length ? labels : ["Q1", "Q2", "Q3"], values.length ? values : [30, 55, 80], "bar");
      showModal(`<h3 class="modal-title">Sheet Chart (Columns A & B)</h3><div>${svg}</div>`);
    },

    // Presentation Actions
    syncSlideCanvasEdits,
    saveSlidesNow,
    startSlideshow,
    jumpSlide(idx) {
      state.doc.activeSlide = Math.max(0, Math.min(state.doc.slides.length - 1, idx));
      renderSlideEditor(document.getElementById("editorOverlay"), state.doc);
    },
    stepSlide(delta) {
      App.jumpSlide((state.doc.activeSlide || 0) + delta);
    },
    addSlideBullet() {
      const s = state.doc.slides[state.doc.activeSlide];
      s.bullets = s.bullets || [];
      s.bullets.push("New key point");
      renderSlideEditor(document.getElementById("editorOverlay"), state.doc);
      scheduleAutoSave("slideSaveState");
    },
    cycleSlideTheme() {
      const order = ["light", "dark", "cobalt"];
      state.doc.slideTheme = order[(order.indexOf(state.doc.slideTheme || "light") + 1) % order.length];
      renderSlideEditor(document.getElementById("editorOverlay"), state.doc);
    },
    async openSpeakerNotesSheet() {
      const s = state.doc.slides[state.doc.activeSlide];
      const notes = await askInputSheet({
        title: `Speaker Notes — Slide ${state.doc.activeSlide + 1}`,
        label: "Presenter notes (visible only to you)",
        defaultValue: s.notes || "",
        confirmText: "Save Notes"
      });
      if (notes === null) return;
      s.notes = notes;
      scheduleAutoSave("slideSaveState");
    },
    addNewSlide() {
      state.doc.slides.push({ title: "New Slide", bullets: ["Key point"], notes: "" });
      state.doc.activeSlide = state.doc.slides.length - 1;
      renderSlideEditor(document.getElementById("editorOverlay"), state.doc);
      scheduleAutoSave("slideSaveState");
    },
    duplicateSlide() {
      const copy = JSON.parse(JSON.stringify(state.doc.slides[state.doc.activeSlide]));
      state.doc.slides.splice(state.doc.activeSlide + 1, 0, copy);
      state.doc.activeSlide++;
      renderSlideEditor(document.getElementById("editorOverlay"), state.doc);
      scheduleAutoSave("slideSaveState");
    },
    moveSlideshow(delta) {
      state.slideShowIdx = Math.max(0, Math.min(state.doc.slides.length - 1, state.slideShowIdx + delta));
      renderSlideshowFrame();
    },

    // PDF Gesture-First Actions (Zero Zoom Buttons)
    togglePdfReflow() {
      state.doc.reflow = !state.doc.reflow;
      renderPdfEditor(document.getElementById("editorOverlay"), state.doc);
      toast(state.doc.reflow ? "Mobile Reflow View" : "Original Page Layout");
    },
    cyclePdfReadingMode() {
      const modes = ["day", "sepia", "night"];
      state.doc.readingMode = modes[(modes.indexOf(state.doc.readingMode || "day") + 1) % modes.length];
      renderPdfEditor(document.getElementById("editorOverlay"), state.doc);
    },
    async openPdfJumpSheet() {
      const num = await askInputSheet({
        title: `Jump to Page (1 – ${state.doc.pageCount})`,
        label: "Page number",
        defaultValue: String((state.doc.curPage || 0) + 1),
        type: "number",
        confirmText: "Go"
      });
      const idx = parseInt(num, 10) - 1;
      if (Number.isNaN(idx) || idx < 0 || idx >= state.doc.pageCount) return;
      state.doc.curPage = idx;
      const card = document.querySelector(`.pdf-page-card[data-page-idx="${idx}"]`);
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
      document.getElementById("pdfPagePill").textContent = `${idx + 1} / ${state.doc.pageCount}`;
    },
    openPdfAnnotateToolsSheet() {
      showModal(`
        <h3 class="modal-title">PDF Annotation & Tools</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          <button class="action-row" onclick="App.closeModal();App.quickPdfStamp()">
            ${svgIcon("check")}<div class="action-row-body"><div class="action-row-title">Apply Approval Stamp</div><div class="action-row-sub">APPROVED, PAID, or custom status banner</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.quickPdfWatermark()">
            ${svgIcon("pen")}<div class="action-row-body"><div class="action-row-title">Add Diagonal Watermark</div><div class="action-row-sub">CONFIDENTIAL or custom text</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.quickPdfPageNumbers()">
            ${svgIcon("doc")}<div class="action-row-body"><div class="action-row-title">Add Footer Page Numbers</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.quickPdfProtect()">
            ${svgIcon("lock")}<div class="action-row-body"><div class="action-row-title">Encrypt with Password (AES-256)</div></div>
          </button>
          <button class="action-row" onclick="App.closeModal();App.execSelectedPdfTool('extract_text','${esc(state.doc.rel)}')">
            ${svgIcon("doc")}<div class="action-row-body"><div class="action-row-title">Extract Full Text (.txt)</div></div>
          </button>
        </div>
      `);
    },
    readPdfPageAloud() {
      const txt = (state.doc.pagesText || [])[state.doc.curPage] || "";
      nativeCall("speakText", { text: txt || "No selectable text on this page." });
      toast(`Reading page ${state.doc.curPage + 1} aloud...`);
    },
    chatWithCurrentPdf() {
      const rel = state.doc.rel;
      closeEditor();
      App.switchTab("ai");
      document.getElementById("aiPdfSelect").value = rel;
      toast("Selected " + rel + " for page Q&A");
    },
    async quickPdfStamp() {
      const label = await askInputSheet({ title: "Approval Stamp", label: "Stamp text", defaultValue: "APPROVED", confirmText: "Apply" });
      if (!label) return;
      const r = await nativeCall("runPdfTool", { tool: "stamp", path: state.doc.rel, label });
      openFile(r.rel);
    },
    async quickPdfWatermark() {
      const text = await askInputSheet({ title: "Diagonal Watermark", label: "Watermark text", defaultValue: "CONFIDENTIAL", confirmText: "Apply" });
      if (!text) return;
      const r = await nativeCall("runPdfTool", { tool: "watermark", path: state.doc.rel, text });
      openFile(r.rel);
    },
    async quickPdfProtect() {
      const pw = await askInputSheet({ title: "Protect PDF (AES-256)", label: "Set password", type: "password", confirmText: "Encrypt" });
      if (!pw) return;
      const r = await nativeCall("runPdfTool", { tool: "protect", path: state.doc.rel, password: pw });
      await refreshWorkspace();
      toast("Saved encrypted PDF: " + r.rel);
    },
    async quickPdfPageNumbers() {
      const r = await nativeCall("runPdfTool", { tool: "page_numbers", path: state.doc.rel });
      openFile(r.rel);
    },

    // Photos & Scanner Actions
    applyPhotoPreset(b, c, s, g) {
      Object.assign(state.photoFilter, { bright: b, contrast: c, sat: s, gray: g });
      redrawPhotoCanvas();
    },
    setPhotoSlider(k, v) { state.photoFilter[k] = Number(v); redrawPhotoCanvas(); },
    rotatePhoto90() { state.photoFilter.rotate = (state.photoFilter.rotate + 90) % 360; redrawPhotoCanvas(); },
    openPhotoSlidersSheet() {
      const f = state.photoFilter;
      showModal(`
        <h3 class="modal-title">Image Adjustments</h3>
        <label class="field-label">Brightness</label>
        <input type="range" min="50" max="160" value="${f.bright}" style="width:100%" oninput="App.setPhotoSlider('bright', this.value)" />
        <label class="field-label">Contrast</label>
        <input type="range" min="50" max="180" value="${f.contrast}" style="width:100%" oninput="App.setPhotoSlider('contrast', this.value)" />
        <label class="field-label">Saturation</label>
        <input type="range" min="0" max="200" value="${f.sat}" style="width:100%" oninput="App.setPhotoSlider('sat', this.value)" />
      `);
    },
    async saveEditedPhoto() {
      const cv = document.getElementById("photoCanvas");
      await nativeCall("saveDocument", { kind: "photos", rel: state.doc.rel, dataUri: cv.toDataURL("image/png") });
      toast("Saved " + state.doc.name);
    },
    async convertPhotoToPdf() {
      const r = await nativeCall("runPdfTool", { tool: "images_to_pdf", paths: [state.doc.rel] });
      openFile(r.rel);
    },

    // Sync Hub
    async discoverLanPcs() {
      toast("Searching Wi-Fi for PCs...");
      const res = await nativeCall("discoverLanPeers");
      const peers = res.peers || [];
      const box = document.getElementById("discoveredPeersBox");
      if (!peers.length) {
        box.innerHTML = `<div style="font-size:13px;color:var(--ink-muted);margin:8px 0;">No PC broadcast found — press Ctrl+Alt+Y on PC or enter address below.</div>`;
        return;
      }
      box.innerHTML = `<div class="action-list" style="border:1px solid var(--line);margin:8px 0;">` + peers.map((p) => `
        <button class="action-row" onclick="document.getElementById('pcUrlInput').value='${esc(p.url)}'">
          ${svgIcon("wifi")}
          <div class="action-row-body"><div class="action-row-title">${esc(p.device)} (${esc(p.platform)})</div><div class="action-row-sub">${esc(p.url)}</div></div>
        </button>
      `).join("") + `</div>`;
      document.getElementById("pcUrlInput").value = peers[0].url;
    },

    async pairAndSyncPcNow() {
      const url = document.getElementById("pcUrlInput").value.trim();
      const pin = document.getElementById("pcPinInput").value.trim();
      if (!url) { toast("Scan PC QR code or enter PC URL first"); return; }
      try {
        toast("Syncing with PC...");
        const res = await nativeCall("pairAndSyncPc", { url, pin });
        document.getElementById("syncReportBody").textContent = `${res.pcDevice}: ${res.report.summary}`;
        renderPcLiveRooms(res.collabRooms || []);
        await refreshWorkspace();
        toast("Sync complete: " + res.report.summary);
      } catch (e) {
        toast("Sync error: " + e.message);
      }
    },

    async togglePhoneHost(enable) {
      const res = await nativeCall("togglePhoneSyncHost", { enable });
      const panel = document.getElementById("phoneHostPanel");
      if (!res.running) {
        panel.classList.add("hidden");
        toast("Phone sync server stopped");
        return;
      }
      panel.classList.remove("hidden");
      document.getElementById("phoneHostQr").innerHTML = res.qrSvg;
      document.getElementById("phoneHostMeta").innerHTML = `Address: <strong>${esc(res.url)}</strong><br/>PIN: <strong>${esc(res.pin)}</strong>`;
      toast("Phone sync server active");
    },

    async syncGmailNow() {
      const gmailAddress = document.getElementById("gmailAddressInput").value.trim();
      const gmailAppPassword = document.getElementById("gmailPassInput").value.trim();
      const vaultEncryptSync = document.getElementById("chkVaultSync").checked;
      toast("Syncing via Gmail...");
      try {
        const res = await nativeCall("runSyncNow", { syncKind: "gmail", gmailAddress, gmailAppPassword, vaultEncryptSync });
        document.getElementById("syncReportBody").textContent = `Gmail: ${res.report.summary}`;
        await refreshWorkspace();
        toast("Gmail sync complete: " + res.report.summary);
      } catch (e) {
        toast("Gmail error: " + e.message);
      }
    },

    async syncRouterFtpNow() {
      const ftpUrl = document.getElementById("ftpUrlInput").value.trim();
      const ftpUser = document.getElementById("ftpUserInput").value.trim() || "anonymous";
      const ftpPassword = document.getElementById("ftpPassInput").value;
      const vaultEncryptSync = document.getElementById("chkVaultSync").checked;
      try {
        const res = await nativeCall("runSyncNow", { syncKind: "ftp", ftpUrl, ftpUser, ftpPassword, vaultEncryptSync });
        document.getElementById("syncReportBody").textContent = `Router: ${res.report.summary}`;
        await refreshWorkspace();
        toast("Router sync complete: " + res.report.summary);
      } catch (e) {
        toast("Router error: " + e.message);
      }
    },

    saveAutoSyncPref() {
      nativeCall("saveSettings", { autoSync: document.getElementById("chkAutoSync").checked });
    },

    // Collaboration
    async joinCollabRoomNow() {
      const link = document.getElementById("collabLinkInput").value.trim();
      const userName = document.getElementById("collabUserName").value.trim() || "Mobile User";
      if (!link) { toast("Enter room link or scan QR first"); return; }
      try {
        state.collabMessages = [];
        state.collabUnread = 0;
        const res = await nativeCall("joinCollabRoom", { link, userName });
        const sess = res.session || {};
        state.collabRoom = sess.room;
        state.collabMeta = sess;
        if (Array.isArray(sess.chat)) {
          sess.chat.forEach((m) => upsertCollabMessage(m));
        }
        document.getElementById("collabRoomHeader").textContent = `Room: ${sess.room}`;
        const badge = document.getElementById("collabPeersBadge");
        badge.textContent = "Live";
        badge.classList.add("live");
        renderCollabChat();
        renderSharedFileCard();
        await refreshWorkspace();
        const rel = resolveSharedRelPath();
        if (rel) {
          toast(`Connected & opening ${sess.filename || rel}...`);
          await openFile(rel);
          if (typeof sess.writerText === "string" && sess.writerText && state.doc && state.doc.kind === "writer") {
            const surf = document.getElementById("writerSurface");
            if (surf && !surf.innerText.trim()) surf.innerText = sess.writerText;
          }
        } else {
          toast("Connected to room " + sess.room);
        }
      } catch (e) {
        toast("Connection failed: " + e.message);
      }
    },

    async sendChatNow() {
      if (!state.collabRoom) { toast("Connect to a live room first"); return; }
      const inp = document.getElementById("collabChatInput");
      const text = inp.value.trim();
      if (!text) return;
      inp.value = "";
      await nativeCall("sendCollabChat", { room: state.collabRoom, text });
    },

    openLiveCollabChatSheet() {
      if (!state.collabRoom) return;
      state.collabUnread = 0;
      updateEditorCollabBadge();
      const fname = state.collabMeta?.filename || state.collabRoom;
      showModal(`
        <h3 class="modal-title">Live Team Chat · ${esc(fname)}</h3>
        <div class="chat-log" id="sheetCollabChatLog" style="max-height:240px;margin-bottom:10px;">
          ${renderChatHtmlList()}
        </div>
        <div style="display:flex;gap:8px;">
          <input class="input" id="sheetCollabChatInput" placeholder="Message PC & team..." onkeydown="if(event.key==='Enter')App.sendSheetChatNow()" />
          <button class="btn btn-primary" onclick="App.sendSheetChatNow()">Send</button>
        </div>
      `);
      const modalBox = document.getElementById("sheetCollabChatLog");
      if (modalBox) modalBox.scrollTop = modalBox.scrollHeight;
      setTimeout(() => document.getElementById("sheetCollabChatInput")?.focus(), 80);
    },

    async sendSheetChatNow() {
      if (!state.collabRoom) return;
      const inp = document.getElementById("sheetCollabChatInput");
      if (!inp) return;
      const text = inp.value.trim();
      if (!text) return;
      inp.value = "";
      await nativeCall("sendCollabChat", { room: state.collabRoom, text });
    },

    async openJoinedRoomFile() {
      const rel = resolveSharedRelPath();
      if (!rel) {
        toast("Waiting for host file snapshot...");
        return;
      }
      await refreshWorkspace();
      await openFile(rel);
    },

    async leaveCurrentRoom() {
      if (!state.collabRoom) return;
      await nativeCall("leaveCollabRoom", { room: state.collabRoom });
      state.collabRoom = null;
      state.collabMeta = null;
      state.collabUnread = 0;
      renderSharedFileCard();
      const badge = document.getElementById("collabPeersBadge");
      badge.textContent = "Offline";
      badge.classList.remove("live");
      toast("Disconnected from room");
    },

    // PDF Toolbox, Vault & Utilities
    launchPdfToolModal(tool) {
      const pdfs = state.files.filter((f) => f.ext === "pdf");
      const imgs = state.files.filter((f) => f.type === "photos");
      if (tool === "images_to_pdf") {
        if (!imgs.length) { toast("Import or scan an image first"); return; }
        nativeCall("runPdfTool", { tool: "images_to_pdf", paths: imgs.map((i) => i.rel) }).then((r) => {
          refreshWorkspace(); openFile(r.rel);
        });
        return;
      }
      if (!pdfs.length) { toast("Create or import a PDF document first"); return; }
      if (tool === "merge") {
        if (pdfs.length < 2) { toast("Add at least two PDF files to merge"); return; }
        nativeCall("runPdfTool", { tool: "merge", paths: pdfs.map((p) => p.rel) }).then((r) => {
          refreshWorkspace(); openFile(r.rel);
        });
        return;
      }
      showModal(`
        <h3 class="modal-title">Select PDF Document</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          ${pdfs.map((p) => `<button class="action-row" onclick="App.execSelectedPdfTool('${esc(tool)}','${esc(p.rel)}')">${svgIcon("pdf")}<div class="action-row-body"><div class="action-row-title">${esc(p.name)}</div></div></button>`).join("")}
        </div>
      `);
    },

    async execSelectedPdfTool(tool, rel) {
      closeModal();
      const extra = {};
      if (tool === "protect" || tool === "unlock") {
        const pw = await askInputSheet({ title: tool === "protect" ? "Protect PDF" : "Unlock PDF", label: "Password", type: "password", confirmText: "Continue" });
        if (pw === null) return;
        extra.password = pw;
      } else if (tool === "watermark") {
        const txt = await askInputSheet({ title: "Watermark Text", defaultValue: "CONFIDENTIAL", confirmText: "Apply" });
        if (!txt) return;
        extra.text = txt;
      } else if (tool === "stamp") {
        const lbl = await askInputSheet({ title: "Approval Stamp", defaultValue: "APPROVED", confirmText: "Apply" });
        if (!lbl) return;
        extra.label = lbl;
      } else if (tool === "split") {
        const rng = await askInputSheet({ title: "Page Ranges", label: "Example: 1-2, 4 or all", defaultValue: "all", confirmText: "Split" });
        if (!rng) return;
        extra.ranges = rng;
      }
      const r = await nativeCall("runPdfTool", Object.assign({ tool, path: rel }, extra));
      await refreshWorkspace();
      toast("Completed: " + (r.rel || "Pages saved"));
      if (r.rel) openFile(r.rel);
    },

    vaultEncryptPicker() {
      const plain = state.files.filter((f) => f.ext !== "movault");
      if (!plain.length) { toast("No unsealed documents in workspace"); return; }
      showModal(`
        <h3 class="modal-title">Select File to Seal (.movault)</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          ${plain.map((f) => `<button class="action-row" onclick="App.lockSingleFile('${esc(f.rel)}')">${svgIcon("lock")}<div class="action-row-body"><div class="action-row-title">${esc(f.name)}</div></div></button>`).join("")}
        </div>
      `);
    },

    vaultDecryptPicker() {
      const vaults = state.files.filter((f) => f.ext === "movault");
      if (!vaults.length) { toast("No .movault files in workspace"); return; }
      showModal(`
        <h3 class="modal-title">Select .movault File</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          ${vaults.map((f) => `<button class="action-row" onclick="App.closeModal();App.openFile('${esc(f.rel)}')">${svgIcon("unlock")}<div class="action-row-body"><div class="action-row-title">${esc(f.name)}</div></div></button>`).join("")}
        </div>
      `);
    },

    async exportVaultRecoveryKit() {
      const pw = await askInputSheet({ title: "Export Vault Key Kit", label: "Password to protect .movaultkey file", type: "password", confirmText: "Export" });
      if (!pw) return;
      const r = await nativeCall("exportVaultKeyKit", { password: pw });
      await refreshWorkspace();
      toast("Saved " + r.rel);
    },

    async importVaultRecoveryKit() {
      const kits = state.files.filter((f) => f.ext === "movaultkey");
      if (!kits.length) { toast("Import your PC .movaultkey file first via Import"); return; }
      const pw = await askInputSheet({ title: "Authorize PC Vault Key", label: "Password for " + kits[0].name, type: "password", confirmText: "Import" });
      if (!pw) return;
      const r = await nativeCall("importVaultKeyKit", { path: kits[0].rel, password: pw });
      toast("Authorized " + r.importedKeys.length + " key(s)");
    },

    async qrGeneratorModal() {
      const text = await askInputSheet({ title: "Generate QR Code", label: "Link or text", defaultValue: "https://myoffice.local", confirmText: "Generate" });
      if (!text) return;
      const r = await nativeCall("makeQrSvg", { text });
      showModal(`<h3 class="modal-title">QR Code</h3><div style="text-align:center;"><div class="qr-box">${r.svg}</div></div>`);
    },

    repairDocPicker() {
      const ooxml = state.files.filter((f) => ["docx", "xlsx", "pptx"].includes(f.ext));
      if (!ooxml.length) { toast("No DOCX, XLSX, or PPTX files found"); return; }
      showModal(`
        <h3 class="modal-title">Select File to Verify & Repair</h3>
        <div class="action-list" style="border:1px solid var(--line);margin-bottom:0;">
          ${ooxml.map((f) => `<button class="action-row" onclick="App.runRepairFile('${esc(f.rel)}')">${svgIcon("check")}<div class="action-row-body"><div class="action-row-title">${esc(f.name)}</div></div></button>`).join("")}
        </div>
      `);
    },

    async runRepairFile(rel) {
      closeModal();
      const r = await nativeCall("repairDocument", { path: rel });
      await refreshWorkspace();
      toast("Verified & repaired: " + r.rel);
    },

    async runSelfDiagnostics() {
      toast("Running system self-checks...");
      const r = await nativeCall("runSelfTest");
      const rows = (r.checks || []).map((c) => `
        <div style="padding:9px 0;border-bottom:1px solid var(--line);font-size:13px;">
          <div style="font-weight:700;color:${c.ok ? "var(--ok)" : "var(--danger)"}">${c.ok ? "OK" : "FAIL"} · ${esc(c.name)} <span style="color:var(--ink-muted);font-weight:400;">(${c.ms} ms)</span></div>
          <div style="color:var(--ink-secondary);font-size:12px;">${esc(c.detail)}</div>
        </div>
      `).join("");
      showModal(`<h3 class="modal-title">${r.ok ? "All Subsystems Healthy" : "Diagnostics Report"}</h3>${rows}`);
    },

    // GitHub Dual-Tier OTA Self-Updater UI
    openOtaUpdateSheet() {
      const repoVal = state.config.updateRepoUrl || "";
      const info = state.pendingOta;
      const statusHtml = info && info.available
        ? `<div class="panel" style="background:var(--accent-soft);border-color:var(--accent);margin-bottom:12px;">
             <div style="font-weight:700;color:var(--accent);font-size:14px;">Update v${esc(info.latestVersion)} Ready (${info.mode === "hotpatch" ? "Instant <1s Live Patch" : "Full Signed APK"})</div>
             <div style="font-size:12.5px;color:var(--ink-secondary);margin-top:4px;">${esc(info.notes || "Signed release verified with ECDSA-P256.")}</div>
           </div>`
        : `<p class="section-desc">Active: <strong>v${esc(state.version)}</strong> (Base APK ${esc(state.nativeVersion)}). Enter your GitHub repo once — future updates verify ECDSA signature & apply automatically without rebuilding APKs.</p>`;

      showModal(`
        <h3 class="modal-title">GitHub Auto-Update (OTA)</h3>
        ${statusHtml}
        <label class="field-label">GitHub Repository URL</label>
        <input class="input" id="otaRepoInput" value="${esc(repoVal)}" placeholder="https://github.com/username/myoffice" />
        <label class="field-label">Private Repo Read Token (Leave blank for Public repo)</label>
        <input class="input" type="password" id="otaTokenInput" placeholder="Optional fine-grained read token" />
        <div id="otaStatusMsg" style="font-size:12.5px;color:var(--ink-secondary);margin-top:10px;"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px;">
          <button class="btn btn-subtle" onclick="App.checkOtaNow()">Check Update</button>
          <button class="btn btn-primary" onclick="App.applyOtaNow()">Save & Update</button>
        </div>
        <button class="btn btn-compact btn-subtle btn-block" style="margin-top:8px;" onclick="App.resetOtaFactoryNow()">Reset to Built-in APK Version</button>
      `);
    },

    async checkOtaNow() {
      const repo = document.getElementById("otaRepoInput")?.value.trim() || "";
      const tok = document.getElementById("otaTokenInput")?.value.trim() || "";
      const msgEl = document.getElementById("otaStatusMsg");
      if (msgEl) msgEl.textContent = "Checking signed GitHub manifest...";
      try {
        const payload = { updateRepoUrl: repo };
        if (tok) payload.githubReadToken = tok;
        const r = await nativeCall("checkOtaUpdate", payload);
        state.config.updateRepoUrl = repo;
        state.pendingOta = r;
        renderOtaBanner();
        if (msgEl) {
          msgEl.textContent = r.available
            ? `New version v${r.latestVersion} available (${r.mode === "hotpatch" ? Math.round((r.size || 0) / 1024) + " KB instant live patch" : "Signed APK"}) — tap Save & Update.`
            : `Up to date (v${r.currentVersion}, ECDSA verified).`;
        }
      } catch (e) {
        if (msgEl) msgEl.textContent = "Check error: " + e.message;
      }
    },

    async applyOtaNow() {
      const repo = (document.getElementById("otaRepoInput")?.value.trim() || state.config.updateRepoUrl || "https://github.com/ABDULLAH-AMJID/MyOffice").trim();
      const tok = document.getElementById("otaTokenInput")?.value.trim() || "";
      if (!repo) { toast("Enter your GitHub repository URL first"); return; }

      // Instant 0ms live progress sheet + Home banner update BEFORE starting network call!
      const targetVer = state.pendingOta?.latestVersion || "Latest";
      const isHotpatch = state.pendingOta?.mode === "hotpatch";
      openOtaLiveProgressModal(targetVer, isHotpatch);
      handleNativeOtaProgress({
        phase: "connecting",
        percent: 2,
        downloaded: 0,
        total: state.pendingOta?.size || 0,
        speedBps: 0,
        etaSec: -1,
        version: targetVer,
        mode: state.pendingOta?.mode || "apk"
      });

      try {
        if (tok) await nativeCall("saveSettings", { updateRepoUrl: repo, githubReadToken: tok });
        else await nativeCall("saveSettings", { updateRepoUrl: repo });
        state.config.updateRepoUrl = repo;
        const applied = await nativeCall("applyOtaUpdate", { updateRepoUrl: repo });
        toast(`Updating to v${applied.version}...`);
      } catch (e) {
        handleNativeOtaProgress({ phase: "error", percent: 0, error: e.message || String(e) });
      }
    },

    async cancelOtaDownloadNow() {
      const btn = document.getElementById("otaCancelBtn");
      if (btn) { btn.disabled = true; btn.textContent = "Cancelling..."; }
      try {
        await nativeCall("cancelOtaUpdate", {});
      } catch (_) {}
    },

    async resetOtaFactoryNow() {
      const ok = await askConfirmSheet({
        title: "Reset to Built-in APK Version",
        message: "Revert live hot-patches back to the factory built-in UI shipped inside the APK?",
        confirmText: "Reset"
      });
      if (!ok) return;
      await nativeCall("resetOtaFactory");
    },

    // AI Assistant
    openAiSettingsSheet() {
      const curProv = document.getElementById("aiProviderSelect").value;
      const curModel = document.getElementById("aiModelInput").value;
      const curEnd = document.getElementById("aiEndpointInput").value;
      showModal(`
        <h3 class="modal-title">Assistant Model Settings</h3>
        <label class="field-label">Provider</label>
        <select class="input" id="sheetAiProv">
          <option value="ollama" ${curProv === "ollama" ? "selected" : ""}>Local PC Ollama (Free LAN)</option>
          <option value="gemini" ${curProv === "gemini" ? "selected" : ""}>Google Gemini</option>
          <option value="openai" ${curProv === "openai" ? "selected" : ""}>OpenAI</option>
          <option value="groq" ${curProv === "groq" ? "selected" : ""}>Groq</option>
          <option value="anthropic" ${curProv === "anthropic" ? "selected" : ""}>Anthropic Claude</option>
          <option value="openrouter" ${curProv === "openrouter" ? "selected" : ""}>OpenRouter</option>
        </select>
        <label class="field-label">Model Name</label>
        <input class="input" id="sheetAiModel" value="${esc(curModel)}" />
        <label class="field-label">Custom Endpoint (Ollama LAN URL)</label>
        <input class="input" id="sheetAiEnd" value="${esc(curEnd)}" placeholder="http://192.168.1.10:11434" />
        <label class="field-label">API Key (Stored Encrypted)</label>
        <input class="input" type="password" id="sheetAiKey" placeholder="Leave unchanged to keep saved key" />
        <button class="btn btn-primary btn-block" style="margin-top:14px;" onclick="App.commitAiSettingsSheet()">Save Model Settings</button>
      `);
    },

    commitAiSettingsSheet() {
      document.getElementById("aiProviderSelect").value = document.getElementById("sheetAiProv").value;
      document.getElementById("aiModelInput").value = document.getElementById("sheetAiModel").value.trim();
      document.getElementById("aiEndpointInput").value = document.getElementById("sheetAiEnd").value.trim();
      const k = document.getElementById("sheetAiKey").value;
      if (k) document.getElementById("aiKeyInput").value = k;
      closeModal();
      nativeCall("saveSettings", {
        aiProvider: document.getElementById("aiProviderSelect").value,
        aiModel: document.getElementById("aiModelInput").value,
        aiEndpoint: document.getElementById("aiEndpointInput").value,
        aiApiKey: k || "••••••••"
      });
      toast("Model settings saved");
    },

    quickAiPrompt(prefix) {
      const el = document.getElementById("aiPromptInput");
      el.value = prefix + (el.value ? "\n" + el.value : "");
      el.focus();
    },

    async askAiNow() {
      const promptText = document.getElementById("aiPromptInput").value.trim();
      if (!promptText) { toast("Enter a question or instruction first"); return; }
      const ansEl = document.getElementById("aiAnswerBox");
      ansEl.classList.remove("hidden");
      ansEl.textContent = "Generating response...";
      try {
        const res = await nativeCall("askAi", {
          provider: document.getElementById("aiProviderSelect").value,
          model: document.getElementById("aiModelInput").value.trim(),
          endpoint: document.getElementById("aiEndpointInput").value.trim(),
          apiKey: document.getElementById("aiKeyInput").value,
          pdfPath: document.getElementById("aiPdfSelect").value,
          prompt: promptText
        });
        ansEl.textContent = res.answer;
      } catch (e) {
        ansEl.textContent = "Error: " + e.message;
      }
    }
  };

  function renderPcLiveRooms(rooms) {
    const box = document.getElementById("pcLiveRoomsBox");
    if (!box || !rooms.length) return;
    box.innerHTML = `<label class="field-label">Active Rooms on Paired PC</label><div style="display:flex;gap:6px;flex-wrap:wrap;">` +
      rooms.map((r) => `<button class="btn btn-compact btn-subtle" onclick="document.getElementById('collabLinkInput').value='${esc(r.ws_url || r.join_uri)}';App.joinCollabRoomNow()">Join ${esc(r.room)}</button>`).join("") +
      `</div>`;
  }

  function formatBytesCompact(n) {
    if (!n || n <= 0) return "0 KB";
    if (n >= 1048576) return (n / 1048576).toFixed(2) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }

  function openOtaLiveProgressModal(targetVer, isHotpatch) {
    showModal(`
      <h3 class="modal-title" id="otaProgTitle">Updating MyOffice to v${esc(targetVer)}</h3>
      <p class="section-desc" id="otaProgPhase" style="font-weight:600;color:var(--ink);">Connecting to GitHub CDN (6x Turbo Parallel Streams)...</p>
      <div style="width:100%;height:10px;background:var(--surface-muted,#E5E7EB);border-radius:999px;overflow:hidden;margin:12px 0 8px;">
        <div id="otaProgBarFill" style="height:100%;width:2%;background:var(--accent,#2563EB);border-radius:999px;transition:width 0.14s ease;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12.5px;color:var(--ink-secondary);font-variant-numeric:tabular-nums;">
        <span id="otaProgBytes">Starting download...</span>
        <span id="otaProgSpeed" style="font-weight:700;color:var(--accent,#2563EB);">2%</span>
      </div>
      <div style="font-size:11.5px;color:var(--ink-muted,#6B7280);margin-top:8px;">
        ${isHotpatch ? "Instant verified UI patch — reloads automatically with zero data loss." : "6x parallel stream download + SHA-256 verification. Android will prompt 'Update' as soon as download completes."}
      </div>
      <div id="otaProgActions" style="margin-top:14px;display:flex;gap:8px;">
        <button class="btn btn-subtle btn-block" id="otaCancelBtn" onclick="App.cancelOtaDownloadNow()">Cancel Download</button>
      </div>
    `);
  }

  function handleNativeOtaProgress(rawEvt) {
    const p = (typeof rawEvt === "string") ? JSON.parse(rawEvt) : (rawEvt || {});
    const pct = Math.max(0, Math.min(100, Number(p.percent || 0)));
    const phase = p.phase || "downloading";
    const done = Number(p.downloaded || 0);
    const total = Number(p.total || 0);
    const spd = Number(p.speedBps || 0);
    const eta = Number(p.etaSec ?? -1);
    const streams = Number(p.turboStreams || 6);

    const spdStr = spd >= 1048576
      ? (spd / 1048576).toFixed(2) + " MB/s"
      : (spd > 0 ? Math.max(1, Math.round(spd / 1024)) + " KB/s" : "");
    const etaStr = (eta > 0 && phase === "downloading") ? ` • ~${eta}s left` : "";

    let phaseLabel = "Preparing update...";
    if (phase === "checking") phaseLabel = "Checking signed GitHub release manifest...";
    else if (phase === "connecting") phaseLabel = `Connecting to GitHub CDN (${streams}x Turbo Parallel Streams)...`;
    else if (phase === "downloading") phaseLabel = `Downloading update (${streams > 1 ? streams + "x Parallel Turbo" : "Direct Stream"})...`;
    else if (phase === "verifying") phaseLabel = "Verifying SHA-256 & ECDSA P-256 cryptographic signature...";
    else if (phase === "installing_apk" || phase === "ready") phaseLabel = "Download verified 100%! Opening Android Installer...";
    else if (phase === "reloading_ui") phaseLabel = "Update verified 100%! Activating live update now...";
    else if (phase === "cancelled") phaseLabel = "Update download cancelled.";
    else if (phase === "error") phaseLabel = "Update error: " + (p.error || "Network error");

    const titleEl = document.getElementById("otaProgTitle");
    const phaseEl = document.getElementById("otaProgPhase");
    const fillEl = document.getElementById("otaProgBarFill");
    const bytesEl = document.getElementById("otaProgBytes");
    const speedEl = document.getElementById("otaProgSpeed");
    const actionsEl = document.getElementById("otaProgActions");

    if (titleEl && p.version) titleEl.textContent = `Updating MyOffice to v${p.version}`;
    if (phaseEl) phaseEl.textContent = phaseLabel;
    if (fillEl) {
      fillEl.style.width = `${pct}%`;
      fillEl.style.background = phase === "error" ? "#DC2626" : (pct >= 100 ? "#16A34A" : "var(--accent,#2563EB)");
    }
    if (bytesEl) {
      bytesEl.textContent = total > 0
        ? `${formatBytesCompact(done)} / ${formatBytesCompact(total)}${etaStr}`
        : (done > 0 ? `${formatBytesCompact(done)} downloaded` : phaseLabel);
    }
    if (speedEl) {
      speedEl.textContent = spdStr ? `${spdStr} (${pct}%)` : `${pct}%`;
    }
    if (actionsEl && (phase === "error" || phase === "cancelled" || phase === "installing_apk")) {
      actionsEl.innerHTML = phase === "installing_apk"
        ? `<button class="btn btn-primary btn-block" onclick="App.closeModalSheet && App.closeModalSheet()">Done</button>`
        : `<button class="btn btn-subtle btn-block" onclick="App.applyOtaNow()">Retry Update</button>`;
    }

    // Also mirror live status onto the Home Screen Banner so even if user dismisses modal, banner shows live %!
    const bannerBtn = document.getElementById("otaBannerInstallBtn");
    const bannerSub = document.getElementById("otaBannerSub");
    if (bannerBtn) {
      if (phase === "downloading" || phase === "connecting" || phase === "checking" || phase === "verifying") {
        bannerBtn.disabled = true;
        bannerBtn.textContent = `${pct}%`;
      } else {
        bannerBtn.disabled = false;
        bannerBtn.textContent = "Install";
      }
    }
    if (bannerSub && (phase === "downloading" || phase === "verifying" || phase === "connecting")) {
      bannerSub.textContent = `${total > 0 ? formatBytesCompact(done) + " / " + formatBytesCompact(total) : phaseLabel}${spdStr ? " • " + spdStr : ""}`;
    }
  }
  window.__onNativeOtaProgress = handleNativeOtaProgress;

  function renderOtaBanner() {
    let banner = document.getElementById("otaBannerCard");
    if (!state.pendingOta || !state.pendingOta.available) {
      if (banner) banner.remove();
      return;
    }
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "otaBannerCard";
      banner.className = "panel";
      banner.style.cssText = "background:var(--accent-soft);border-color:var(--accent);display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 14px;";
      const home = document.getElementById("tabHome");
      home.insertBefore(banner, home.firstChild);
    }
    const szLabel = state.pendingOta.size > 0 ? ` (${formatBytesCompact(state.pendingOta.size)})` : "";
    banner.innerHTML = `
      <div style="flex:1;min-width:0;">
        <div style="font-size:13.5px;font-weight:700;color:var(--accent);">Update v${esc(state.pendingOta.latestVersion)} Available${esc(szLabel)}</div>
        <div id="otaBannerSub" style="font-size:12px;color:var(--ink-secondary);">${esc(state.pendingOta.notes || "Signed GitHub update ready")}</div>
      </div>
      <button id="otaBannerInstallBtn" class="btn btn-compact btn-primary" onclick="App.applyOtaNow()">Install</button>
    `;
  }

  // ==================== BOOTSTRAP ====================
  async function boot() {
    try {
      if (window.AndroidNative && typeof window.AndroidNative.confirmUiBootHealthy === "function") {
        window.AndroidNative.confirmUiBootHealthy();
      }
    } catch (_) {}
    document.getElementById("btnQuickSync").onclick = () => App.triggerQuickSync();
    document.getElementById("btnPrefs").onclick = () => openPreferencesSheet();
    window.MyOfficeGestures.attachBottomSheetSwipeClose(document.getElementById("modalSheet"), closeModal);

    applyTheme("light", false);
    try {
      const init = await nativeCall("getBootstrap");
      state.files = init.files || [];
      state.config = init.config || {};
      state.version = init.version || "1.6.0";
      state.nativeVersion = init.nativeVersion || "1.6.0";
      state.deviceInfo = {
        name: init.deviceName || "Android Device",
        ip: init.primaryIp || "Offline",
        keyId: init.vaultKeyId || "—"
      };
      if (init.config.theme) applyTheme(init.config.theme, false);
      if (init.config.lang) state.lang = init.config.lang;
      document.getElementById("lblVaultKeyId").textContent = init.vaultKeyId || "—";
      document.getElementById("pcUrlInput").value = init.config.pcUrl || "";
      document.getElementById("gmailAddressInput").value = init.config.gmailAddress || "";
      document.getElementById("ftpUrlInput").value = init.config.ftpUrl || "";
      document.getElementById("chkAutoSync").checked = Boolean(init.config.autoSync);
      document.getElementById("chkVaultSync").checked = Boolean(init.config.vaultEncryptSync);
      if (init.config.aiProvider) document.getElementById("aiProviderSelect").value = init.config.aiProvider;
      if (init.config.aiModel) document.getElementById("aiModelInput").value = init.config.aiModel;
      if (init.config.aiEndpoint) document.getElementById("aiEndpointInput").value = init.config.aiEndpoint;
      document.getElementById("collabUserName").value = init.deviceName || "Mobile User";
      applyLang(false);
      renderFiles();
      refreshPdfDropdown();
      // Silent startup GitHub OTA check whenever a repo URL is configured
      if (state.config.updateRepoUrl) {
        setTimeout(async () => {
          try {
            const ota = await nativeCall("checkOtaUpdate", {});
            if (ota && ota.available) {
              state.pendingOta = ota;
              renderOtaBanner();
            }
          } catch (_) {}
        }, 1200);
      }
    } catch (_) {
      applyLang(false);
      renderFiles();
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
