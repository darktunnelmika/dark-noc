// DARK NOC mobile-only runtime. Keeps desktop behavior untouched.
(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('menu-toggle');
  if (!sidebar || !toggle) return;

  const mobileQuery = window.matchMedia('(max-width: 850px)');
  const backdrop = document.createElement('button');
  backdrop.type = 'button';
  backdrop.className = 'mobile-nav-backdrop';
  backdrop.setAttribute('aria-label', 'Close navigation');
  document.body.appendChild(backdrop);

  const updateViewportHeight = () => {
    const height = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty('--mobile-vh', `${Math.max(320, Math.round(height))}px`);
  };

  const syncNavigation = () => {
    const open = mobileQuery.matches && sidebar.classList.contains('open');
    backdrop.classList.toggle('open', open);
    document.body.classList.toggle('mobile-nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    sidebar.setAttribute('aria-hidden', String(mobileQuery.matches && !open));
  };

  const closeNavigation = ({ focusToggle = false } = {}) => {
    sidebar.classList.remove('open');
    syncNavigation();
    if (focusToggle) toggle.focus({ preventScroll: true });
  };

  toggle.setAttribute('aria-controls', 'sidebar');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.addEventListener('click', () => requestAnimationFrame(syncNavigation));
  backdrop.addEventListener('click', () => closeNavigation({ focusToggle: true }));

  sidebar.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      if (mobileQuery.matches) requestAnimationFrame(() => closeNavigation());
    });
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mobileQuery.matches && sidebar.classList.contains('open')) {
      event.preventDefault();
      closeNavigation({ focusToggle: true });
    }
  });

  const observer = new MutationObserver(syncNavigation);
  observer.observe(sidebar, { attributes: true, attributeFilter: ['class'] });

  const onMediaChange = () => {
    if (!mobileQuery.matches) sidebar.classList.remove('open');
    syncNavigation();
    updateViewportHeight();
  };
  mobileQuery.addEventListener?.('change', onMediaChange);
  window.addEventListener('resize', updateViewportHeight, { passive: true });
  window.visualViewport?.addEventListener('resize', updateViewportHeight, { passive: true });
  window.visualViewport?.addEventListener('scroll', updateViewportHeight, { passive: true });

  updateViewportHeight();
  syncNavigation();
})();
