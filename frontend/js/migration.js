const input = document.querySelector("#backup");
const button = document.querySelector("#start");
const progress = document.querySelector("#progress");
const status = document.querySelector("#status");

input.addEventListener("change", () => {
  button.disabled = !input.files?.length;
  status.textContent = "";
  status.className = "";
});

button.addEventListener("click", () => {
  const file = input.files?.[0];
  const token = localStorage.getItem("sc.token") || "";
  if (!token) {
    status.textContent = "Log eerst in bij StudyGrasp en open deze pagina daarna opnieuw.";
    status.className = "error";
    return;
  }
  button.disabled = true;
  input.disabled = true;
  progress.hidden = false;
  progress.value = 0;
  status.textContent = "Back-up uploaden…";
  status.className = "";

  const form = new FormData();
  form.append("backup", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/owner/migration/import");
  xhr.setRequestHeader("Authorization", `Bearer ${token}`);
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      progress.value = Math.round((event.loaded / event.total) * 100);
      status.textContent = `Back-up uploaden… ${progress.value}%`;
    }
  };
  xhr.onload = () => {
    let payload = {};
    try { payload = JSON.parse(xhr.responseText || "{}"); } catch { /* afgehandeld hieronder */ }
    if (xhr.status >= 200 && xhr.status < 300 && payload.ok) {
      progress.value = 100;
      status.textContent = `${payload.documents} colleges zijn geïmporteerd. Je kunt terug naar StudyGrasp.`;
      status.className = "success";
      const home = document.createElement("a");
      home.className = "button";
      home.href = "/";
      home.textContent = "Open mijn colleges";
      status.after(home);
    } else {
      status.textContent = payload.message || "De migratie is niet gelukt. Probeer het niet opnieuw voordat de fout is gecontroleerd.";
      status.className = "error";
      button.disabled = false;
      input.disabled = false;
    }
  };
  xhr.onerror = () => {
    status.textContent = "De verbinding viel weg tijdens de migratie. Controleer Railway voordat je opnieuw probeert.";
    status.className = "error";
    button.disabled = false;
    input.disabled = false;
  };
  xhr.send(form);
});
