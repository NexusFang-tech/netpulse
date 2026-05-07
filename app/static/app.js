// NetPulse dashboard - v1.2
"use strict";

(function () {
  var REFRESH_MS = (window.NETPULSE_CONFIG && window.NETPULSE_CONFIG.refreshMs) || 5000;
  document.getElementById("refresh-rate").textContent = REFRESH_MS + "ms";

  var deviceState = {
    devices: [],
    sortKey: "last_seen",
    sortDir: "desc",
    filter: "",
  };

  // ---------- helpers ----------
  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtTime(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function fmtRel(ts) {
    if (!ts) return "—";
    var diff = (Date.now() / 1000) - ts;
    if (diff < 60) return Math.floor(diff) + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }
  function fmtMs(v) {
    if (v === null || v === undefined) return "—";
    if (v < 1) return "<1 ms";
    return Math.round(v) + " ms";
  }

  // ---------- Clock ----------
  function tickClock() {
    var d = new Date();
    var h = String(d.getHours()).padStart(2, "0");
    var m = String(d.getMinutes()).padStart(2, "0");
    var s = String(d.getSeconds()).padStart(2, "0");
    document.getElementById("clock").textContent = h + ":" + m + ":" + s;
  }
  setInterval(tickClock, 1000); tickClock();

  // ---------- Status / verdict ----------
  function renderStatus(data) {
    var card = document.getElementById("verdict-card");
    var pat = document.getElementById("verdict-pattern");
    var sum = document.getElementById("verdict-summary");
    var recs = document.getElementById("verdict-recs");
    var time = document.getElementById("verdict-time");
    var dot = document.getElementById("status-dot");
    var label = document.getElementById("status-label");

    card.classList.remove("healthy", "degraded");
    dot.classList.remove("warn", "crit");
    if (data.overall === "healthy") {
      card.classList.add("healthy");
      pat.textContent = "NOMINAL";
      label.textContent = "HEALTHY";
    } else if (data.overall === "degraded") {
      card.classList.add("degraded");
      dot.classList.add("warn");
      pat.textContent = data.pattern || "DEGRADED";
      label.textContent = "DEGRADED";
    } else {
      dot.classList.add("crit");
      pat.textContent = data.pattern || "OUTAGE";
      label.textContent = "OUTAGE";
    }
    sum.textContent = data.summary || "—";
    time.textContent = fmtTime(data.ts);

    recs.innerHTML = "";
    (data.recommendations || []).forEach(function (r) {
      var li = document.createElement("li");
      li.textContent = r;
      recs.appendChild(li);
    });

    // SSID grid
    var grid = document.getElementById("ssid-grid");
    grid.innerHTML = "";
    (data.ssids || []).forEach(function (s) {
      var tile = document.createElement("div");
      tile.className = "ssid-tile" + (s.healthy ? "" : " unhealthy");
      var dropClass = s.drop_percent > 50 ? "ssid-drop bad" : "ssid-drop";
      tile.innerHTML =
        '<div class="ssid-tile-header">' +
          '<span class="ssid-name">' + escapeHtml(s.name) + '</span>' +
          (s.critical ? '<span class="ssid-badge">CRIT</span>' : '') +
        '</div>' +
        '<div class="ssid-current">' + s.current_clients +
          ' <span class="ssid-current-label">CLIENTS</span></div>' +
        '<div class="ssid-meta">' +
          '<span>peak: ' + s.baseline_clients + '</span>' +
          '<span class="' + dropClass + '">↓ ' + s.drop_percent + '%</span>' +
        '</div>';
      grid.appendChild(tile);
    });
  }

  // ---------- Probes ----------
  function renderProbes(data) {
    var list = document.getElementById("probe-list");
    list.innerHTML = "";
    var rows = [
      { key: "ping_gateway", label: "GATEWAY", sub: "ICMP" },
      { key: "ping_external", label: "EXTERNAL", sub: "ICMP" },
      { key: "dns", label: "DNS LOOKUP", sub: "NSLOOKUP" },
      { key: "dhcp_state", label: "DHCP LEASE", sub: "IPCONFIG" },
    ];
    rows.forEach(function (r) {
      var d = data[r.key];
      var ok = d && d.success;
      var div = document.createElement("div");
      div.className = "probe-row" + (ok ? "" : " fail");
      var latency = "—";
      if (d) {
        if (r.key === "dhcp_state" && d.latency_ms) {
          latency = Math.floor(d.latency_ms / 3600) + "h " +
            Math.floor((d.latency_ms % 3600) / 60) + "m old";
        } else {
          latency = fmtMs(d.latency_ms);
        }
      }
      div.innerHTML =
        '<span class="probe-check">' + (ok ? "✓" : "✗") + '</span>' +
        '<div>' +
          '<div class="probe-name">' + r.label + '</div>' +
          '<div class="probe-target">' + r.sub + ' · ' +
          escapeHtml(d && d.target || "—") + '</div>' +
        '</div>' +
        '<div class="probe-latency">' + latency + '</div>';
      list.appendChild(div);
    });
  }

  // ---------- Events ----------
  function renderEvents(events) {
    var list = document.getElementById("event-list");
    list.innerHTML = "";
    if (!events || events.length === 0) {
      list.innerHTML = '<div class="empty-state">No recent events.</div>';
      return;
    }
    events.slice(0, 30).forEach(function (e) {
      var sev = e.severity === "critical" ? "crit"
              : e.severity === "warning" ? "warn" : "ok";
      var div = document.createElement("div");
      div.className = "event-row " + sev;
      div.innerHTML =
        '<div class="event-time">' + fmtRel(e.ts) + '</div>' +
        '<div>' +
          '<div class="event-title">' + escapeHtml(e.title || "?") + '</div>' +
          '<div class="event-detail">' + escapeHtml(e.source + ' :: ' + e.category) + '</div>' +
        '</div>';
      list.appendChild(div);
    });
  }

  // ---------- Incidents ----------
  function renderIncidents(incidents) {
    var list = document.getElementById("incident-list");
    list.innerHTML = "";
    if (!incidents || incidents.length === 0) {
      list.innerHTML = '<div class="empty-state">No recent incidents.</div>';
      return;
    }
    incidents.slice(0, 20).forEach(function (i) {
      var sev = i.severity === "critical" ? "crit" : "warn";
      var status = i.ended_at ? "RESOLVED" : (i.acknowledged ? "ACK" : "ACTIVE");
      var statusClass = i.ended_at ? "ok" : (i.severity === "critical" ? "crit" : "warn");
      var dur = i.ended_at ?
        Math.round((i.ended_at - i.started_at) / 60) + "m" : "—";
      var div = document.createElement("div");
      div.className = "incident-row " + sev;
      div.innerHTML =
        '<div class="incident-time">' + fmtRel(i.started_at) + '</div>' +
        '<div>' +
          '<div class="incident-title">' + escapeHtml(i.pattern) +
          ' <span class="incident-status ' + statusClass + '">' + status + '</span></div>' +
          '<div class="incident-detail">' + escapeHtml(i.summary) + ' · ' + dur + '</div>' +
        '</div>';
      list.appendChild(div);
    });
  }

  // ---------- AP Grid ----------
  function renderAps(aps) {
    var grid = document.getElementById("ap-grid");
    var summary = document.getElementById("ap-summary");
    grid.innerHTML = "";
    if (!aps || aps.length === 0) {
      grid.innerHTML = '<div class="empty-state">Waiting for AP data...</div>';
      summary.textContent = "—";
      return;
    }
    var total = aps.reduce(function (s, a) { return s + a.current_clients; }, 0);
    var unhealthy = aps.filter(function (a) { return !a.healthy; }).length;
    summary.textContent = aps.length + " APs · " + total + " clients" +
      (unhealthy > 0 ? " · " + unhealthy + " degraded" : "");
    aps.forEach(function (a) {
      var tile = document.createElement("div");
      var classes = ["ap-tile"];
      if (!a.healthy) classes.push("unhealthy");
      if (a.current_clients === 0) classes.push("silent");
      tile.className = classes.join(" ");
      tile.setAttribute("data-ap", a.name);
      var dropClass = a.drop_percent > 50 ? "drop bad" : "drop";
      tile.innerHTML =
        '<div class="ap-name">' + escapeHtml(a.name) + '</div>' +
        '<div class="ap-count">' + a.current_clients + '</div>' +
        '<div class="ap-meta">' +
          '<span>peak: ' + a.baseline_clients + '</span>' +
          '<span class="' + dropClass + '">↓ ' + a.drop_percent + '%</span>' +
        '</div>';
      tile.addEventListener("click", function () { openApModal(a.name); });
      grid.appendChild(tile);
    });
  }

  // ---------- Devices Table ----------
  function renderDevices() {
    var tbody = document.getElementById("device-tbody");
    var countEl = document.getElementById("devices-count");
    var filter = deviceState.filter.toLowerCase();
    var sortKey = deviceState.sortKey;
    var sortDir = deviceState.sortDir;
    var devices = deviceState.devices.slice();

    // filter
    if (filter) {
      devices = devices.filter(function (d) {
        return ((d.display_name || "") + " " + (d.mac || "") + " " +
                (d.ssid || "") + " " + (d.ap_name || "") + " " +
                (d.manufacturer || "") + " " + (d.ip || ""))
          .toLowerCase().indexOf(filter) >= 0;
      });
    }
    // sort
    devices.sort(function (a, b) {
      var av = a[sortKey], bv = b[sortKey];
      if (av === null || av === undefined) av = "";
      if (bv === null || bv === undefined) bv = "";
      if (typeof av === "string") av = av.toLowerCase();
      if (typeof bv === "string") bv = bv.toLowerCase();
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });

    countEl.textContent = devices.length + (deviceState.filter ? " filtered" : " online");
    tbody.innerHTML = "";
    if (devices.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">' +
        (deviceState.filter ? "No matches." : "No devices online yet.") + '</td></tr>';
      return;
    }
    devices.slice(0, 200).forEach(function (d) {
      var tr = document.createElement("tr");
      var ssidClass = "";
      if (d.ssid) {
        ssidClass = d.ssid.toLowerCase().indexOf("guest") >= 0 ? "ssid-guest" : "ssid-corp";
      }
      tr.innerHTML =
        '<td class="name">' + escapeHtml(d.display_name) + '</td>' +
        '<td>' + escapeHtml(d.manufacturer || "—") + '</td>' +
        '<td class="' + ssidClass + '">' + escapeHtml(d.ssid || "—") + '</td>' +
        '<td>' + escapeHtml(d.ap_name || "—") + '</td>' +
        '<td>' + escapeHtml(d.ip || "—") + '</td>' +
        '<td>' + escapeHtml(d.vlan || "—") + '</td>' +
        '<td>' + fmtRel(d.last_seen) + '</td>';
      tbody.appendChild(tr);
    });

    // sort indicators
    document.querySelectorAll("#device-table thead th").forEach(function (th) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.getAttribute("data-sort") === sortKey) {
        th.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  // sortable column headers
  document.querySelectorAll("#device-table thead th").forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.getAttribute("data-sort");
      if (deviceState.sortKey === key) {
        deviceState.sortDir = deviceState.sortDir === "asc" ? "desc" : "asc";
      } else {
        deviceState.sortKey = key;
        deviceState.sortDir = "asc";
      }
      renderDevices();
    });
  });

  // search box
  var searchInput = document.getElementById("device-search");
  searchInput.addEventListener("input", function () {
    deviceState.filter = searchInput.value;
    renderDevices();
  });

  // ---------- Recent disconnects ----------
  function renderDrops(drops) {
    var list = document.getElementById("drops-list");
    list.innerHTML = "";
    if (!drops || drops.length === 0) {
      list.innerHTML = '<div class="empty-state">No recent disconnects.</div>';
      return;
    }
    drops.slice(0, 50).forEach(function (d) {
      var div = document.createElement("div");
      div.className = "drop-row";
      div.innerHTML =
        '<div class="drop-name">' + escapeHtml(d.display_name || d.mac) + '</div>' +
        '<div class="drop-meta">' +
          escapeHtml(d.manufacturer || "—") + ' · ' +
          escapeHtml(d.ssid || "—") + ' · ' +
          escapeHtml(d.ap_name || "—") + ' · ' +
          fmtRel(d.last_seen) +
        '</div>';
      list.appendChild(div);
    });
  }

  // ---------- AP Modal ----------
  var apModal = document.getElementById("ap-modal");
  var apModalTitle = document.getElementById("ap-modal-title");
  var apModalBody = document.getElementById("ap-modal-body");
  document.getElementById("ap-close").addEventListener("click", closeApModal);
  document.getElementById("ap-backdrop").addEventListener("click", closeApModal);

  function openApModal(apName) {
    apModalTitle.textContent = "AP // " + apName.toUpperCase();
    apModalBody.innerHTML = '<div class="settings-loading">Loading...</div>';
    apModal.classList.add("open");
    fetch("/api/devices?status=online&limit=500")
      .then(function (r) { return r.json(); })
      .then(function (devs) {
        var onAp = devs.filter(function (d) { return d.ap_name === apName; });
        if (onAp.length === 0) {
          apModalBody.innerHTML = '<div class="empty-state">No clients on this AP right now.</div>';
          return;
        }
        var html = '<div class="ap-detail-list">';
        onAp.forEach(function (d) {
          html += '<div class="ap-detail-row">' +
            '<div class="name">' + escapeHtml(d.display_name) + '</div>' +
            '<div>' + escapeHtml(d.manufacturer || "—") + '</div>' +
            '<div class="meta">' + escapeHtml(d.ssid || "—") + ' · ' + escapeHtml(d.ip || "—") + '</div>' +
            '<div class="meta">' + fmtRel(d.last_seen) + '</div>' +
          '</div>';
        });
        html += '</div>';
        apModalBody.innerHTML = html;
      })
      .catch(function () {
        apModalBody.innerHTML = '<div class="empty-state">Failed to load.</div>';
      });
  }
  function closeApModal() { apModal.classList.remove("open"); }

  // ---------- DHCP Health (v1.3) ----------
  function renderDhcpHealth(data) {
    var panel = document.getElementById("dhcp-panel");
    var overall = document.getElementById("dhcp-overall");
    panel.classList.remove("degraded", "outage");

    if (!data || !data.enabled) {
      overall.textContent = "DHCP probing disabled — set dhcp_server.enabled in config";
      ["inform", "ping", "dns", "ldap"].forEach(function (k) {
        var el = document.getElementById("dhcp-probe-" + k);
        el.classList.remove("fail", "warn");
        el.classList.add("disabled");
        el.querySelector(".dhcp-probe-status").textContent = "OFF";
      });
      return;
    }

    var probes = data.probes || {};
    var advisory = !!data.inform_advisory;
    var failures = 0;
    var probeMap = {
      "inform": probes.dhcp_inform,
      "ping": probes.dc_ping,
      "dns": probes.dc_dns,
      "ldap": probes.dc_ldap,
    };
    Object.keys(probeMap).forEach(function (k) {
      var p = probeMap[k];
      var el = document.getElementById("dhcp-probe-" + k);
      el.classList.remove("fail", "warn", "disabled", "advisory");
      var statusEl = el.querySelector(".dhcp-probe-status");
      var metaEl = el.querySelector(".dhcp-probe-meta");
      var originalMeta = metaEl.getAttribute("data-original");
      if (!originalMeta) {
        originalMeta = metaEl.textContent;
        metaEl.setAttribute("data-original", originalMeta);
      }

      if (!p || !p.latest) {
        statusEl.textContent = "—";
        metaEl.textContent = "no data yet";
        return;
      }
      var latest = p.latest;
      if (latest.success) {
        var lat = latest.latency_ms !== null && latest.latency_ms !== undefined
          ? Math.round(latest.latency_ms) + " ms"
          : "OK";
        statusEl.textContent = lat;
        var sr = p.success_rate;
        if (sr < 95) {
          el.classList.add("warn");
          metaEl.textContent = sr + "% reliability (15m)";
        } else {
          metaEl.textContent = originalMeta + " · " + sr + "%";
        }
      } else {
        // INFORM probe failing alone is treated as advisory (likely firewall
        // filtering, not a real DHCP outage). Other probe failures are real.
        if (k === "inform" && advisory) {
          el.classList.add("advisory");
          statusEl.textContent = "ADVISORY";
          metaEl.textContent = "INFORM blocked - service likely OK (other DC probes green)";
        } else {
          failures += 1;
          el.classList.add("fail");
          statusEl.textContent = "FAIL";
          metaEl.textContent = (latest.error || "no response").substring(0, 60);
        }
      }
    });

    if (failures >= 2) {
      panel.classList.add("outage");
    } else if (failures === 1) {
      panel.classList.add("degraded");
    }

    var serverPart = data.server_ip ? data.server_ip : "unconfigured";
    var status_str;
    if (failures > 0) {
      status_str = " · " + failures + " probe(s) failing";
    } else if (advisory) {
      status_str = " · INFORM advisory (others healthy)";
    } else {
      status_str = " · all healthy";
    }
    overall.textContent = serverPart + status_str;

    // Recent DHCP events bar
    var bar = document.getElementById("dhcp-events-bar");
    var content = document.getElementById("dhcp-events-content");
    var evs = data.recent_dhcp_events || [];
    if (evs.length === 0) {
      bar.classList.remove("has-events");
      content.textContent = "none in the last hour";
    } else {
      bar.classList.add("has-events");
      var titles = evs.slice(0, 3).map(function (e) {
        return fmtRel(e.ts) + " — " + (e.title || e.category);
      });
      content.textContent = titles.join("  |  ") +
        (evs.length > 3 ? "  (+" + (evs.length - 3) + " more)" : "");
    }
  }

  function renderDhcpSparklines() {
    document.querySelectorAll(".dhcp-sparkline").forEach(function (el) {
      var probeType = el.getAttribute("data-probe");
      fetch("/api/dhcp/probe_history?probe_type=" + probeType + "&minutes=30")
        .then(function (r) { return r.json(); })
        .then(function (history) {
          if (!history || !Array.isArray(history)) return;
          el.innerHTML = "";
          // Show last 30 samples
          var recent = history.slice(-30);
          if (recent.length === 0) return;
          var maxLat = Math.max.apply(null, recent.map(function (h) {
            return h.latency_ms || 0;
          }).filter(function (n) { return n > 0; }).concat([1]));
          recent.forEach(function (h) {
            var bar = document.createElement("div");
            bar.className = "dhcp-spark-bar" + (h.success ? "" : " fail");
            var height = h.success && h.latency_ms
              ? Math.max(15, Math.min(100, (h.latency_ms / maxLat) * 100))
              : 100;
            bar.style.height = height + "%";
            el.appendChild(bar);
          });
        })
        .catch(function () {});
    });
  }

  // ---------- Polling loop ----------
  function pollAll() {
    document.getElementById("poll-time").textContent = fmtTime(Date.now() / 1000);
    fetch("/api/status").then(function (r) { return r.json(); }).then(renderStatus).catch(function () {});
    fetch("/api/probes/latest").then(function (r) { return r.json(); }).then(renderProbes).catch(function () {});
    fetch("/api/events?limit=30").then(function (r) { return r.json(); }).then(renderEvents).catch(function () {});
    fetch("/api/incidents?limit=20").then(function (r) { return r.json(); }).then(renderIncidents).catch(function () {});
    fetch("/api/aps").then(function (r) { return r.json(); }).then(renderAps).catch(function () {});
    fetch("/api/devices?status=online&limit=500").then(function (r) { return r.json(); })
      .then(function (devs) { deviceState.devices = devs || []; renderDevices(); }).catch(function () {});
    fetch("/api/devices/recent_drops?minutes=30&limit=50").then(function (r) { return r.json(); })
      .then(renderDrops).catch(function () {});
    fetch("/api/dhcp/health").then(function (r) { return r.json(); }).then(renderDhcpHealth).catch(function () {});
    renderDhcpSparklines();
  }
  pollAll();
  setInterval(pollAll, REFRESH_MS);

  // ============================================================================
  // Settings Modal
  // ============================================================================
  var modal = document.getElementById("settings-modal");
  var btn = document.getElementById("settings-btn");
  var closeBtn = document.getElementById("settings-close");
  var backdrop = document.getElementById("settings-backdrop");
  var body = document.getElementById("settings-body");
  var saveBtn = document.getElementById("settings-save");
  var resetBtn = document.getElementById("settings-reset");
  var statusEl = document.getElementById("settings-status");
  var pathEl = document.getElementById("settings-config-path");

  if (!modal || !btn) return;

  var schema = [];
  var originalValues = {};
  var currentValues = {};

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "settings-status " + (cls || "");
  }

  function loadSettings() {
    body.innerHTML = '<div class="settings-loading">Loading settings...</div>';
    fetch("/api/settings").then(function (r) { return r.json(); })
      .then(function (data) {
        schema = data.settings || [];
        pathEl.textContent = data.config_path || "—";
        originalValues = {}; currentValues = {};
        schema.forEach(function (s) { originalValues[s.key] = s.current; currentValues[s.key] = s.current; });
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
      html += s.type === "bool" ? renderBoolRow(s) : renderNumberRow(s);
    });
    body.innerHTML = html;
    wireInputs();
  }

  function formatValue(s, val) {
    if (s.type === "int" && (s.key === "baseline_window_seconds" ||
        s.key === "dedupe_seconds" || s.key === "device_offline_threshold_seconds") && val >= 60) {
      var mins = Math.round(val / 60);
      return val + ' <span style="color:var(--text-muted);font-size:.8rem">(' + mins + 'm)</span>';
    }
    return val;
  }

  function renderNumberRow(s) {
    var val = currentValues[s.key];
    var unit = s.unit ? '<span class="setting-unit">' + escapeHtml(s.unit) + '</span>' : '';
    return '<div class="setting-row" data-key="' + escapeHtml(s.key) + '">' +
      '<div class="setting-top">' +
        '<span class="setting-label" data-label>' + escapeHtml(s.label) + '</span>' +
        '<span class="setting-value-display" data-display>' + formatValue(s, val) + unit + '</span>' +
      '</div>' +
      '<div class="setting-description">' + escapeHtml(s.description) + '</div>' +
      '<div class="setting-slider-wrap">' +
        '<input type="range" class="setting-slider" data-input ' +
          'min="' + s.min + '" max="' + s.max + '" step="' + (s.step || 1) + '" value="' + val + '" />' +
      '</div>' +
      '<div class="setting-range-labels"><span>' + s.min + '</span><span>' + s.max + '</span></div>' +
    '</div>';
  }

  function renderBoolRow(s) {
    var val = currentValues[s.key];
    return '<div class="setting-row" data-key="' + escapeHtml(s.key) + '">' +
      '<div class="setting-top"><span class="setting-label" data-label>' + escapeHtml(s.label) + '</span></div>' +
      '<div class="setting-description">' + escapeHtml(s.description) + '</div>' +
      '<div class="setting-bool-row">' +
        '<div class="setting-toggle ' + (val ? "on" : "") + '" data-toggle role="switch" aria-checked="' + val + '"></div>' +
        '<span class="setting-toggle-label ' + (val ? "on" : "off") + '" data-toggle-label>' +
          (val ? "ENABLED" : "DISABLED") + '</span>' +
      '</div>' +
    '</div>';
  }

  function wireInputs() {
    body.querySelectorAll(".setting-row").forEach(function (row) {
      var key = row.getAttribute("data-key");
      var spec = schema.find(function (s) { return s.key === key; });
      if (!spec) return;
      if (spec.type === "bool") {
        var toggle = row.querySelector("[data-toggle]");
        toggle.addEventListener("click", function () {
          currentValues[key] = !currentValues[key];
          toggle.classList.toggle("on", currentValues[key]);
          var lbl = row.querySelector("[data-toggle-label]");
          lbl.textContent = currentValues[key] ? "ENABLED" : "DISABLED";
          lbl.className = "setting-toggle-label " + (currentValues[key] ? "on" : "off");
          markChanged(row, key); updateButtons();
        });
      } else {
        var input = row.querySelector("[data-input]");
        var display = row.querySelector("[data-display]");
        input.addEventListener("input", function () {
          var v = parseInt(input.value, 10);
          currentValues[key] = v;
          var unit = spec.unit ? '<span class="setting-unit">' + escapeHtml(spec.unit) + '</span>' : '';
          display.innerHTML = formatValue(spec, v) + unit;
          markChanged(row, key); updateButtons();
        });
      }
    });
  }

  function markChanged(row, key) {
    var label = row.querySelector("[data-label]");
    if (currentValues[key] !== originalValues[key]) label.classList.add("changed");
    else label.classList.remove("changed");
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
    saveBtn.disabled = true; resetBtn.disabled = true;
    var updates = {};
    for (var k in currentValues) {
      if (currentValues[k] !== originalValues[k]) updates[k] = currentValues[k];
    }
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates: updates })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (result) {
        if (!result.ok) {
          var errMsg = "Save failed.";
          if (result.data && result.data.detail) {
            if (typeof result.data.detail === "string") errMsg = result.data.detail;
            else if (result.data.detail.errors) errMsg = result.data.detail.errors.join("; ");
          }
          setStatus(errMsg, "error"); updateButtons(); return;
        }
        setStatus("Saved. Detection engine reloaded.", "success");
        schema = result.data.settings || schema;
        originalValues = {};
        schema.forEach(function (s) { originalValues[s.key] = s.current; currentValues[s.key] = s.current; });
        renderSettings(); updateButtons();
      })
      .catch(function (err) { setStatus("Network error: " + err, "error"); updateButtons(); });
  }

  function reset() {
    schema.forEach(function (s) { currentValues[s.key] = originalValues[s.key]; });
    renderSettings();
    setStatus("Changes reverted.", "info");
    updateButtons();
  }

  btn.addEventListener("click", function () {
    modal.classList.add("open"); modal.setAttribute("aria-hidden", "false"); loadSettings();
  });
  closeBtn.addEventListener("click", function () {
    modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); setStatus("", "");
  });
  backdrop.addEventListener("click", function () {
    modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true");
  });
  saveBtn.addEventListener("click", save);
  resetBtn.addEventListener("click", reset);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (modal.classList.contains("open")) modal.classList.remove("open");
      if (apModal.classList.contains("open")) apModal.classList.remove("open");
    }
  });
})();