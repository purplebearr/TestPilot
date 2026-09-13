// Mobile nav toggle
document.addEventListener("click", (e) => {
  const toggle = e.target.closest("[data-nav-toggle]");
  if (toggle) {
    document.querySelector(".nav-mobile")?.classList.toggle("open");
  }
});

// Close mobile nav after navigating
document.addEventListener("htmx:afterSettle", () => {
  document.querySelector(".nav-mobile")?.classList.remove("open");
});

// Show/clear inline form messages when an htmx form response comes back.
// The view is expected to return a small HTML fragment (or 4xx with an
// error fragment) targeting #form-message.
document.body.addEventListener("htmx:responseError", (evt) => {
  const target = evt.target.querySelector?.("#form-message") || document.getElementById("form-message");
  if (target) {
    target.textContent = "Something went wrong. Please try again.";
    target.classList.add("error");
  }
});