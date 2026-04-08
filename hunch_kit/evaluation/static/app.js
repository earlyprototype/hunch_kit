document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("scoring-form");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const experimentId = form.dataset.experimentId;
    const status = document.getElementById("save-status");

    const scores = {};
    form.querySelectorAll("input[type='range'], input[type='number']").forEach((el) => {
      if (el.name) scores[el.name] = el.value;
    });

    const preference = form.querySelector("#overall_preference")?.value || "";
    const notes = form.querySelector("#notes")?.value || "";

    status.textContent = "Saving...";
    status.style.color = "";

    try {
      const res = await fetch(`/eval/${experimentId}/score`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scores,
          overall_preference: preference,
          notes,
        }),
      });

      if (res.ok) {
        status.textContent = "Saved";
        status.style.color = "#16a34a";
      } else {
        const data = await res.json();
        status.textContent = `Error: ${data.error || res.statusText}`;
        status.style.color = "#dc2626";
      }
    } catch (err) {
      status.textContent = `Error: ${err.message}`;
      status.style.color = "#dc2626";
    }

    setTimeout(() => { status.textContent = ""; }, 3000);
  });
});
