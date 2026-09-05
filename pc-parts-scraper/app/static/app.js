(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const els = {
    hero: $("#hero"), form: $("#search-form"), q: $("#q"), clear: $("#clear-btn"),
    categories: $("#categories"), results: $("#results"), summaryCount: $("#summary-count"),
    summaryMeta: $("#summary-meta"), refresh: $("#refresh-btn"), sort: $("#sort"),
    minPrice: $("#min-price"), maxPrice: $("#max-price"), inStock: $("#in-stock"),
    retailerList: $("#retailer-list"), retailerCount: $("#retailer-count"), skeleton: $("#skeleton"),
    empty: $("#empty"), list: $("#list"), more: $("#more-btn"), error: $("#error"),
    rate: $("#rate-remaining"), theme: $("#theme-toggle"), rowTpl: $("#row-tpl"), copy: $("#copy-btn"),
    affiliate: $("#affiliate-note"),
    dialog: $("#history-dialog"), hTitle: $("#history-title"), hSub: $("#history-sub"), hStats: $("#history-stats"),
    hChart: $("#history-chart"), hTip: $("#history-tooltip"), hTable: $("#history-table tbody"), hClose: $("#history-close"),
  };

  const PAGE_SIZE = 40;
  const RETAILER_COLOURS = {
    scan: "#e4002b", overclockers: "#ff6a00", ebuyer: "#0057b8", ccl: "#00a651",
    novatech: "#7b2cbf", awd_it: "#111827", box: "#1d4ed8", currys: "#5a2ca0",
    newegg: "#f29a1f", amazon: "#ff9900", nvidia: "#76b900",
  };
  const gbp = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" });

  const state = {
    retailers: [],           // [{id, name, homepage, enabled}]
    selected: new Set(),      // retailer ids currently ticked
    data: null,               // last SearchResponse
    shown: PAGE_SIZE,
    controller: null,
    lastQuery: "",
  };

  // ---------- theme ----------
  const applyTheme = (t) => { document.documentElement.dataset.theme = t; };
  try {
    const saved = localStorage.getItem("theme");
    applyTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  } catch { applyTheme("light"); }
  els.theme.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("theme", next); } catch {}
  });

  // ---------- helpers ----------
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const timeAgo = (ts) => {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  };
  const stateClass = (st) => ({ ok: "ok", cached: "cached", stale: "warn", empty: "warn", timeout: "bad", blocked: "bad", error: "bad", disabled: "" }[st] || "");
  const stateLabel = (r) => {
    switch (r.state) {
      case "ok": return `live · ${r.ms} ms`;
      case "cached": return `cached ${timeAgo(r.fetched_at)}`;
      case "stale": return `older results (${r.message || "refresh failed"})`;
      case "empty": return "no matches";
      case "timeout": return "timed out";
      case "blocked": return "blocked by bot protection";
      case "error": return r.message || "error";
      default: return r.state;
    }
  };
  const showError = (msg) => { els.error.textContent = msg; els.error.hidden = !msg; };

  // ---------- URL state ----------
  const readUrl = () => {
    const p = new URLSearchParams(location.search);
    return { q: (p.get("q") || "").trim(), retailers: (p.get("r") || "").split(",").filter(Boolean), sort: p.get("sort") || "price" };
  };
  const writeUrl = (q, replace = false) => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    const all = state.retailers.filter((r) => r.enabled).map((r) => r.id);
    if (state.selected.size && state.selected.size !== all.length) p.set("r", [...state.selected].join(","));
    if (els.sort.value !== "price") p.set("sort", els.sort.value);
    const url = `${location.pathname}${p.toString() ? "?" + p : ""}`;
    history[replace ? "replaceState" : "pushState"](null, "", url);
  };

  // ---------- retailers sidebar ----------
  const renderRetailers = () => {
    const statuses = new Map((state.data?.retailers || []).map((r) => [r.id, r]));
    els.retailerList.replaceChildren(...state.retailers.filter((r) => r.enabled).map((r) => {
      const st = statuses.get(r.id);
      const li = document.createElement("li");
      const label = document.createElement("label");
      const cb = Object.assign(document.createElement("input"), { type: "checkbox", checked: state.selected.has(r.id) });
      cb.addEventListener("change", () => {
        cb.checked ? state.selected.add(r.id) : state.selected.delete(r.id);
        writeUrl(state.lastQuery, true);
        renderList();
      });
      const dot = document.createElement("span");
      dot.className = `dot ${st ? stateClass(st.state) : ""}`;
      dot.title = st ? stateLabel(st) : "";
      const name = Object.assign(document.createElement("span"), { className: "name", textContent: r.name });
      const count = Object.assign(document.createElement("span"), { className: "count", textContent: st ? String(st.count) : "" });
      label.append(cb, dot, name, count);
      label.title = st ? `${r.name}: ${stateLabel(st)}` : r.name;
      li.append(label);
      return li;
    }));
  };

  // ---------- results list ----------
  const visibleListings = () => {
    if (!state.data) return [];
    const min = parseFloat(els.minPrice.value), max = parseFloat(els.maxPrice.value);
    const inStockOnly = els.inStock.checked;
    let items = state.data.listings.filter((l) =>
      state.selected.has(l.retailer) &&
      (Number.isNaN(min) || l.price >= min) &&
      (Number.isNaN(max) || l.price <= max) &&
      (!inStockOnly || l.in_stock !== false));
    const sorters = {
      "price": (a, b) => a.price - b.price || b.relevance - a.relevance,
      "price-desc": (a, b) => b.price - a.price || b.relevance - a.relevance,
      "relevance": (a, b) => b.relevance - a.relevance || a.price - b.price,
      "retailer": (a, b) => a.retailer_name.localeCompare(b.retailer_name) || a.price - b.price,
    };
    return items.sort(sorters[els.sort.value] || sorters.price);
  };

  const buildRow = (l, cheapest) => {
    const frag = els.rowTpl.content.cloneNode(true);
    const row = $(".row", frag);
    row.style.setProperty("--rc", RETAILER_COLOURS[l.retailer] || "var(--accent)");
    if (cheapest) row.classList.add("cheapest");
    const thumb = $(".thumb", frag), img = $("img", thumb);
    thumb.href = l.url;
    if (l.image) { img.src = l.image; img.alt = ""; img.addEventListener("error", () => { img.remove(); thumb.classList.add("empty"); thumb.textContent = "No image"; }); }
    else { img.remove(); thumb.classList.add("empty"); thumb.textContent = "No image"; }
    const title = $(".title", frag); title.href = l.url; title.textContent = l.title; title.title = l.title;
    $(".retailer", frag).textContent = l.seller ? `${l.retailer_name} · sold by ${l.seller}` : l.retailer_name;
    const stock = $(".stock", frag);
    if (l.in_stock === true) { stock.textContent = "In stock"; stock.classList.add("in"); }
    else if (l.in_stock === false) { stock.textContent = "Out of stock"; stock.classList.add("out"); }
    else stock.textContent = "Stock unknown";
    $(".match", frag).textContent = l.relevance < 1 ? `${Math.round(l.relevance * 100)}% match` : "";
    $(".price", frag).textContent = gbp.format(l.price);
    const was = $(".was", frag);
    if (l.previous_price && Math.abs(l.previous_price - l.price) >= 0.01) {
      const dir = l.price < l.previous_price ? "down" : "up";
      const diff = Math.abs(l.previous_price - l.price);
      was.innerHTML = `<span class="${dir}">${dir === "down" ? "▼" : "▲"} ${gbp.format(diff)}</span> was ${gbp.format(l.previous_price)}`;
    } else if (cheapest) {
      was.innerHTML = `<span class="badge-cheapest">Cheapest</span>`;
    } else if (l.lowest_price && l.lowest_price < l.price - 0.005) {
      was.textContent = `lowest seen ${gbp.format(l.lowest_price)}`;
    }
    $(".view", frag).href = l.url;
    $(".history-btn", frag).addEventListener("click", () => openHistory(l));
    return frag;
  };

  const renderList = () => {
    const items = visibleListings();
    const slice = items.slice(0, state.shown);
    const frag = document.createDocumentFragment();
    slice.forEach((l, i) => frag.append(buildRow(l, i === 0 && els.sort.value === "price")));
    els.list.replaceChildren(frag);
    els.more.hidden = items.length <= state.shown;
    els.more.textContent = `Show more (${Math.max(0, items.length - state.shown)} remaining)`;
    const total = state.data?.listings.length || 0;
    els.empty.hidden = items.length > 0;
    if (items.length === 0) {
      const bad = (state.data?.retailers || []).filter((r) => ["blocked", "timeout", "error"].includes(r.state));
      els.empty.textContent = total === 0
        ? (bad.length ? `No results. ${bad.length} retailer${bad.length > 1 ? "s" : ""} couldn't be reached (${bad.map((r) => r.name).join(", ")}).` : "No results for that search. Try a shorter or more specific term.")
        : "Nothing matches the current filters.";
    }
    const n = items.length;
    els.summaryCount.textContent = `${n} result${n === 1 ? "" : "s"}`;
    const okRetailers = (state.data?.retailers || []).filter((r) => r.count > 0).length;
    const newest = Math.max(0, ...(state.data?.retailers || []).map((r) => r.fetched_at || 0));
    els.summaryMeta.textContent = `for “${state.data?.query || ""}” from ${okRetailers} retailer${okRetailers === 1 ? "" : "s"} · updated ${newest ? timeAgo(newest) : "now"}`;
  };

  // ---------- searching ----------
  const setLoading = (on) => {
    els.skeleton.hidden = !on;
    if (on) els.skeleton.replaceChildren(...Array.from({ length: 6 }, () => Object.assign(document.createElement("div"), { className: "sk" })));
    els.refresh.disabled = on;
    els.refresh.textContent = on ? "Searching…" : "Refresh";
    els.form.classList.toggle("busy", on);
  };

  async function runSearch(query, { refresh = false, push = true } = {}) {
    query = (query || "").trim();
    if (!query) return;
    state.lastQuery = query;
    els.q.value = query;
    els.clear.hidden = !query;
    els.hero.classList.add("compact");
    els.results.hidden = false;
    showError("");
    if (push) writeUrl(query);
    document.title = `${query} · Shep's Parts`;

    if (state.controller) state.controller.abort();
    const controller = new AbortController();
    state.controller = controller;
    setLoading(true);
    els.list.replaceChildren();
    els.empty.hidden = true;
    els.more.hidden = true;
    els.summaryCount.textContent = "Searching…";
    els.summaryMeta.textContent = `checking ${state.selected.size} retailers`;

    const params = new URLSearchParams({ q: query });
    const all = state.retailers.filter((r) => r.enabled).map((r) => r.id);
    if (state.selected.size && state.selected.size !== all.length) params.set("retailers", [...state.selected].join(","));
    if (refresh) params.set("refresh", "1");
    try {
      const res = await fetch(`/api/search?${params}`, { signal: controller.signal });
      const remaining = res.headers.get("X-RateLimit-Remaining");
      if (remaining !== null) els.rate.textContent = `${remaining} searches left this minute`;
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Search failed (${res.status})`);
      }
      state.data = await res.json();
      state.shown = PAGE_SIZE;
      renderRetailers();
      renderList();
    } catch (err) {
      if (err.name === "AbortError") return;
      showError(err.message || "Something went wrong.");
      els.summaryCount.textContent = "Search failed";
      els.summaryMeta.textContent = "";
    } finally {
      if (state.controller === controller) { state.controller = null; setLoading(false); }
    }
  }

  // ---------- copy link ----------
  const copyLink = async () => {
    const url = location.href;
    let ok = false;
    try { await navigator.clipboard.writeText(url); ok = true; } catch {}
    if (!ok) {
      const tmp = Object.assign(document.createElement("input"), { value: url });
      document.body.append(tmp); tmp.select();
      try { ok = document.execCommand("copy"); } catch {}
      tmp.remove();
    }
    const label = $("span", els.copy);
    label.textContent = ok ? "Copied!" : "Copy failed";
    els.copy.classList.toggle("done", ok);
    setTimeout(() => { label.textContent = "Copy link"; els.copy.classList.remove("done"); }, 1800);
  };

  // ---------- price history ----------
  const fmtDate = (ts, withTime = false) => new Date(ts * 1000).toLocaleString("en-GB", withTime
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", year: "numeric" });
  const stat = (label, value, small = false) => `<div class="stat"><div class="label">${label}</div><div class="value${small ? " small" : ""}">${value}</div></div>`;

  const renderHistoryChart = (points) => {
    els.hChart.replaceChildren();
    els.hTip.hidden = true;
    if (!points.length) {
      els.hChart.innerHTML = `<div class="empty-msg">No price history yet. Prices are recorded every time this product is fetched.</div>`;
      return;
    }
    // A step series: each observation holds until the next; extend the last one to "now".
    const now = Date.now() / 1000;
    const series = [...points, { price: points[points.length - 1].price, seen_at: Math.max(now, points[points.length - 1].seen_at + 60) }];
    const W = 640, H = 220, padL = 56, padR = 72, padT = 16, padB = 28;
    const xs = series.map((p) => p.seen_at), ys = series.map((p) => p.price);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    // Snap the y range to round tick values so the axis reads £500, £520, £540 rather than £569.14.
    let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 1) { lo -= 5; hi += 5; } else { const pad = (hi - lo) * 0.15; lo -= pad; hi += pad; }
    lo = Math.max(0, lo);
    const ticks = 4;
    const rawStep = (hi - lo) / ticks;
    const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= rawStep) || 10 * mag;
    const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
    const sx = (t) => padL + ((t - x0) / Math.max(1, x1 - x0)) * (W - padL - padR);
    const sy = (v) => padT + (1 - (v - y0) / (y1 - y0)) * (H - padT - padB);

    const svgNS = "http://www.w3.org/2000/svg";
    const el = (tag, attrs = {}, text) => { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text != null) n.textContent = text; return n; };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Price over time" });

    // Recessive hairline grid with y labels.
    for (let v = y0; v <= y1 + step / 1000; v += step) {
      svg.append(el("line", { class: "grid", x1: padL, x2: W - padR, y1: sy(v), y2: sy(v) }));
      svg.append(el("text", { class: "axis-label", x: padL - 8, y: sy(v) + 4, "text-anchor": "end" }, gbp.format(v)));
    }
    // Two or three x labels.
    const xTicks = x1 - x0 > 0 ? [x0, (x0 + x1) / 2, x1] : [x0];
    xTicks.forEach((t, i) => svg.append(el("text", { class: "axis-label", x: sx(t), y: H - 8, "text-anchor": i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle" }, fmtDate(t))));

    // Step path + light area.
    let d = `M${sx(series[0].seen_at)},${sy(series[0].price)}`;
    for (let i = 1; i < series.length; i++) d += ` H${sx(series[i].seen_at)} V${sy(series[i].price)}`;
    const baseline = sy(y0);
    svg.append(el("path", { class: "area", d: `${d} V${baseline} H${sx(series[0].seen_at)} Z` }));
    svg.append(el("path", { class: "series", d }));
    points.forEach((p) => svg.append(el("circle", { class: "marker", cx: sx(p.seen_at), cy: sy(p.price), r: 4 })));
    // Direct label on the current value only.
    const last = series[series.length - 1];
    svg.append(el("text", { class: "end-label", x: sx(last.seen_at) + 8, y: sy(last.price) + 4 }, gbp.format(last.price)));

    // Hover: crosshair + tooltip showing the price in force at that time.
    const cursor = el("line", { class: "cursor", y1: padT, y2: H - padB, x1: 0, x2: 0 });
    const dot = el("circle", { class: "cursor-dot", r: 5 });
    svg.append(cursor, dot);
    const onMove = (evt) => {
      const rect = svg.getBoundingClientRect();
      const t = x0 + ((evt.clientX - rect.left) / rect.width * W - padL) / (W - padL - padR) * (x1 - x0);
      if (t < x0 || t > x1) return;
      let active = points[0];
      for (const p of points) if (p.seen_at <= t) active = p;
      cursor.setAttribute("x1", sx(t)); cursor.setAttribute("x2", sx(t)); cursor.style.opacity = 1;
      dot.setAttribute("cx", sx(t)); dot.setAttribute("cy", sy(active.price)); dot.style.opacity = 1;
      els.hTip.innerHTML = `<strong>${gbp.format(active.price)}</strong><br>${fmtDate(t, true)}`;
      els.hTip.hidden = false;
      const px = (sx(t) / W) * rect.width, py = (sy(active.price) / H) * rect.height;
      const flip = py < 56;  // near the top: show the tooltip below the point instead of over the stat tiles
      els.hTip.style.left = `${Math.min(Math.max(px, 70), rect.width - 70)}px`;
      els.hTip.style.top = `${flip ? py + 14 : py - 10}px`;
      els.hTip.style.transform = flip ? "translate(-50%, 0)" : "translate(-50%, -100%)";
    };
    svg.addEventListener("mousemove", onMove);
    svg.addEventListener("mouseleave", () => { cursor.style.opacity = 0; dot.style.opacity = 0; els.hTip.hidden = true; });
    els.hChart.append(svg, els.hTip);  // tooltip is positioned relative to the chart box
  };

  async function openHistory(l) {
    els.hTitle.textContent = l.title;
    els.hSub.textContent = l.seller ? `${l.retailer_name} · sold by ${l.seller}` : l.retailer_name;
    els.hStats.innerHTML = "";
    els.hChart.innerHTML = `<div class="empty-msg">Loading…</div>`;
    els.hTable.replaceChildren();
    if (!els.dialog.open) els.dialog.showModal();
    try {
      const res = await fetch(`/api/history?${new URLSearchParams({ retailer: l.retailer, url: l.url })}`);
      if (!res.ok) throw new Error(`History unavailable (${res.status})`);
      const h = await res.json();
      const pts = h.points || [];
      els.hStats.innerHTML =
        stat("Current", gbp.format(h.current ?? l.price)) +
        stat("Lowest seen", h.lowest != null ? gbp.format(h.lowest) : "–") +
        stat("Highest seen", h.highest != null ? gbp.format(h.highest) : "–") +
        stat("Tracking since", h.first_seen ? fmtDate(h.first_seen) : "–", true) +
        stat("Price changes", String(h.changes ?? 0));
      renderHistoryChart(pts);
      const rows = pts.map((p, i) => {
        const prev = i > 0 ? pts[i - 1].price : null;
        const diff = prev == null ? null : p.price - prev;
        const cls = diff == null ? "" : diff < 0 ? "down" : "up";
        const change = diff == null ? "first seen" : `${diff < 0 ? "▼" : "▲"} ${gbp.format(Math.abs(diff))}`;
        return `<tr><td>${fmtDate(p.seen_at, true)}</td><td>${gbp.format(p.price)}</td><td class="${cls}">${change}</td></tr>`;
      }).reverse();
      els.hTable.innerHTML = rows.join("");
    } catch (err) {
      els.hChart.innerHTML = `<div class="empty-msg">${err.message || "Could not load history."}</div>`;
    }
  }
  els.hClose.addEventListener("click", () => els.dialog.close());
  els.dialog.addEventListener("click", (e) => { if (e.target === els.dialog) els.dialog.close(); });

  // ---------- wiring ----------
  els.copy.addEventListener("click", copyLink);
  els.form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(els.q.value); });
  els.q.addEventListener("input", () => { els.clear.hidden = !els.q.value; });
  els.clear.addEventListener("click", () => { els.q.value = ""; els.clear.hidden = true; els.q.focus(); });
  els.refresh.addEventListener("click", () => runSearch(state.lastQuery, { refresh: true, push: false }));
  els.more.addEventListener("click", () => { state.shown += PAGE_SIZE; renderList(); });
  els.sort.addEventListener("change", () => { writeUrl(state.lastQuery, true); renderList(); });
  els.inStock.addEventListener("change", renderList);
  els.minPrice.addEventListener("input", debounce(renderList, 150));
  els.maxPrice.addEventListener("input", debounce(renderList, 150));
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== els.q && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) { e.preventDefault(); els.q.focus(); els.q.select(); }
    if (e.key === "Escape" && document.activeElement === els.q) els.q.blur();
  });
  window.addEventListener("popstate", () => { const u = readUrl(); if (u.q) runSearch(u.q, { push: false }); });
  setInterval(() => { if (state.data) { renderRetailers(); const m = els.summaryMeta.textContent; if (m) renderList(); } }, 60_000);

  // ---------- boot ----------
  (async () => {
    try {
      const [retailers, categories] = await Promise.all([
        fetch("/api/retailers").then((r) => r.json()),
        fetch("/api/categories").then((r) => r.json()),
      ]);
      state.retailers = retailers;
      els.retailerCount.textContent = String(retailers.filter((r) => r.enabled).length);
      // Only claim affiliate links when at least one shop actually comes from a feed.
      els.affiliate.hidden = !retailers.some((r) => r.source === "feed" && r.enabled);
      els.categories.replaceChildren(...categories.map((c) => {
        const b = Object.assign(document.createElement("button"), { type: "button", className: "chip", textContent: c.label });
        b.addEventListener("click", () => runSearch(c.query));
        return b;
      }));
      const u = readUrl();
      const enabledIds = retailers.filter((r) => r.enabled).map((r) => r.id);
      const wanted = u.retailers.filter((id) => enabledIds.includes(id));
      state.selected = new Set(wanted.length ? wanted : enabledIds);
      if (["price", "price-desc", "relevance", "retailer"].includes(u.sort)) els.sort.value = u.sort;
      renderRetailers();
      if (u.q) runSearch(u.q, { push: false }); else els.q.focus();
    } catch (err) {
      showError("Could not load retailer list. Is the server running?");
    }
  })();
})();
