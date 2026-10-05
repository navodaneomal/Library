/* Camera barcode scanning — an enhancement, never a requirement.
   Uses the native BarcodeDetector where available (Chrome/Android) and
   lazily loads the bundled ZXing decoder elsewhere (e.g. iPhone Safari). */
(function () {
  "use strict";

  var buttons = document.querySelectorAll("[data-camera-for]");
  if (!buttons.length) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.isSecureContext) {
    return; // buttons stay hidden
  }

  var dialog = document.getElementById("camera-dialog");
  var video = dialog && dialog.querySelector("video");
  var status = dialog && dialog.querySelector("[data-camera-status]");
  var zxingUrl = dialog && dialog.getAttribute("data-zxing");
  var stream = null;
  var reader = null;
  var running = false;
  var targetInput = null;

  function loadZXing() {
    if (window.ZXing) return Promise.resolve(window.ZXing);
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = zxingUrl;
      script.onload = function () { resolve(window.ZXing); };
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  function stop() {
    running = false;
    if (reader) {
      try { reader.reset(); } catch (e) {}
      reader = null;
    }
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    if (dialog && dialog.open) dialog.close();
  }

  function found(text) {
    if (!running || !text) return;
    stop();
    targetInput.value = text.trim();
    targetInput.focus();
    var form = targetInput.form;
    if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  }

  function useNative() {
    var detector = new window.BarcodeDetector();
    function tick() {
      if (!running) return;
      detector
        .detect(video)
        .then(function (codes) {
          if (codes && codes.length) found(codes[0].rawValue);
          else requestAnimationFrame(tick);
        })
        .catch(function () { requestAnimationFrame(tick); });
    }
    tick();
  }

  function useZXing() {
    status.textContent = "Loading scanner…";
    return loadZXing().then(function (ZXing) {
      status.textContent = "Point the camera at a barcode.";
      reader = new ZXing.BrowserMultiFormatReader();
      reader.decodeFromStream(stream, video, function (result) {
        if (result) found(result.getText());
      });
    });
  }

  function start(input) {
    targetInput = input;
    running = true;
    status.textContent = "Starting camera…";
    dialog.showModal();
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (s) {
        stream = s;
        video.srcObject = s;
        video.setAttribute("playsinline", "true");
        return video.play();
      })
      .then(function () {
        status.textContent = "Point the camera at a barcode.";
        if ("BarcodeDetector" in window) return useNative();
        return useZXing();
      })
      .catch(function () {
        status.textContent = "We couldn't open the camera. You can type the code instead.";
        running = false;
      });
  }

  buttons.forEach(function (btn) {
    btn.hidden = false;
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.getAttribute("data-camera-for"));
      if (input && dialog) start(input);
    });
  });

  if (dialog) {
    dialog.addEventListener("close", stop);
    dialog.querySelector("[data-camera-close]").addEventListener("click", stop);
  }
})();
