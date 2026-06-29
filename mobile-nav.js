/**
 * mobile-nav.js — Injects hamburger + drawer into every page
 * Include AFTER nav markup. Works with .topnav pages and index.html sidebar.
 *
 * Auto-detects current page to mark active drawer link.
 */

(function () {
  const LINKS = [
    { href: "/",               icon: "📊", label: "Overview" },
    { href: "/global.html",    icon: "🌐", label: "Global Market" },
    { href: "/etf.html",       icon: "📦", label: "ETF Board" },
    { href: "/leaders.html",   icon: "🏆", label: "Leadership Board" },
    { href: "/thematic.html",  icon: "🧩", label: "Thematic Matrix" },
    { href: "/rotation.html",  icon: "🔄", label: "Rotation Chart" },
    { href: "/screener.html",  icon: "🔍", label: "Screener" },
    { href: "/correlation.html",icon:"📈", label: "Correlations" },
    { href: "/calendar.html",  icon: "📅", label: "Economic Calendar" },
    { href: "/stock.html",     icon: "🔬", label: "Stock Deep Dive" },
  ];

  function currentPath() {
    const p = window.location.pathname;
    if (p === "/" || p === "/index.html") return "/";
    return p.split("/").pop() ? "/" + p.split("/").pop() : p;
  }

  function buildDrawer() {
    const path   = currentPath();
    const overlay = document.createElement("div");
    overlay.className = "nav-overlay";
    overlay.id        = "navOverlay";

    const drawer = document.createElement("div");
    drawer.className  = "nav-drawer";
    drawer.id         = "navDrawer";
    drawer.innerHTML  = `
      <div class="nav-drawer-brand">
        <div class="brand-icon">🛝</div>
        <div>
          <div class="brand-text">สนามเด็กเล่น</div>
          <div class="brand-sub">Playground · Live</div>
        </div>
      </div>
      <div class="nav-drawer-links">
        ${LINKS.map(l => `
          <a class="nav-drawer-link${l.href === path ? " active" : ""}" href="${l.href}">
            <span class="icon">${l.icon}</span>
            <span>${l.label}</span>
          </a>`).join("")}
      </div>`;

    document.body.appendChild(overlay);
    document.body.appendChild(drawer);
    return { overlay, drawer };
  }

  function buildHamburger() {
    const btn = document.createElement("button");
    btn.className = "nav-hamburger";
    btn.id        = "navHamburger";
    btn.setAttribute("aria-label", "Menu");
    btn.innerHTML = "<span></span><span></span><span></span>";
    return btn;
  }

  function init() {
    const topnav = document.querySelector(".topnav");
    if (!topnav) return; // index.html has sidebar — handled separately

    const { overlay, drawer } = buildDrawer();
    const btn = buildHamburger();
    topnav.appendChild(btn);

    function open()  { drawer.classList.add("open"); overlay.classList.add("open"); btn.classList.add("open"); document.body.style.overflow = "hidden"; }
    function close() { drawer.classList.remove("open"); overlay.classList.remove("open"); btn.classList.remove("open"); document.body.style.overflow = ""; }

    btn.addEventListener("click", () => drawer.classList.contains("open") ? close() : open());
    overlay.addEventListener("click", close);
    drawer.querySelectorAll("a").forEach(a => a.addEventListener("click", close));
    document.addEventListener("keydown", e => e.key === "Escape" && close());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
