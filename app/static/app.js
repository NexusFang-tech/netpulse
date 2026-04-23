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
