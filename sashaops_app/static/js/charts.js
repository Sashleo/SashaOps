/*
 * static/js/charts.js - Phase 3 Task 3.8, web rendering.
 *
 * Chart payloads are built in Python (intelligence/psx_charts.py) and emitted on a
 * canvas as data-sashaops-chart. This file only draws them, so on-screen and
 * exported charts cannot disagree about values, labels or units.
 *
 * A null in a data series is a GAP: Chart.js skips it rather than joining across,
 * because interpolating a value that failed validation would invent data.
 */
(function () {
  "use strict";

  var ACCENT = "#00ff87";
  var NEUTRAL = "#6b7280";
  var GRID = "rgba(255,255,255,0.06)";
  var TEXT = "#9ca3af";

  function baseOptions(spec) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      spanGaps: false, // a failed-validation period stays a gap
      plugins: {
        legend: { labels: { color: TEXT, boxWidth: 10, font: { size: 10 } } },
        title: {
          display: !!spec.title,
          text: spec.title,
          color: TEXT,
          font: { size: 11, weight: "normal" }
        },
        tooltip: { enabled: true }
      },
      scales: {
        x: {
          title: { display: !!spec.x_label, text: spec.x_label, color: TEXT,
                   font: { size: 10 } },
          ticks: { color: TEXT, font: { size: 10 } },
          grid: { color: GRID }
        },
        y: {
          title: { display: !!spec.y_label, text: spec.y_label, color: TEXT,
                   font: { size: 10 } },
          ticks: { color: TEXT, font: { size: 10 } },
          grid: { color: GRID }
        }
      }
    };
  }

  function groupedBar(ctx, spec) {
    var ds = spec.series.map(function (s) {
      return {
        label: s.name,
        data: s.data,
        backgroundColor: s.color || NEUTRAL,
        borderWidth: 0
      };
    });
    var opts = baseOptions(spec);
    // Growth annotations sit in the tooltip so the plot stays readable and the
    // number always matches the plotted series.
    if (spec.annotations) {
      opts.plugins.tooltip.callbacks = {
        afterBody: function (items) {
          var i = items[0].dataIndex;
          var g = spec.annotations[i];
          return g === null || g === undefined
            ? "" : "Change vs prior period: " + (g > 0 ? "+" : "") + g + "%";
        }
      };
    }
    return new Chart(ctx, { type: "bar", data: { labels: spec.labels, datasets: ds },
                            options: opts });
  }

  function line(ctx, spec) {
    var ds = spec.series.map(function (s) {
      return {
        label: s.name,
        data: s.data,
        borderColor: s.color || ACCENT,
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.15
      };
    });
    return new Chart(ctx, { type: "line", data: { labels: spec.labels, datasets: ds },
                            options: baseOptions(spec) });
  }

  /* Small multiples: one canvas per ratio, laid out in a grid by the caller. */
  function smallMultiples(host, spec) {
    var grid = document.createElement("div");
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "repeat(auto-fit,minmax(200px,1fr))";
    grid.style.gap = "10px";
    host.parentNode.insertBefore(grid, host);
    host.style.display = "none";

    spec.series.forEach(function (panel) {
      var cell = document.createElement("div");
      cell.style.background = "rgba(255,255,255,0.02)";
      cell.style.borderRadius = "4px";
      cell.style.padding = "6px";
      var label = document.createElement("div");
      label.textContent = panel.name + (panel.unit === "percent" ? " (%)" : "");
      label.style.cssText = "font-size:0.72em;color:" + TEXT + ";margin-bottom:2px";
      cell.appendChild(label);
      var cv = document.createElement("canvas");
      cv.height = 90;
      cell.appendChild(cv);
      grid.appendChild(cell);

      var datasets = [{
        label: panel.name,
        data: panel.data,
        borderColor: panel.color || ACCENT,
        backgroundColor: "transparent",
        borderWidth: 2,
        pointRadius: 2,
        tension: 0.15
      }];
      if (panel.median !== null && panel.median !== undefined) {
        datasets.push({
          label: "Sector median",
          data: panel.labels.map(function () { return panel.median; }),
          borderColor: NEUTRAL,
          borderDash: [4, 4],
          borderWidth: 1,
          pointRadius: 0
        });
      }
      new Chart(cv.getContext("2d"), {
        type: "line",
        data: { labels: panel.labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false, spanGaps: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: TEXT, font: { size: 9 } }, grid: { color: GRID } },
            y: { ticks: { color: TEXT, font: { size: 9 } }, grid: { color: GRID } }
          }
        }
      });
    });
  }

  /* Horizontal dot plot: subject percentile per ratio, median marker at 50. */
  function dotPlot(ctx, spec) {
    var labels = spec.series.map(function (r) { return r.name; });
    var data = spec.series.map(function (r) { return r.percentile; });
    var opts = baseOptions(spec);
    opts.indexAxis = "y";
    opts.scales.x.min = 0;
    opts.scales.x.max = 100;
    opts.plugins.legend.display = false;
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          { label: "Percentile", data: data, backgroundColor: ACCENT,
            barPercentage: 0.35 },
          { label: "Sector median", type: "line",
            data: labels.map(function () { return spec.median_marker || 50; }),
            borderColor: NEUTRAL, borderDash: [4, 4], borderWidth: 1,
            pointRadius: 0 }
        ]
      },
      options: opts
    });
  }

  function scatter(ctx, spec) {
    var subject = spec.series.filter(function (p) { return p.is_subject; });
    var peers = spec.series.filter(function (p) { return !p.is_subject; });
    var opts = baseOptions(spec);
    opts.plugins.tooltip.callbacks = {
      label: function (c) {
        var p = c.raw;
        return p.label + " (" + p.x + ", " + p.y + ")";
      }
    };
    return new Chart(ctx, {
      type: "scatter",
      data: {
        datasets: [
          { label: "Sector peers", data: peers, backgroundColor: NEUTRAL,
            pointRadius: 4 },
          { label: "Subject", data: subject, backgroundColor: ACCENT,
            pointRadius: 7 }
        ]
      },
      options: opts
    });
  }

  /* Altman gauge: banded horizontal scale with the score marked. */
  function gauge(ctx, spec) {
    var bands = spec.bands || [];
    var opts = baseOptions(spec);
    opts.indexAxis = "y";
    opts.plugins.legend.display = true;
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: ["Z-Score"],
        datasets: bands.map(function (b) {
          return { label: b.name, data: [b.width], backgroundColor: b.color,
                   barPercentage: 0.5 };
        }).concat([{
          label: "Score " + spec.score, type: "line",
          data: [{ x: spec.score, y: "Z-Score" }],
          borderColor: "#ffffff", pointRadius: 7, pointStyle: "rectRot",
          showLine: false
        }])
      },
      options: opts
    });
  }

  function heatmap(ctx, spec) {
    // Rendered as grouped bars per terminal-growth row: each bar is one WACC step.
    var opts = baseOptions(spec);
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: spec.series.map(function (s) {
          return { label: s.name, data: s.data, backgroundColor: s.color || NEUTRAL };
        })
      },
      options: opts
    });
  }

  function stackedBar(ctx, spec) {
    var opts = baseOptions(spec);
    opts.indexAxis = "y";
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: spec.labels,
        datasets: spec.series.map(function (s) {
          return { label: s.name, data: s.data, backgroundColor: s.color || NEUTRAL };
        })
      },
      options: opts
    });
  }

  var RENDERERS = {
    grouped_bar: groupedBar,
    line: line,
    dot_plot: dotPlot,
    scatter: scatter,
    gauge: gauge,
    heatmap: heatmap,
    stacked_bar: stackedBar
  };

  function renderOne(canvas) {
    if (canvas.dataset.sashaopsRendered === "1") { return; }
    var raw = canvas.getAttribute("data-sashaops-chart");
    if (!raw) { return; }
    var spec;
    try {
      spec = JSON.parse(raw);
    } catch (e) {
      return;
    }
    canvas.dataset.sashaopsRendered = "1";
    try {
      if (spec.kind === "small_multiples") {
        smallMultiples(canvas, spec);
        return;
      }
      var fn = RENDERERS[spec.kind];
      if (fn) { fn(canvas.getContext("2d"), spec); }
    } catch (e) {
      // A chart must never break the section around it.
      canvas.insertAdjacentHTML("afterend",
        '<div style="font-size:0.76em;color:#6b7280">Chart could not be drawn.</div>');
    }
  }

  function renderAll(root) {
    var nodes = (root || document).querySelectorAll("canvas[data-sashaops-chart]");
    Array.prototype.forEach.call(nodes, renderOne);
  }

  document.addEventListener("DOMContentLoaded", function () { renderAll(document); });
  // Sections arrive via HTMX after page load, so draw whatever each swap brought in.
  document.body && document.body.addEventListener("htmx:afterSwap", function (e) {
    renderAll(e.target);
  });
  document.addEventListener("htmx:afterSwap", function (e) { renderAll(e.target); });
})();
