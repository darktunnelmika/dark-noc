/* DARK NOC / isolated progressive enhancement for the overview's live matrix.
 * Keeps renderLiveTopology(), its data, layout, listeners and MAP/LIST behavior intact.
 * No polling, API calls, application-state writes, globals or dependencies.
 */
(() => {
  'use strict';
  const root = document.querySelector('.topology-panel #topology');
  if (!root || root.dataset.matrixEnhanced) return;
  root.dataset.matrixEnhanced = '1';
  const ns = 'http://www.w3.org/2000/svg';
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const started = performance.now();
  let currentSVG = null;
  let inView = true;
  let scheduled = false;
  let suspended = false;

  const svgElement = (name, attributes) => {
    const node = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  };

  function syncMotion() {
    const paused = document.hidden || suspended || !inView || motion.matches || root.classList.contains('matrix-static');
    root.dataset.matrixPaused = String(paused);
    root.dataset.matrixReduced = String(motion.matches);
    // CSS cannot pause SVG animateMotion; stop the matrix's SMIL timeline too.
    if (currentSVG) {
      try {
        if (paused) currentSVG.pauseAnimations();
        else currentSVG.unpauseAnimations();
      } catch { /* The fixed connection paths still work without SMIL support. */ }
    }
  }

  function enhance() {
    scheduled = false;
    const svg = root.querySelector('svg.cyber-links');
    if (!svg) {
      currentSVG = null; // Empty state or LIST: do not create or replace any UI.
      root.classList.remove('matrix-lite', 'matrix-static');
      return;
    }
    if (svg !== currentSVG) {
      currentSVG = svg;
      // Preserve particle phase when native telemetry refresh replaces the SVG.
      try { svg.setCurrentTime((performance.now() - started) / 1000); } catch {}
    }
    const routes = [...svg.querySelectorAll('.route-group')];
    root.classList.toggle('matrix-lite', routes.length > 40);
    root.classList.toggle('matrix-static', routes.length > 96);
    const endpoints = [];
    routes.forEach((route, index) => {
      const backbone = route.querySelector('.cyber-route-backbone');
      const flow = route.querySelector('.cyber-route-flow');
      if (!backbone || !flow || route.dataset.matrixDecorated) return;
      route.dataset.matrixDecorated = '1';
      // Presentation contract with the existing renderer: its rate-derived width
      // is 1.60 for zero/unknown throughput (rounded to 2 decimals). Treat that
      // hint conservatively as idle; never derive or display metrics from it.
      const hint = Number.parseFloat(backbone.style.getPropertyValue('--route-width'));
      const canFlow = (route.classList.contains('online') || route.classList.contains('degraded')) && Number.isFinite(hint) && hint > 1.6;
      route.dataset.matrixFlow = canFlow ? 'active' : 'idle';
      route.style.setProperty('--matrix-energy', String(Number.isFinite(hint) ? Math.max(0, Math.min(1, (hint - 1.6) / 2.4)) : 0));
      const seconds = Number.parseFloat(flow.style.getPropertyValue('--flow-duration')) || 4;
      route.style.setProperty('--matrix-phase', `${-((index * .618) % 1) * seconds}s`);
      const d = backbone.getAttribute('d');
      if (!d) return;
      const aura = svgElement('path', { class: 'matrix-route-aura', d, 'aria-hidden': 'true' });
      const core = svgElement('path', { class: 'matrix-route-core', d, 'aria-hidden': 'true' });
      route.insertBefore(aura, backbone);
      route.insertBefore(core, flow);
      route.setAttribute('role', 'button');
      const label = route.querySelector('.route-label');
      const tone = ['online', 'degraded', 'stale', 'down'].find(value => route.classList.contains(value)) || 'unknown';
      route.setAttribute('aria-label', `${label?.textContent || 'Tunnel'} · ${tone} · Open operations`);
      try {
        const length = backbone.getTotalLength();
        if (!Number.isFinite(length) || length <= 0) return;
        const start = backbone.getPointAtLength(0), end = backbone.getPointAtLength(length);
        endpoints.push({ route, start, end });
        if (routes.length <= 40) {
          // A horizontal path has a zero-height object bounding box. The native
          // objectBoundingBox glow can disappear there, independently of rate.
          // Give just these flat routes a small, explicit, padded filter region.
          const bounds = backbone.getBBox();
          if (bounds.height < 8 || bounds.width < 8) {
            const id = `dark-matrix-flat-glow-${index}`;
            const filter = svgElement('filter', {
              id, filterUnits: 'userSpaceOnUse',
              x: bounds.x - 20, y: bounds.y - 20,
              width: Math.max(1, bounds.width) + 40,
              height: Math.max(1, bounds.height) + 40
            });
            const merge = svgElement('feMerge', {});
            merge.append(svgElement('feMergeNode', { in: 'glow' }), svgElement('feMergeNode', { in: 'SourceGraphic' }));
            filter.append(svgElement('feGaussianBlur', { stdDeviation: 2.8, result: 'glow' }), merge);
            let defs = svg.querySelector('defs');
            if (!defs) { defs = svgElement('defs', {}); svg.prepend(defs); }
            defs.append(filter);
            route.style.setProperty('--matrix-glow', `url(#${id})`);
          }
          const point = backbone.getPointAtLength(length / 2);
          const beacon = svgElement('g', { class: 'matrix-beacon', 'aria-hidden': 'true' });
          beacon.append(
            svgElement('circle', { class: 'matrix-beacon-pulse', cx: point.x, cy: point.y, r: 13 }),
            svgElement('circle', { class: 'matrix-beacon-ring', cx: point.x, cy: point.y, r: 8 }),
            svgElement('circle', { class: 'matrix-beacon-core', cx: point.x, cy: point.y, r: 2.6 })
          );
          route.insertBefore(beacon, label || null);
        }
      } catch { /* Invalid geometry must not break native controls or other views. */ }
    });
    // Highlight only genuinely connected routes, using the native canvas's y
    // coordinates. No new topology edges or fake junctions are introduced.
    root.querySelectorAll('.cyber-node').forEach(card => {
      if (card.dataset.matrixBound) return;
      card.dataset.matrixBound = '1';
      const y = Number.parseFloat(card.style.top);
      const side = card.classList.contains('root') ? 'start' : 'end';
      const related = endpoints.filter(item => Math.abs(item[side].y - y) < .6);
      const highlight = () => {
        const active = card.matches(':hover') || document.activeElement === card;
        related.forEach(item => item.route.classList.toggle('matrix-related', active));
      };
      for (const event of ['mouseenter', 'mouseleave', 'focus', 'blur']) card.addEventListener(event, highlight);
    });
    syncMotion();
  }

  // Observe only native root replacements, not our own descendant decoration.
  // This avoids recursive observer work and does not add a per-frame render loop.
  new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(enhance);
  }).observe(root, { childList: true });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting);
      syncMotion();
    }, { threshold: 0 }).observe(root);
  }
  document.addEventListener('visibilitychange', syncMotion);
  if (motion.addEventListener) motion.addEventListener('change', syncMotion);
  else motion.addListener(syncMotion);
  window.addEventListener('pagehide', () => { suspended = true; syncMotion(); });
  window.addEventListener('pageshow', () => { suspended = false; syncMotion(); });
  enhance();
})();
