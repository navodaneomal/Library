/* Applies the remembered light/dark preference before first paint. */
(function () {
  try {
    var theme = localStorage.getItem("theme");
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    }
  } catch (e) {
    /* storage unavailable — follow the system setting */
  }
})();
