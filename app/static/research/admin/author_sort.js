(function () {
  "use strict";

  function rows(container) {
    return Array.from(container.querySelectorAll("tr.form-row:not(.empty-form)"))
      .filter((row) => !row.classList.contains("deleted")
        && !row.querySelector('input[name$="-DELETE"]:checked'))
      .filter((row) => {
        const id = row.querySelector('input[name$="-id"]');
        const user = row.querySelector('[name$="-user"]');
        return Boolean((id && id.value) || (user && user.value));
      });
  }

  function renumber(container) {
    rows(container).forEach((row, index) => {
      const position = row.querySelector('input[name$="-position"]');
      if (position) position.value = String(index + 1);
      row.querySelectorAll(".author-move").forEach((button) => {
        button.disabled = button.dataset.direction === "up"
          ? index === 0
          : index === rows(container).length - 1;
      });
    });
  }

  document.addEventListener("click", (event) => {
    const savedButton = event.target.closest(".author-move-saved");
    if (savedButton) {
      const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
      if (!csrf) return;
      savedButton.disabled = true;
      fetch(savedButton.dataset.url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf.value },
      }).then((response) => {
        if (!response.ok) throw new Error("排序失敗，請重新整理後再試。");
        window.location.reload();
      }).catch((error) => {
        savedButton.disabled = false;
        window.alert(error.message);
      });
      return;
    }
    const button = event.target.closest(".author-move");
    if (!button) return;
    const row = button.closest("tr.form-row");
    const container = row && row.closest(".inline-group");
    if (!container) return;
    const current = rows(container);
    const index = current.indexOf(row);
    const other = current[index + (button.dataset.direction === "up" ? -1 : 1)];
    if (!other) return;
    if (button.dataset.direction === "up") {
      other.before(row);
    } else {
      other.after(row);
    }
    renumber(container);
  });

  document.addEventListener("change", (event) => {
    if (!event.target.matches('[name$="-user"], input[name$="-DELETE"]')) return;
    const container = event.target.closest(".inline-group");
    if (container && container.querySelector(".author-move")) renumber(container);
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".inline-group").forEach((container) => {
      if (!container.querySelector(".author-move")) return;
      renumber(container);
      const observer = new MutationObserver(() => renumber(container));
      observer.observe(container, { childList: true, subtree: true });
    });
  });
})();
