/* "Fill from ISBN" on the book form: asks the server to look the ISBN up
   (Open Library) and fills only the fields that are still empty. */
(function () {
  "use strict";
  var button = document.querySelector("[data-isbn-fill]");
  if (!button) return;
  var isbn = document.getElementById("id_isbn");
  var status = document.querySelector("[data-isbn-status]");
  var map = {
    title: "id_title",
    subtitle: "id_subtitle",
    authors: "id_author_names",
    publisher: "id_publisher_name",
    publication_year: "id_publication_year",
    keywords: "id_keywords",
    cover_url: "id_cover_url",
  };
  button.addEventListener("click", function () {
    var value = (isbn.value || "").trim();
    if (!value) {
      status.textContent = "Enter or scan an ISBN first.";
      isbn.focus();
      return;
    }
    status.textContent = "Looking up the book…";
    button.disabled = true;
    fetch(button.getAttribute("data-isbn-fill") + "?isbn=" + encodeURIComponent(value), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.found) {
          status.textContent = "No details found — please type them in.";
          return;
        }
        var filled = 0;
        Object.keys(map).forEach(function (key) {
          var field = document.getElementById(map[key]);
          if (field && !field.value && data[key]) {
            field.value = data[key];
            filled += 1;
          }
        });
        status.textContent = filled ? "Filled " + filled + " fields from Open Library. Please check them." : "Nothing new to fill.";
      })
      .catch(function () { status.textContent = "Lookup is unavailable right now — please type the details."; })
      .finally(function () { button.disabled = false; });
  });
})();
