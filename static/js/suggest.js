/* Search-as-you-type suggestions (ARIA combobox). Results are built with
   textContent only, so nothing from the server is ever parsed as HTML. */
(function () {
  "use strict";

  function debounce(fn, ms) {
    var timer;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(null, args); }, ms);
    };
  }

  document.querySelectorAll("input[data-suggest]").forEach(function (input, index) {
    var url = input.getAttribute("data-suggest");
    var box = input.closest(".search");
    var list = document.createElement("ul");
    var listId = "suggest-list-" + index;
    var items = [];
    var active = -1;
    var controller = null;
    var lastQuery = "";

    list.id = listId;
    list.className = "suggest";
    list.setAttribute("role", "listbox");
    list.hidden = true;
    box.appendChild(list);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", listId);
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("autocomplete", "off");

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    function open() {
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function setActive(i) {
      items.forEach(function (el, n) { el.setAttribute("aria-selected", n === i ? "true" : "false"); });
      active = i;
      if (i >= 0 && items[i]) {
        input.setAttribute("aria-activedescendant", items[i].id);
        items[i].scrollIntoView({ block: "nearest" });
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    function el(tag, cls, text) {
      var node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text) node.textContent = text;
      return node;
    }

    function addOption(href, build) {
      var li = el("li", "suggest__item");
      li.id = listId + "-opt-" + items.length;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      li.dataset.href = href;
      build(li);
      li.addEventListener("mousedown", function (event) {
        event.preventDefault();
        window.location.href = href;
      });
      list.appendChild(li);
      items.push(li);
    }

    function render(data, query) {
      list.textContent = "";
      items = [];
      active = -1;
      if (!data.books.length && !data.authors.length) {
        list.appendChild(el("li", "suggest__empty", "No matches yet — press Enter to search everything."));
        open();
        return;
      }
      if (data.books.length) {
        list.appendChild(el("li", "suggest__group", "Books"));
        data.books.forEach(function (book) {
          addOption(book.url, function (li) {
            var spine = el("span", "suggest__spine");
            spine.classList.add("cover--tone-" + book.tone);
            li.appendChild(spine);
            var text = el("span", "grow");
            text.appendChild(el("div", "suggest__title", book.title));
            if (book.authors) text.appendChild(el("div", "suggest__meta", book.authors));
            li.appendChild(text);
            li.appendChild(
              el("span", "suggest__status" + (book.available ? " suggest__status--available" : ""), book.status)
            );
          });
        });
      }
      if (data.authors.length) {
        list.appendChild(el("li", "suggest__group", "Authors"));
        data.authors.forEach(function (author) {
          addOption(author.url, function (li) {
            li.appendChild(el("span", "suggest__title", author.name));
          });
        });
      }
      open();
    }

    var fetchSuggestions = debounce(function (query) {
      if (controller) controller.abort();
      controller = "AbortController" in window ? new AbortController() : null;
      fetch(url + "?q=" + encodeURIComponent(query), {
        credentials: "same-origin",
        signal: controller ? controller.signal : undefined,
        headers: { Accept: "application/json" },
      })
        .then(function (r) { return r.ok ? r.json() : { books: [], authors: [] }; })
        .then(function (data) {
          if (input.value.trim() === query) render(data, query);
        })
        .catch(function () {});
    }, 140);

    input.addEventListener("input", function () {
      var query = input.value.trim();
      if (query === lastQuery) return;
      lastQuery = query;
      if (query.length < 2) {
        close();
        return;
      }
      list.textContent = "";
      list.appendChild(el("li", "suggest__empty", "Finding your next book…"));
      open();
      fetchSuggestions(query);
    });

    input.addEventListener("keydown", function (event) {
      if (list.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive(items.length ? (active + 1) % items.length : -1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive(items.length ? (active <= 0 ? items.length - 1 : active - 1) : -1);
      } else if (event.key === "Enter" && active >= 0 && items[active]) {
        event.preventDefault();
        window.location.href = items[active].dataset.href;
      } else if (event.key === "Escape") {
        close();
      }
    });

    input.addEventListener("blur", function () { setTimeout(close, 120); });
  });
})();
