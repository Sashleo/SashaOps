/*
 * charts.js -- Draws chart specs built in Python (psx_charts.py).
 * Reads colours from CSS custom properties so theme changes recolour charts.
 * Canvas must be inside a .chart-frame with position:relative and explicit height.
 */
(function () {
  "use strict";

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function palette() {
    return {
      text: css('--text-muted') || '#94A3B8',
      grid: css('--border') || '#2B3544',
      series: [
        css('--series-1') || '#3987E5',
        css('--series-2') || '#D95926',
        css('--series-3') || '#199E70',
        css('--series-4') || '#C98500',
      ],
      accent: css('--accent') || '#2DD4BF',
      muted: css('--text-muted') || '#94A3B8',
    };
  }

  var compactNum = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });

  function baseOpts(spec, p, mini) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 150 },
      spanGaps: false,
      plugins: {
        legend: {
          display: !mini || (spec.series && spec.series.length > 1),
          labels: { color: p.text, boxWidth: 10, font: { size: mini ? 10 : 11 } }
        },
        title: { display: false },
        tooltip: { enabled: true }
      },
      scales: {
        x: {
          title: { display: !mini && !!spec.x_label, text: spec.x_label || '', color: p.text, font: { size: 10 } },
          ticks: { color: p.text, font: { size: mini ? 9 : 10 }, maxTicksLimit: mini ? 6 : undefined },
          grid: { color: p.grid + '33' }
        },
        y: {
          title: { display: !mini && !!spec.y_label, text: spec.y_label || '', color: p.text, font: { size: 10 } },
          ticks: {
            color: p.text,
            font: { size: mini ? 9 : 10 },
            maxTicksLimit: mini ? 4 : undefined,
            callback: function(v) {
              if (spec.y_format === 'percent') return (v * 100).toFixed(1) + '%';
              if (spec.y_format === 'ratio') return v.toFixed(1) + 'x';
              if (Math.abs(v) >= 1e6) return compactNum.format(v);
              return v;
            }
          },
          grid: { color: p.grid + '33' }
        }
      }
    };
  }

  function colorForIdx(p, i) { return p.series[i % p.series.length]; }

  function groupedBar(ctx, spec, p, mini) {
    var ds = spec.series.map(function (s, i) {
      return {
        label: s.name, data: s.data,
        backgroundColor: s.color || colorForIdx(p, i),
        borderWidth: 0, borderRadius: 2
      };
    });
    var opts = baseOpts(spec, p, mini);
    if (spec.annotations) {
      opts.plugins.tooltip.callbacks = {
        afterBody: function (items) {
          var idx = items[0].dataIndex;
          var g = spec.annotations[idx];
          return g == null ? '' : 'vs prior: ' + (g > 0 ? '+' : '') + g + '%';
        }
      };
    }
    return new Chart(ctx, { type: 'bar', data: { labels: spec.labels, datasets: ds }, options: opts });
  }

  function line(ctx, spec, p, mini) {
    var ds = spec.series.map(function (s, i) {
      return {
        label: s.name, data: s.data,
        borderColor: s.color || colorForIdx(p, i),
        backgroundColor: 'transparent',
        borderWidth: 2, pointRadius: mini ? 2 : 3, pointHoverRadius: 5,
        tension: 0.15
      };
    });
    return new Chart(ctx, { type: 'line', data: { labels: spec.labels, datasets: ds }, options: baseOpts(spec, p, mini) });
  }

  function smallMultiples(host, spec, p) {
    var grid = document.createElement('div');
    grid.className = 'grid--charts';
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(auto-fit,minmax(12rem,1fr))';
    grid.style.gap = 'var(--s-3)';
    host.parentNode.insertBefore(grid, host);
    host.style.display = 'none';

    spec.series.forEach(function (panel) {
      var cell = document.createElement('div');
      cell.style.background = 'var(--surface-2)';
      cell.style.borderRadius = 'var(--radius-sm)';
      cell.style.padding = 'var(--s-2)';
      var label = document.createElement('div');
      label.textContent = panel.name + (panel.unit === 'percent' ? ' (%)' : '');
      label.style.cssText = 'font-size:0.75rem;color:var(--text-muted);margin-bottom:0.25rem';
      cell.appendChild(label);
      var frame = document.createElement('div');
      frame.className = 'chart-frame';
      frame.style.height = '5.5rem';
      var cv = document.createElement('canvas');
      frame.appendChild(cv);
      cell.appendChild(frame);
      grid.appendChild(cell);

      var datasets = [{
        label: panel.name, data: panel.data,
        borderColor: p.series[0], backgroundColor: 'transparent',
        borderWidth: 2, pointRadius: 2, tension: 0.15
      }];
      if (panel.median != null) {
        datasets.push({
          label: 'Sector median',
          data: panel.labels.map(function () { return panel.median; }),
          borderColor: p.muted, borderDash: [4, 4], borderWidth: 1, pointRadius: 0
        });
      }
      new Chart(cv.getContext('2d'), {
        type: 'line',
        data: { labels: panel.labels, datasets: datasets },
        options: {
          responsive: true, maintainAspectRatio: false, spanGaps: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: p.text, font: { size: 9 } }, grid: { color: p.grid + '33' } },
            y: { ticks: { color: p.text, font: { size: 9 } }, grid: { color: p.grid + '33' } }
          }
        }
      });
    });
  }

  function dotPlot(ctx, spec, p, mini) {
    var labels = spec.series.map(function (r) { return r.name; });
    var data = spec.series.map(function (r) { return r.percentile; });
    var opts = baseOpts(spec, p, mini);
    opts.indexAxis = 'y';
    opts.scales.x.min = 0;
    opts.scales.x.max = 100;
    opts.plugins.legend.display = false;
    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          { label: 'Percentile', data: data, backgroundColor: p.series[0], barPercentage: 0.35 },
          { label: 'Median', type: 'line',
            data: labels.map(function () { return spec.median_marker || 50; }),
            borderColor: p.muted, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 }
        ]
      },
      options: opts
    });
  }

  function scatter(ctx, spec, p, mini) {
    var subject = spec.series.filter(function (pt) { return pt.is_subject; });
    var peers = spec.series.filter(function (pt) { return !pt.is_subject; });
    var opts = baseOpts(spec, p, mini);
    opts.plugins.tooltip.callbacks = {
      label: function (c) {
        var pt = c.raw;
        return pt.label + ' (' + pt.x + ', ' + pt.y + ')';
      }
    };
    return new Chart(ctx, {
      type: 'scatter',
      data: {
        datasets: [
          { label: 'Peers', data: peers, backgroundColor: p.muted, pointRadius: 4 },
          { label: 'Subject', data: subject, backgroundColor: p.series[0], pointRadius: 7 }
        ]
      },
      options: opts
    });
  }

  function gauge(ctx, spec, p, mini) {
    var bands = spec.bands || [];
    var opts = baseOpts(spec, p, mini);
    opts.indexAxis = 'y';
    opts.plugins.legend.display = true;
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['Z-Score'],
        datasets: bands.map(function (b) {
          return { label: b.name, data: [b.width], backgroundColor: b.color, barPercentage: 0.5 };
        }).concat([{
          label: 'Score ' + spec.score, type: 'line',
          data: [{ x: spec.score, y: 'Z-Score' }],
          borderColor: p.text, pointRadius: 7, pointStyle: 'rectRot', showLine: false
        }])
      },
      options: opts
    });
  }

  function heatmap(ctx, spec, p, mini) {
    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels: spec.labels,
        datasets: spec.series.map(function (s, i) {
          return { label: s.name, data: s.data, backgroundColor: s.color || colorForIdx(p, i) };
        })
      },
      options: baseOpts(spec, p, mini)
    });
  }

  function stackedBar(ctx, spec, p, mini) {
    var opts = baseOpts(spec, p, mini);
    opts.indexAxis = 'y';
    opts.scales.x.stacked = true;
    opts.scales.y.stacked = true;
    return new Chart(ctx, {
      type: 'bar',
      data: {
        labels: spec.labels,
        datasets: spec.series.map(function (s, i) {
          return { label: s.name, data: s.data, backgroundColor: s.color || colorForIdx(p, i) };
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

  function drawSpec(canvas, spec, mini) {
    var p = palette();
    var existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    if (spec.kind === 'small_multiples') {
      smallMultiples(canvas, spec, p);
      return;
    }
    var fn = RENDERERS[spec.kind];
    if (fn) fn(canvas.getContext('2d'), spec, p, mini !== false);
  }

  // Expose for dialog reuse
  window._drawChartSpec = function(canvas, spec) { drawSpec(canvas, spec, false); };

  function renderOne(canvas) {
    if (canvas.dataset.sashaopsRendered === '1') return;
    var raw = canvas.getAttribute('data-sashaops-chart');
    if (!raw) return;
    var spec;
    try { spec = JSON.parse(raw); } catch (e) { return; }
    canvas.dataset.sashaopsRendered = '1';

    try {
      drawSpec(canvas, spec, true);
    } catch (e) {
      canvas.insertAdjacentHTML('afterend',
        '<div style="font-size:0.75rem;color:var(--text-muted)">Chart could not be drawn.</div>');
    }
  }

  function renderAll(root) {
    (root || document).querySelectorAll('canvas[data-sashaops-chart]').forEach(renderOne);
  }

  // Print handler
  window.addEventListener('beforeprint', function () {
    Chart.helpers.each(Chart.instances, function (c) { if (c) c.resize(); });
  });

  document.addEventListener('DOMContentLoaded', function () { renderAll(); });
  document.addEventListener('htmx:afterSettle', function (e) { renderAll(e.target); });

  // Event delegation for .chart-open buttons — works after any HTMX swap
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.chart-open');
    if (!btn) return;
    var canvas = btn.querySelector('canvas[data-sashaops-chart]');
    if (!canvas) return;
    var raw = canvas.getAttribute('data-sashaops-chart');
    if (!raw) return;
    try {
      var spec = JSON.parse(raw);
      if (window.openChartDialog) window.openChartDialog(spec.title || '', spec);
    } catch (err) {}
  });
})();
