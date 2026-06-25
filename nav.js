# @title
/**
 * nav.js — Shared UX utilities (NO nav injection — nav is in each HTML)
 *
 * showLastUpdated(resp|string) → ใส่ timestamp ใน #nav-updated
 * setLoading(bool)             → toggle ⏳ ใน #nav-loading
 * coldStartBanner()            → returns {start, stop} for 3s-delay banner
 * fmtPct(v, digits)            → "+1.23%" with color class
 * rsBadge(rs)                  → colored RS pill HTML
 */

/** อัปเดต timestamp badge ใน nav */
function showLastUpdated(respOrString) {
  const el = document.getElementById("nav-updated");
  if (!el) return;
  let ts = "";
  if (typeof respOrString === "string") {
    ts = respOrString;
  } else if (respOrString && typeof respOrString.get === "function") {
    // Response headers
    ts = respOrString.get("X-Data-Updated") || "";
  } else if (respOrString && respOrString.updated) {
    ts = respOrString.updated;
  }
  if (ts) el.textContent = "🕐 " + ts;
}

/** Toggle loading indicator */
function setLoading(on) {
  const el = document.getElementById("nav-loading");
  if (el) el.style.display = on ? "inline" : "none";
}

/**
 * coldStartBanner() — shows banner if first fetch takes >3s
 * Usage: const cs = coldStartBanner(); cs.start(); ... cs.stop();
 */
function coldStartBanner() {
  let banner = document.getElementById("cold-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "cold-banner";
    banner.textContent = "⏳ กำลังโหลดข้อมูล... (ครั้งแรกอาจใช้เวลา 30-60 วินาที)";
    banner.style.cssText = [
      "position:fixed","bottom:20px","left:50%","transform:translateX(-50%)",
      "background:#1e2a40","color:#93c5fd","padding:10px 20px","border-radius:8px",
      "font-size:13px","z-index:9999","display:none","box-shadow:0 4px 12px #0005",
      "pointer-events:none","white-space:nowrap",
    ].join(";");
    document.body.appendChild(banner);
  }
  let timer = null;
  return {
    start() { timer = setTimeout(() => banner.style.display = "block", 3000); },
    stop()  { clearTimeout(timer); banner.style.display = "none"; },
  };
}

/** "+1.23%" with .pos / .neg span */
function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v))
    return '<span class="neutral">—</span>';
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "neutral";
  const s   = v > 0 ? "+" : "";
  return `<span class="${cls}">${s}${Number(v).toFixed(digits)}%</span>`;
}

/** Colored RS badge */
function rsBadge(rs) {
  const c = rs >= 80 ? "#10b981" : rs >= 60 ? "#f59e0b" : rs >= 40 ? "#6b7280" : "#ef4444";
  return `<span style="background:${c}22;color:${c};padding:1px 6px;border-radius:4px;font-size:11px">${rs}</span>`;
}

/**
 * Patch window.fetch to auto-call showLastUpdated + setLoading + coldStartBanner.
 * Call this ONCE per page after DOMContentLoaded.
 */
function installFetchHooks() {
  const _fetch = window.fetch;
  let cs = null;
  window.fetch = function (...args) {
    if (!cs) cs = coldStartBanner();
    cs.start();
    setLoading(true);
    return _fetch.apply(this, args)
      .then(resp => {
        // clone so caller can still read body
        const clone = resp.clone();
        clone.headers && showLastUpdated(clone);
        cs.stop();
        setLoading(false);
        return resp;
      })
      .catch(err => {
        cs.stop();
        setLoading(false);
        throw err;
      });
  };
}

// Auto-install hooks when DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", installFetchHooks);
} else {
  installFetchHooks();
}
