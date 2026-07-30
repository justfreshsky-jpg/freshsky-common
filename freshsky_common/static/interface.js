/* Progressive accessibility and navigation enhancement for FreshSky pages. */
(function() {
  function ready() {
    var body = document.body;
    if (!body) return;

    var primary = document.querySelector('main,[role="main"]') ||
      document.querySelector('.container,.wrap,.shell,.main-content');
    if (primary) {
      if (!primary.id) primary.id = 'main-content';
      if (primary.tagName !== 'MAIN' && !primary.hasAttribute('role')) {
        primary.setAttribute('role', 'main');
      }
      if (!document.querySelector('.fs-skip-link')) {
        var skip = document.createElement('a');
        skip.className = 'fs-skip-link';
        skip.href = '#' + primary.id;
        skip.textContent = 'Skip to main content';
        body.prepend(skip);
      }
    }

    document.querySelectorAll('.topbar,.nav,.fs-nav').forEach(function(nav) {
      if (!nav.hasAttribute('role')) nav.setAttribute('role', 'navigation');
      if (!nav.hasAttribute('aria-label')) {
        nav.setAttribute('aria-label', 'FreshSky navigation');
      }
    });

    document.querySelectorAll('a[target="_blank"]').forEach(function(link) {
      var values = (link.getAttribute('rel') || '')
        .split(/\s+/)
        .filter(Boolean);
      if (values.indexOf('noopener') === -1) values.push('noopener');
      link.setAttribute('rel', values.join(' '));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready, {once: true});
  } else {
    ready();
  }
})();
