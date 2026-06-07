"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
    cwd: "",
    selected: null,
    jobId: null,
    ws: null,
};

const fmtBytes = (n) => {
    if (n < 1024) return `${n} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
};

const fmtDuration = (secs) => {
    if (secs == null) return "";
    const s = Math.round(secs);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${m}m` : `${m} min`;
};

const sliderToSpeed = (v) => (Number(v) / 20).toFixed(2);

// ---- file browser ----
async function loadFolder(path) {
    state.cwd = path;
    const list = $("#listing");
    list.innerHTML = '<div class="placeholder">Loading…</div>';
    try {
        const r = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        renderCrumbs(data.crumbs);
        renderListing(data.folders, data.files);
    } catch (e) {
        list.innerHTML = `<div class="placeholder">Couldn't read: ${e.message}</div>`;
    }
}

function renderCrumbs(crumbs) {
    const el = $("#crumbs");
    el.innerHTML = "";
    crumbs.forEach((c, i) => {
        if (i > 0) {
            const sep = document.createElement("span");
            sep.className = "sep";
            sep.textContent = "›";
            el.appendChild(sep);
        }
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = c.name;
        b.onclick = () => loadFolder(c.path);
        el.appendChild(b);
    });
}

function renderListing(folders, files) {
    const el = $("#listing");
    el.innerHTML = "";
    if (!folders.length && !files.length) {
        el.innerHTML = '<div class="empty">…this scroll holds no tomes…</div>';
        return;
    }
    folders.forEach((f) => {
        const row = document.createElement("div");
        row.className = "row folder";
        row.innerHTML =
            `<span class="icon">📁</span>
             <span class="name"></span>`;
        row.querySelector(".name").textContent = f.name;
        row.onclick = () => loadFolder(f.path);
        el.appendChild(row);
    });
    files.forEach((f) => {
        const row = document.createElement("div");
        row.className = "row file";
        row.innerHTML =
            `<span class="icon">📖</span>
             <span class="name"></span>
             <span class="meta"></span>`;
        row.querySelector(".name").textContent = f.name;
        row.querySelector(".meta").textContent = fmtBytes(f.size);
        row.onclick = () => selectFile(f.path, row);
        el.appendChild(row);
    });
}

async function selectFile(path, row) {
    document.querySelectorAll(".listing .row.selected").forEach((r) =>
        r.classList.remove("selected"));
    row.classList.add("selected");
    const sel = $("#selected");
    sel.classList.add("has-file");
    sel.textContent = `📖 ${path}`;
    state.selected = { path };
    $("#quicken").disabled = false;

    try {
        const r = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
        if (!r.ok) return;
        const info = await r.json();
        state.selected = info;
        const meta = [];
        if (info.duration) meta.push(fmtDuration(info.duration));
        meta.push(fmtBytes(info.size));
        if (info.bitrate) meta.push(`${Math.round(info.bitrate / 1000)} kbps`);
        sel.innerHTML = "";
        const name = document.createElement("span");
        name.textContent = `📖 ${info.name}`;
        sel.appendChild(name);
        const metaEl = document.createElement("span");
        metaEl.className = "meta";
        metaEl.textContent = ` — ${meta.join(", ")}`;
        sel.appendChild(metaEl);
    } catch {
        // ignore
    }
}

// ---- speed ----
const slider = $("#speed");
const readout = $("#speed-readout");
function updateReadout() {
    readout.textContent = `${sliderToSpeed(slider.value)}×`;
}
slider.addEventListener("input", updateReadout);
$("#presets").addEventListener("click", (e) => {
    const v = e.target.dataset.v;
    if (!v) return;
    slider.value = v;
    updateReadout();
});

// ---- convert flow ----
$("#quicken").addEventListener("click", () => {
    if (!state.selected) return;
    $("#confirm-path").textContent = state.selected.path;
    $("#confirm-speed").textContent = `${sliderToSpeed(slider.value)}×`;
    $("#confirm").classList.remove("hidden");
});
$("#confirm-cancel").addEventListener("click", () =>
    $("#confirm").classList.add("hidden"));
$("#confirm-ok").addEventListener("click", () => {
    $("#confirm").classList.add("hidden");
    startConvert();
});

async function startConvert() {
    const speed = Number(sliderToSpeed(slider.value));
    setRunning(true);
    setStatus("Summoning the encoder…");
    try {
        const r = await fetch("/api/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: state.selected.path, speed }),
        });
        if (!r.ok) throw new Error(await r.text());
        const { job_id } = await r.json();
        state.jobId = job_id;
        openSocket(job_id);
    } catch (e) {
        setStatus(`Spell fizzled: ${e.message}`, "error");
        setRunning(false);
    }
}

function openSocket(jobId) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);
    state.ws = ws;
    ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "progress") {
            setProgress(msg.pct);
        } else if (msg.type === "status") {
            setStatus(msg.msg);
        } else if (msg.type === "done") {
            setProgress(100);
            setStatus(`✦ Quickened: ${msg.path} ✦`, "done");
            setRunning(false);
        } else if (msg.type === "error") {
            setStatus(`Spell fizzled — ${msg.msg.split("\n")[0]}`, "error");
            setRunning(false);
        }
    };
    ws.onerror = () => setStatus("Lost the scrying link", "error");
    ws.onclose = () => { state.ws = null; };
}

$("#cancel").addEventListener("click", async () => {
    if (!state.jobId) return;
    await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    setStatus("Abjuring…");
});

// ---- UI helpers ----
function setProgress(pct) {
    $("#progress-fill").style.width = `${pct}%`;
    $("#progress-label").textContent = `${pct.toFixed(0)}%`;
}
function setStatus(msg, cls = "") {
    const el = $("#status");
    el.textContent = msg;
    el.className = `status ${cls}`;
}
function setRunning(on) {
    slider.disabled = on;
    $("#quicken").disabled = on || !state.selected;
    $("#cancel").disabled = !on;
    document.querySelectorAll("#presets button").forEach((b) => b.disabled = on);
    if (!on) {
        state.jobId = null;
        if (state.ws) try { state.ws.close(); } catch {}
    } else {
        setProgress(0);
        $("#progress-label").textContent = "0%";
    }
}

// ---- boot ----
updateReadout();
loadFolder("");
