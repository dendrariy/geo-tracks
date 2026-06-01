/* geo-tracks — клиентское приложение (vanilla JS + Leaflet) */
(() => {
  "use strict";
  const PAGE = 60;                 // сколько треков показывать в списке за раз
  let INDEX = [], META = null, GEOM = {};
  let byId = {};
  const layers = {};               // id -> L.Polyline
  let activeId = null;
  let shown = PAGE;

  const $ = (s) => document.querySelector(s);

  // ---------- цвет по длине маршрута ----------
  // Градация: <5 зелёный, 5–10 жёлтый, 10–20 оранжевый, 20–30 красный, 30+ фиолетовый
  const LENGTH_BANDS = [
    { max: 5,        color: "#16a34a", label: "до 5 км" },
    { max: 10,       color: "#eab308", label: "5–10 км" },
    { max: 20,       color: "#f97316", label: "10–20 км" },
    { max: 30,       color: "#dc2626", label: "20–30 км" },
    { max: Infinity, color: "#9333ea", label: "30 км и больше" },
  ];
  const colorForLength = (km) =>
    (LENGTH_BANDS.find(b => km < b.max) || LENGTH_BANDS[LENGTH_BANDS.length - 1]).color;

  // ---------- карта и слои ----------
  const map = L.map("map", { zoomControl: true, preferCanvas: true });
  const renderer = L.canvas({ padding: 0.5 });

  const base = {
    "Esri — улицы (англ.)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, attribution: "© Esri, HERE, Garmin, © OpenStreetMap contributors" }),
    "OpenTopoMap": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
      maxZoom: 17, attribution: "© OpenTopoMap, © OpenStreetMap" }),
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap" }),
    "CyclOSM": L.tileLayer("https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "CyclOSM, © OpenStreetMap" }),
    "OSM Humanitarian": L.tileLayer("https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png", {
      maxZoom: 19, subdomains: "abc", attribution: "HOT, © OpenStreetMap" }),
    "Esri — спутник": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, attribution: "© Esri, Maxar, Earthstar Geographics" }),
    "Esri — топо": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", {
      maxZoom: 19, attribution: "© Esri" }),
  };
  const overlays = {
    "Тропы (Waymarked)": L.tileLayer("https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© waymarkedtrails.org" }),
  };
  base["Esri — улицы (англ.)"].addTo(map);
  L.control.layers(base, overlays, { position: "topright", collapsed: false }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  // ---------- загрузка данных ----------
  async function load() {
    const [idx, meta, geom] = await Promise.all([
      fetch("data/index.json").then(r => r.json()),
      fetch("data/meta.json").then(r => r.json()),
      fetch("data/geometry.json").then(r => r.json()),
    ]);
    INDEX = idx; META = meta; GEOM = geom;
    INDEX.forEach(t => byId[t.id] = t);

    const e = META.view || META.extent;
    map.fitBounds([[e[0], e[1]], [e[2], e[3]]]);

    buildTracks();
    buildLengthControl();
    buildLegend();
    map.on("moveend", () => { if ($("#vp").checked) applyFilters(); });
    applyFilters();
    $("#loader").style.display = "none";
  }

  // ---------- полилинии ----------
  function buildTracks() {
    INDEX.forEach(t => {
      const pts = GEOM[t.id];
      if (!pts) return;
      const pl = L.polyline(pts, {
        renderer, color: colorForLength(t.lengthKm), weight: 2.5, opacity: 0.82, smoothFactor: 1.2,
      });
      pl.bindTooltip(t.name, { className: "track-tip", sticky: true });
      pl.on("mouseover", () => pl.setStyle({ weight: 5, opacity: 1 }).bringToFront());
      pl.on("mouseout", () => { if (t.id !== activeId) pl.setStyle({ weight: 2.5, opacity: 0.82 }); });
      pl.on("click", () => selectTrack(t.id, false));
      layers[t.id] = pl;
    });
  }

  function popupHtml(t) {
    const gain = t.gainM != null ? `<div class="popup-row"><b>Набор высоты:</b> ${t.gainM} м</div>` : "";
    const src = t.url ? `<div class="popup-row"><b>Источник:</b> <a href="${t.url}" target="_blank" rel="noopener">${t.source}</a></div>` : "";
    return `<span class="popup-title">${t.location}</span>
      <div class="popup-row"><b>Длина:</b> ${t.lengthKm} км</div>${gain}${src}
      <div class="popup-row" style="margin-top:5px;opacity:.7">${t.name}</div>`;
  }

  function selectTrack(id, fly) {
    if (activeId && layers[activeId]) layers[activeId].setStyle({ weight: 2.5, opacity: 0.82 });
    activeId = id;
    const t = byId[id], pl = layers[id];
    if (!pl) return;
    pl.setStyle({ weight: 5, opacity: 1 }).bringToFront();
    pl.bindPopup(popupHtml(t), { maxWidth: 280 });
    if (fly) map.fitBounds([[t.bbox[0], t.bbox[1]], [t.bbox[2], t.bbox[3]]], { padding: [40, 40], maxZoom: 15 });
    pl.openPopup();
    document.querySelectorAll(".item").forEach(el => el.classList.toggle("active", el.dataset.id === id));
  }

  // ---------- фильтры ----------
  function currentFilters() {
    const q = $("#q").value.trim().toLowerCase();
    const sMax = META.sliderMax;
    const min = parseFloat($("#lmin").value) || 0;
    let max = parseFloat($("#lmax").value);
    if (isNaN(max)) max = Infinity;
    // верхний бегунок на максимуме = "и длиннее"
    if (parseFloat($("#rmax").value) >= sMax && $("#lmax").value == sMax) max = Infinity;
    const vp = $("#vp").checked;
    const bounds = vp ? map.getBounds() : null;
    return { q, min, max, vp, bounds };
  }

  function matches(t, f) {
    if (t.lengthKm < f.min || t.lengthKm > f.max) return false;
    if (f.q && !t.name.toLowerCase().includes(f.q) && !t.location.toLowerCase().includes(f.q)) return false;
    if (f.vp && f.bounds) {
      const b = t.bbox; // пересечение bbox трека с областью карты
      const tb = L.latLngBounds([b[0], b[1]], [b[2], b[3]]);
      if (!f.bounds.intersects(tb)) return false;
    }
    return true;
  }

  function applyFilters() {
    const f = currentFilters();
    const visible = [];
    INDEX.forEach(t => {
      const ok = matches(t, f);
      const pl = layers[t.id];
      if (ok) {
        if (pl && !map.hasLayer(pl)) pl.addTo(map);
        visible.push(t);
      } else if (pl && map.hasLayer(pl)) {
        map.removeLayer(pl);
      }
    });
    renderList(visible);
    $("#cnt").textContent = visible.length;
  }

  // ---------- список ----------
  function renderList(visible) {
    const list = $("#list");
    if (!visible.length) {
      list.innerHTML = `<div class="empty">Ничего не найдено.<br>Измените запрос или фильтры.</div>`;
      return;
    }
    const slice = visible.slice(0, shown);
    list.innerHTML = slice.map(t => `
      <div class="item${t.id === activeId ? " active" : ""}" data-id="${t.id}">
        <span class="swatch" style="background:${colorForLength(t.lengthKm)}"></span>
        <div>
          <div class="nm">${t.location} <span style="color:var(--ink-soft);font-weight:400">· ${(+t.lengthKm).toFixed(1)} км</span></div>
          <div class="sub">
            ${t.url ? `<a class="src" href="${t.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${t.source} ↗</a>` : ""}
          </div>
        </div>
      </div>`).join("") +
      (visible.length > shown
        ? `<div class="more"><button id="more">Показать ещё (${visible.length - shown})</button></div>` : "");

    list.querySelectorAll(".item").forEach(el => {
      el.addEventListener("click", () => selectTrack(el.dataset.id, true));
      el.addEventListener("mouseenter", () => { const pl = layers[el.dataset.id]; if (pl && map.hasLayer(pl)) pl.setStyle({ weight: 4.5, opacity: 1 }).bringToFront(); });
      el.addEventListener("mouseleave", () => { const pl = layers[el.dataset.id]; if (pl && el.dataset.id !== activeId) pl.setStyle({ weight: 2.5, opacity: 0.82 }); });
    });
    const m = $("#more");
    if (m) m.addEventListener("click", () => { shown += PAGE; renderList(visible); });
  }

  // ---------- контрол длины ----------
  function buildLengthControl() {
    const maxL = META.sliderMax;
    const lmin = $("#lmin"), lmax = $("#lmax"), rmin = $("#rmin"), rmax = $("#rmax");
    rmin.min = 0; rmin.max = maxL; rmin.value = 0;
    rmax.min = 0; rmax.max = maxL; rmax.value = maxL;
    lmin.value = 0; lmax.value = maxL;
    $("#lenhint").textContent = `${maxL} км = «и длиннее» (до ${Math.round(META.maxLengthKm)})`;

    function paintFill() {
      const a = (+rmin.value / maxL) * 100, b = (+rmax.value / maxL) * 100;
      $("#fill").style.left = a + "%"; $("#fill").style.width = (b - a) + "%";
    }
    function sync(from) {
      let a, b;
      if (from === "num") {
        a = Math.min(+lmin.value || 0, +lmax.value || maxL);
        b = Math.max(+lmin.value || 0, +lmax.value || maxL);
        rmin.value = Math.min(a, maxL); rmax.value = Math.min(b, maxL);
      } else {
        a = Math.min(+rmin.value, +rmax.value); b = Math.max(+rmin.value, +rmax.value);
        lmin.value = a; lmax.value = b;
      }
      paintFill(); shown = PAGE; applyFilters();
    }
    rmin.addEventListener("input", () => sync("range"));
    rmax.addEventListener("input", () => sync("range"));
    lmin.addEventListener("change", () => sync("num"));
    lmax.addEventListener("change", () => sync("num"));
    paintFill();
  }

  // ---------- легенда (по длине маршрута) ----------
  function buildLegend() {
    const body = $("#legbody");
    body.innerHTML = LENGTH_BANDS.map(b =>
      `<div class="leg-item">
        <span class="swatch" style="background:${b.color}"></span>
        <span>${b.label}</span>
        <span class="c">${INDEX.filter(t => colorForLength(t.lengthKm) === b.color).length}</span>
      </div>`).join("");
  }

  // ---------- поиск + reset + бургер ----------
  let qTimer;
  $("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(() => { shown = PAGE; applyFilters(); }, 160); });
  $("#vp").addEventListener("change", () => { shown = PAGE; applyFilters(); });
  $("#reset").addEventListener("click", () => {
    $("#q").value = ""; $("#vp").checked = false;
    const maxL = META.sliderMax;
    $("#lmin").value = 0; $("#lmax").value = maxL; $("#rmin").value = 0; $("#rmax").value = maxL;
    $("#fill").style.left = "0%"; $("#fill").style.width = "100%";
    shown = PAGE;
    const e = META.view || META.extent;
    map.fitBounds([[e[0], e[1]], [e[2], e[3]]]);
    applyFilters();
  });
  $("#burger").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

  load().catch(err => {
    $("#loader").innerHTML = `<p style="color:var(--accent)">Не удалось загрузить данные.<br>${err}</p>`;
    console.error(err);
  });
})();
