// Pages after the final survey (completion, results).

// Copy buttons: data-copy-target names the element whose text or value is copied.
for (const button of document.querySelectorAll("[data-copy-target]")) {
  const label = button.textContent;
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copyTarget);
    const text = ("value" in source ? source.value : source.textContent).trim();
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      // Older browsers: select the text and use the legacy copy command.
      if ("select" in source) {
        source.select();
      } else {
        const range = document.createRange();
        range.selectNodeContents(source);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
      }
      document.execCommand("copy");
    }
    button.textContent = "Copied ✓";
    setTimeout(() => (button.textContent = label), 2000);
  });
}

// Results: the answer list cannot be copied, cut, dragged or right-clicked.
const resultsList = document.getElementById("results-list");
if (resultsList) {
  for (const type of ["copy", "cut", "contextmenu", "dragstart"]) {
    resultsList.addEventListener(type, (event) => event.preventDefault());
  }

  const items = [...resultsList.querySelectorAll("details")];
  const toggle = document.getElementById("toggle-all");
  const sync = () => {
    const allOpen = items.every((item) => item.open);
    toggle.textContent = allOpen ? "Collapse all" : "Expand all";
    toggle.setAttribute("aria-expanded", String(allOpen));
  };
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    const open = !items.every((item) => item.open);
    items.forEach((item) => (item.open = open));
    sync();
  });
  items.forEach((item) => item.addEventListener("toggle", sync));
  sync();

  // The numbered squares in the score card open the question they point to.
  for (const link of document.querySelectorAll(".score-strip a")) {
    link.addEventListener("click", () => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target) target.open = true;
    });
  }
}
