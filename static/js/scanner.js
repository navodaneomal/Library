/* Barcode-scanner support for desk pages.
   USB scanners type the code and press Enter, so the scan input simply has
   to stay focused. We keep it focused, route stray keystrokes into it,
   play a short tone for success/error, and support Esc to reset. */
(function () {
  "use strict";

  var station = document.querySelector("[data-scan-station]");
  if (!station) return;

  function primaryInput() {
    return document.querySelector("[data-scan-primary]");
  }

  // Only real text entry elsewhere (another input, select, textarea) keeps
  // focus. Buttons and links don't: a USB scanner "types", so after any
  // click the scan box must be ready again or the next scan is lost.
  function isTypingElsewhere() {
    var el = document.activeElement;
    if (!el || el === document.body || el.matches("[data-scan-primary]")) return false;
    if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
    if (el.tagName === "INPUT") {
      return !/^(button|submit|reset|checkbox|radio|file|image)$/i.test(el.type);
    }
    return false;
  }

  function refocus() {
    var input = primaryInput();
    var active = document.activeElement;
    // Respect a next step the server highlighted (e.g. "Continue to details").
    if (active && active !== input && active.matches("[data-autofocus]")) return;
    if (input && !isTypingElsewhere() && !document.querySelector("dialog[open]")) input.focus();
  }

  // Keystrokes that arrive while focus is on the page or on a button go to
  // the scan input (Space and Enter are left alone so buttons still work
  // from the keyboard).
  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      var reset = document.querySelector("[data-scan-reset]");
      if (reset && !document.querySelector("dialog[open]")) {
        event.preventDefault();
        reset.click();
      }
      return;
    }
    if (event.key.length !== 1 || event.key === " ") return;
    if (!isTypingElsewhere() && !document.querySelector("dialog[open]")) {
      var input = primaryInput();
      if (input && document.activeElement !== input) input.focus();
    }
  });

  document.addEventListener("click", function (event) {
    // detail === 0: a keyboard-generated click (Enter submitting a form);
    // the swap helper decides focus in that case.
    if (event.detail === 0 || event.target.closest("a[href], summary, label")) return;
    setTimeout(refocus, 60);
  });
  window.addEventListener("focus", refocus);
  setTimeout(refocus, 30);

  // Sound feedback (Web Audio — no files). Toggle remembered per browser.
  var soundOn = true;
  try {
    soundOn = localStorage.getItem("scanSound") !== "off";
  } catch (e) {}
  var toggle = document.querySelector("[data-sound-toggle]");
  function syncToggle() {
    if (!toggle) return;
    toggle.setAttribute("aria-pressed", soundOn ? "true" : "false");
    toggle.title = soundOn ? "Scan sounds on" : "Scan sounds off";
    toggle.classList.toggle("muted", !soundOn);
  }
  if (toggle) {
    toggle.addEventListener("click", function () {
      soundOn = !soundOn;
      try {
        localStorage.setItem("scanSound", soundOn ? "on" : "off");
      } catch (e) {}
      syncToggle();
    });
  }
  syncToggle();

  var audio = null;
  function tone(freq, start, duration, ctx) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime + start);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + start + duration);
    osc.connect(gain).connect(ctx.destination);
    osc.start(ctx.currentTime + start);
    osc.stop(ctx.currentTime + start + duration + 0.02);
  }
  function beep(kind) {
    if (!soundOn) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (kind === "error") {
        tone(330, 0, 0.14, audio);
        tone(260, 0.16, 0.2, audio);
      } else if (kind === "warning") {
        tone(660, 0, 0.12, audio);
        tone(520, 0.14, 0.14, audio);
      } else {
        tone(880, 0, 0.12, audio);
      }
    } catch (e) {}
  }

  document.addEventListener("library:swapped", function (event) {
    var result = event.detail && event.detail.result;
    if (result) beep(result.getAttribute("data-sound"));
    // Announce to screen readers via the live region inside the fragment.
  });
})();
