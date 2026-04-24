// NETPULSE dashboard client
(function () {
  "use strict";

  var cfg = window.NETPULSE_CONFIG || { refreshMs: 5000, ssids: [] };
  var PROBE_STALE_SECONDS = 60;

  // ---- Helpers --------------------------------------------------------
  function $(id) { return document.getElementById(id); }

  function fmtTime(ts) {
    if (!ts) return "--:--:--";
    var d = new Date(ts * 1000);
    var h = ("0" + d.getHours()).slice(-2);
    var m = ("0" + d.getMinutes()).slice(-2);
    var s = ("0" + d.getSeconds()).slice(-2);
    return h + ":" + m + ":" + s;
  }

  function fmtRelative(ts) {
    if (!ts) return "";
    var diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 5) return "just now";
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }

  function tickClock() {
    var now = new Date();
    var h = ("0" + now.getHours()).slice(-2);
    var m = ("0" + now.getMinutes()).slice(-2);
    var s = ("0" + now.getSeconds()).slice(-2);
    $("clock").textContent = h + ":" + m + ":" + s;
  }

  function setText(id, text) { var el = $(id); if (el) el.textContent = text; }

  // ---- Fetchers -------------------------------------------------------
  function fetchJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  // ---- Renderers ------------------------------------------------------
  function renderStatus(status) {
    var dot = $("status-dot");
    var label = $("status-label");
    var panel = $("verdict-panel");

    dot.className = "status-dot " + (status.overall || "unknown");
    label.textContent = (status.overall || "unknown").toUpperCase();

    panel.classList.remove("healthy", "degraded", "outage");
    if (status.overall) panel.classList.add(status.overall);

    setText("verdict-pattern", status.pattern ? status.pattern : "NOMINAL");
    setText("verdict-time", fmtTime(status.ts));
    setText("verdict-summary", status.summary || "—");

    var recs = $("verdict-recs");
    recs.innerHTML = "";
    (status.recommendations || []).forEach(function (r) {
      var li = document.createElement("li");
      li.textContent = r;
      recs.appendChild(li);
    });

    // Render SSID tiles
    var tiles = $("ssid-tiles");
    tiles.innerHTML = "";
    (status.ssids || []).forEach(function (s) {
      var tile = document.createElement("div");
      var cls = "ssid-tile";
      if (s.critical && !s.healthy) cls += " critical-unhealthy";
      else if (s.healthy) cls += " healthy";
      tile.className = cls;

      var badge = s.critical ? '<span class="ssid-badge">CRIT</span>' : "";
      var dropClass = s.drop_percent >= 50 ? "ssid-drop high" : "ssid-drop";

      tile.innerHTML =
        '<div class="ssid-name">' + escapeHtml(s.name) + badge + '</div>' +
        '<div class="ssid-metric">' +
          '<span class="ssid-metric-value">' + s.current_clients + '</span>' +
          '<span class="ssid-metric-label">clients</span>' +
        '</div>' +
        '<div class="ssid-meta">' +
          '<span>peak: ' + s.baseline_clients + '</span>' +
          '<span class="' + dropClass + '">&#8595; ' + s.drop_percent + '%</span>' +
        '</div>';
      tiles.appendChild(tile);
    });

    if ((status.ssids || []).length === 0) {
      tiles.innerHTML = '<div class="empty">Waiting for Meraki data...</div>';
    }
  }

  function renderProbes(latest) {
    var list = $("probe-list");
    list.innerHTML = "";
    var now = Math.floor(Date.now() / 1000);

    var types = [
      { key: "ping_gateway", label: "GATEWAY", subLabel: "ICMP" },
      { key: "ping_external", label: "EXTERNAL", subLabel: "ICMP" },
      { key: "dns", label: "DNS LOOKUP", subLabel: "NSLOOKUP" },
      { key: "dhcp_state", label: "DHCP LEASE", subLabel: "IPCONFIG" }
    ];

    types.forEach(function (t) {
      var p = latest[t.key];
      var row = document.createElement("div");
      var cls = "probe-row";
      var icon = "?";
      var latText = "--";

      if (!p) {
        cls += " stale";
        icon = "?";
        latText = "no data";
      } else if (now - p.ts > PROBE_STALE_SECONDS) {
        cls += " stale";
        icon = "?";
        latText = "stale (" + fmtRelative(p.ts) + ")";
      } else if (!p.success) {
        cls += " fail";
        icon = "X";
        latText = p.error || "FAIL";
      } else {
        icon = "&#10003;";
        if (t.key === "dhcp_state") {
          if (p.latency_ms !== null && p.latency_ms !== undefined) {
            var hours = Math.floor(p.latency_ms / 3600);
            var mins = Math.floor((p.latency_ms % 3600) / 60);
            latText = hours + "h " + mins + "m old";
          } else {
            latText = "active";
          }
        } else if (p.latency_ms !== null && p.latency_ms !== undefined) {
          latText = p.latency_ms.toFixed(1) + " ms";
        } else {
          latText = "ok";
        }
      }

      row.className = cls;
      row.innerHTML =
        '<div class="probe-icon">' + icon + '</div>' +
        '<div class="probe-label">' + escapeHtml(t.label) + '<small>' +
          escapeHtml(t.subLabel) + ' &middot; ' + escapeHtml((p && p.target) || "—") +
        '</small></div>' +
        '<div class="probe-latency">' + latText + '</div>';
      list.appendChild(row);
    });
  }

  function renderEvents(events) {
    var list = $("events-list");
    if (!events || events.length === 0) {
      list.innerHTML = '<div class="empty">No events yet</div>';
      return;
    }
    list.innerHTML = "";
    events.slice(0, 30).forEach(function (e) {
      var row = document.createElement("div");
      row.className = "event-row " + (e.severity || "info");
      row.innerHTML =
        '<div class="event-time">' + fmtRelative(e.ts) + '</div>' +
        '<div>' +
          '<div class="event-title">' + escapeHtml(e.title || e.category) + '</div>' +
          '<div class="event-detail">' + escapeHtml(e.source + " :: " + (e.category || "")) + '</div>' +
        '</div>';
      list.appendChild(row);
    });
  }

  function renderIncidents(incidents) {
    var list = $("incidents-list");
    if (!incidents || incidents.length === 0) {
      list.innerHTML = '<div class="empty">No incidents recorded</div>';
      return;
    }
    list.innerHTML = "";
    incidents.forEach(function (i) {
      var row = document.createElement("div");
      row.className = "incident-row " + (i.severity || "warning");
      var statusBadge = i.ended_at
        ? '<span class="incident-status resolved">RESOLVED</span>'
        : '<span class="incident-status">ACTIVE</span>';
      var duration = i.ended_at
        ? Math.floor((i.ended_at - i.started_at) / 60) + "m"
        : fmtRelative(i.started_at).replace(" ago", "");
      row.innerHTML =
        '<div class="incident-time">' + fmtRelative(i.started_at) + '</div>' +
        '<div>' +
          '<div class="incident-title">' + escapeHtml(i.pattern) + statusBadge + '</div>' +
          '<div class="incident-detail">' + escapeHtml(i.summary) + ' &middot; ' + duration + '</div>' +
        '</div>';
      list.appendChild(row);
    });
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ---- Poll loop ------------------------------------------------------
  function pollOnce() {
    Promise.all([
      fetchJSON("/api/status"),
      fetchJSON("/api/probes/latest"),
      fetchJSON("/api/events?limit=30"),
      fetchJSON("/api/incidents?limit=15")
    ]).then(function (results) {
      renderStatus(results[0]);
      renderProbes(results[1]);
      renderEvents(results[2]);
      renderIncidents(results[3]);
      setText("last-poll", fmtTime(Math.floor(Date.now() / 1000)));
    }).catch(function (err) {
      console.error("Poll failed:", err);
      var dot = $("status-dot");
      if (dot) dot.className = "status-dot unknown";
      setText("status-label", "OFFLINE");
    });
  }

  // ---- Init -----------------------------------------------------------
  setInterval(tickClock, 1000);
  tickClock();
  pollOnce();
  setInterval(pollOnce, cfg.refreshMs);
})();

