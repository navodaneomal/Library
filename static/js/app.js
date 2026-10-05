/* Library — progressive enhancement. Every feature here has a working
   server-side fallback; JavaScript only makes things feel instant. */
(function () {
  "use strict";

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = csrfMeta ? csrfMeta.content : "";
  var progress = document.getElementById("progress");

  /* ---------------------------------------------------------------- theme */
  function currentTheme() {
    var explicit = document.documentElement.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeButtons() {
    var dark = currentTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
      btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      var use = btn.querySelector("use");
      if (use) {
        var href = use.getAttribute("href");
        use.setAttribute("href", href.replace(/#(sun|moon)$/, dark ? "#sun" : "#moon"));
      }
      var label = btn.querySelector("[data-theme-label]");
      if (label) label.textContent = dark ? "Light mode" : "Dark mode";
    });
  }
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch (e) {}
    syncThemeButtons();
  });
  syncThemeButtons();

  /* --------------------------------------------------------------- toasts */
  function dismiss(toast) {
    toast.classList.add("is-leaving");
    setTimeout(function () {
      toast.remove();
    }, 260);
  }
  document.querySelectorAll(".toast").forEach(function (toast) {
    var isError = toast.classList.contains("toast--error");
    if (!isError) setTimeout(function () { dismiss(toast); }, 6000);
  });
  document.addEventListener("click", function (event) {
    var close = event.target.closest(".toast__close");
    if (close) dismiss(close.closest(".toast"));
  });

  /* ------------------------------------------------------ confirm actions */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  /* ------------------------------------------------- menus (details) */
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll("details.menu[open]").forEach(function (menu) {
        menu.removeAttribute("open");
        var summary = menu.querySelector("summary");
        if (summary) summary.focus();
      });
    }
  });

  /* ------------------------------------------------------ staff sidebar */
  var sidebar = document.getElementById("sidebar");
  var scrim = document.getElementById("scrim");
  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    if (scrim) scrim.hidden = !open;
    document.querySelectorAll("[data-sidebar-toggle]").forEach(function (b) {
      b.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-sidebar-toggle]")) setSidebar(!sidebar.classList.contains("is-open"));
    else if (event.target === scrim) setSidebar(false);
  });

  /* "/" focuses the search box */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "/" || event.ctrlKey || event.metaKey) return;
    var tag = (document.activeElement && document.activeElement.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    var search = document.querySelector("[data-global-search]");
    if (search) {
      event.preventDefault();
      search.focus();
    }
  });

  /* -------------------------------------------- instant form swapping
     <form data-swap="#target"> posts with fetch and replaces #target with
     the server-rendered fragment. Without JS the form posts normally.   */
  function setBusy(on) {
    if (!progress) return;
    progress.classList.toggle("is-active", on);
  }

  function focusAfterSwap(target) {
    var auto = target.querySelector("[data-autofocus]") || document.querySelector("[data-scan-primary]");
    if (auto) {
      auto.focus();
      if (auto.select && auto.tagName === "INPUT") auto.select();
    }
  }

  window.LibrarySwap = function (form, extra) {
    var targetSelector = form.getAttribute("data-swap");
    var target = document.querySelector(targetSelector);
    if (!target) return Promise.resolve();
    var data = new FormData(form);
    if (extra) {
      Object.keys(extra).forEach(function (key) { data.set(key, extra[key]); });
    }
    setBusy(true);
    target.setAttribute("aria-busy", "true");
    // getAttribute: a field named "action" would shadow form.action (DOM clobbering).
    var url = form.getAttribute("action") || window.location.href;
    return fetch(url, {
      method: "POST",
      body: data,
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrfToken },
    })
      .then(function (response) {
        if (response.status === 403 || response.status === 401) {
          window.location.reload();
          return null;
        }
        return response.text();
      })
      .then(function (html) {
        if (html === null) return;
        target.innerHTML = html; // server-rendered, auto-escaped template output
        target.removeAttribute("aria-busy");
        var result = target.querySelector("[data-sound]");
        document.dispatchEvent(new CustomEvent("library:swapped", { detail: { target: target, result: result } }));
        focusAfterSwap(target);
      })
      .catch(function () {
        target.removeAttribute("aria-busy");
        form.removeAttribute("data-swap");
        form.submit(); // network hiccup: fall back to a normal submit
      })
      .finally(function () {
        setBusy(false);
      });
  };

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-swap]");
    if (!form || event.defaultPrevented) return;
    event.preventDefault();
    var extra = {};
    if (event.submitter && event.submitter.name) extra[event.submitter.name] = event.submitter.value;
    window.LibrarySwap(form, extra);
    var input = form.querySelector("input[data-clear-after]");
    if (input) input.value = "";
  });

  /* -------------------------------------------- cover image fallback */
  document.addEventListener(
    "error",
    function (event) {
      var img = event.target;
      if (img && img.classList && img.classList.contains("cover__img")) img.remove();
    },
    true
  );

  /* ----------------------------------------------------- auto-submit */
  document.addEventListener("change", function (event) {
    var el = event.target.closest("[data-autosubmit]");
    if (el && el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
  });
})();

/* Print buttons */
document.addEventListener("click", function (event) {
  if (event.target.closest("[data-print]")) window.print();
});
