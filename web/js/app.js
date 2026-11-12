// Entry point: tab switching and per-view initialization.
import { initRaceView } from "./race.js";
import { initEditorView } from "./editor.js";
import { initReplayView } from "./replay.js";

const tabs = document.querySelectorAll(".tab");
const views = {
  race: document.getElementById("view-race"),
  editor: document.getElementById("view-editor"),
  replay: document.getElementById("view-replay"),
};

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    Object.values(views).forEach((v) => v.classList.remove("active"));
    views[tab.dataset.view].classList.add("active");
  });
});

initRaceView().catch((e) => console.error("race view:", e));
initEditorView().catch((e) => console.error("editor view:", e));
initReplayView().catch((e) => console.error("replay view:", e));