// ============================================================================
// Settings Modal
// ============================================================================
(function () {
  "use strict";

  var modal = document.getElementById("settings-modal");
  var btn = document.getElementById("settings-btn");
  var closeBtn = document.getElementById("settings-close");
  var backdrop = document.getElementById("settings-backdrop");
  var body = document.getElementById("settings-body");
  var saveBtn = document.getElementById("settings-save");
  var resetBtn = document.getElementById("settings-reset");
  var statusEl = document.getElementById("settings-status");
  var pathEl = document.getElementById("settings-config-path");

  if (!modal || !btn) return; // settings UI not present

  var schema = [];       // array of setting definitions (from API)
  var originalValues = {}; // key -> value at open time
  var currentValues = {};  // key -> value being edited

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function openSettings() {
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    loadSettings();
  }

  function closeSettings() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    setStatus("", "");
  }

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "settings-status " + (cls || "");
  }

  function loadSettings() {
    body.innerHTML = '<div class="settings-loading">Loading settings...</div>';
    fetch("/api/settings")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        schema = data.settings || [];
        pathEl.textContent = data.config_path || "—";
        originalValues = {};
        currentValues = {};
        schema.forEach(function (s) {
          originalValues[s.key] = s.current;
          currentValues[s.key] = s.current;
        });
        renderSettings();
        setStatus("Settings loaded.", "info");
        updateButtons();
      })
      .catch(function (err) {
        body.innerHTML = '<div class="settings-loading">Failed to load settings.</div>';
        setStatus("Error: " + err, "error");
      });
  }

  function renderSettings() {
    var html = "";
    schema.forEach(function (s) {
      if (s.type === "bool") {
        html += renderBoolRow(s);
      } else {
        html += renderNumberRow(s);
      }
    });
    body.innerHTML = html;
    wireInputs();
  }

  function renderNumberRow(s) {
    var val = currentValues[s.key];
    var unit = s.unit ? '<span class="setting-unit">' + escapeHtml(s.unit) + '</span>' : '';
    var displayVal = formatValue(s, val);
    return '' +
      '<div class="setting-row" data-key="' + escapeHtml(s.key) + '">' +
        '<div class="setting-top">' +
          '<span class="setting-label" data-label>' + escapeHtml(s.label) + '</span>' +
          '<span class="setting-value-display" data-display>' + displayVal + unit + '</span>' +
        '</div>' +
        '<div class="setting-description">' + escapeHtml(s.description) + '</div>' +
        '<div class="setting-slider-wrap">' +
          '<input type="range" class="setting-slider" data-input ' +
            'min="' + s.min + '" max="' + s.max + '" step="' + (s.step || 1) + '" ' +
            'value="' + val + '" />' +
        '</div>' +
        '<div class="setting-range-labels">' +
          '<span>' + s.min + '</span><span>' + s.max + '</span>' +
        '</div>' +
      '</div>';
  }

  function renderBoolRow(s) {
    var val = currentValues[s.key];
    var onClass = val ? "on" : "";
    var labelClass = val ? "on" : "off";
    var labelText = val ? "ENABLED" : "DISABLED";
    return '' +
      '<div class="setting-row" data-key="' + escapeHtml(s.key) + '">' +
        '<div class="setting-top">' +
          '<span class="setting-label" data-label>' + escapeHtml(s.label) + '</span>' +
        '</div>' +
        '<div class="setting-description">' + escapeHtml(s.description) + '</div>' +
        '<div class="setting-bool-row">' +
          '<div class="setting-toggle ' + onClass + '" data-toggle role="switch" aria-checked="' + val + '"></div>' +
          '<span class="setting-toggle-label ' + labelClass + '" data-toggle-label>' + labelText + '</span>' +
        '</div>' +
      '</div>';
  }

  function formatValue(s, val) {
    if (s.type === "int") {
      if (s.key === "baseline_window_seconds" && val >= 60) {
        var mins = Math.round(val / 60);
        return val + ' <span style="color:var(--text-muted);font-size:.8rem">(' + mins + 'm)</span>';
      }
      if (s.key === "dedupe_seconds" && val >= 60) {
        var dmins = Math.round(val / 60);
        return val + ' <span style="color:var(--text-muted);font-size:.8rem">(' + dmins + 'm)</span>';
      }
      return val;
    }
    return val;
  }

  function wireInputs() {
    var rows = body.querySelectorAll(".setting-row");
    rows.forEach(function (row) {
      var key = row.getAttribute("data-key");
      var spec = findSpec(key);
      if (!spec) return;
      if (spec.type === "bool") {
        var toggle = row.querySelector("[data-toggle]");
        toggle.addEventListener("click", function () {
          currentValues[key] = !currentValues[key];
          toggle.classList.toggle("on", currentValues[key]);
          toggle.setAttribute("aria-checked", String(currentValues[key]));
          var lbl = row.querySelector("[data-toggle-label]");
          lbl.textContent = currentValues[key] ? "ENABLED" : "DISABLED";
          lbl.className = "setting-toggle-label " + (currentValues[key] ? "on" : "off");
          markChanged(row, key);
          updateButtons();
        });
      } else {
        var input = row.querySelector("[data-input]");
        var display = row.querySelector("[data-display]");
        input.addEventListener("input", function () {
          var v = parseInt(input.value, 10);
          currentValues[key] = v;
          var unit = spec.unit ? '<span class="setting-unit">' + escapeHtml(spec.unit) + '</span>' : '';
          display.innerHTML = formatValue(spec, v) + unit;
          markChanged(row, key);
          updateButtons();
        });
      }
    });
  }

  function markChanged(row, key) {
    var label = row.querySelector("[data-label]");
    if (currentValues[key] !== originalValues[key]) {
      label.classList.add("changed");
    } else {
      label.classList.remove("changed");
    }
  }

  function findSpec(key) {
    for (var i = 0; i < schema.length; i++) {
      if (schema[i].key === key) return schema[i];
    }
    return null;
  }

  function hasChanges() {
    for (var k in currentValues) {
      if (currentValues[k] !== originalValues[k]) return true;
    }
    return false;
  }

  function updateButtons() {
    var dirty = hasChanges();
    saveBtn.disabled = !dirty;
    resetBtn.disabled = !dirty;
  }

  function save() {
    if (!hasChanges()) return;
    setStatus("Saving...", "info");
    saveBtn.disabled = true;
    resetBtn.disabled = true;
    var updates = {};
    for (var k in currentValues) {
      if (currentValues[k] !== originalValues[k]) {
        updates[k] = currentValues[k];
      }
    }
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates: updates })
    })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (result) {
        if (!result.ok) {
          var errMsg = "Save failed.";
          if (result.data && result.data.detail) {
            if (typeof result.data.detail === "string") errMsg = result.data.detail;
            else if (result.data.detail.errors) errMsg = result.data.detail.errors.join("; ");
          }
          setStatus(errMsg, "error");
          updateButtons();
          return;
        }
        setStatus("Saved. Detection engine reloaded.", "success");
        // Refresh the schema so originalValues reflect saved state
        schema = result.data.settings || schema;
        originalValues = {};
        schema.forEach(function (s) { originalValues[s.key] = s.current; currentValues[s.key] = s.current; });
        renderSettings();
        updateButtons();
      })
      .catch(function (err) {
        setStatus("Network error: " + err, "error");
        updateButtons();
      });
  }

  function reset() {
    schema.forEach(function (s) { currentValues[s.key] = originalValues[s.key]; });
    renderSettings();
    setStatus("Changes reverted.", "info");
    updateButtons();
  }

  btn.addEventListener("click", openSettings);
  closeBtn.addEventListener("click", closeSettings);
  backdrop.addEventListener("click", closeSettings);
  saveBtn.addEventListener("click", save);
  resetBtn.addEventListener("click", reset);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal.classList.contains("open")) closeSettings();
  });
})();