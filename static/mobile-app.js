/* The phone shell.
 *
 * Builds an app bar and a bottom tab bar over whatever page is loaded, and turns table rows
 * into cards. Runs only under 780px, so a desktop never sees any of it, and it touches nothing
 * the pages themselves depend on: no markup is removed, only added or relabelled.
 */
(function () {
  'use strict';

  var PHONE = 780;
  function isPhone() { return window.innerWidth <= PHONE; }

  // The five places worth a thumb. Everything else lives under More, which is the existing
  // navigation, untouched.
  var TABS = [
    { href: '/private/overview',   icon: 'fa-binoculars',  label: 'Stand' },
    { href: '/private/spend-plan', icon: 'fa-map-signs',   label: 'Plan' },
    { href: '/progress',           icon: 'fa-tasks',       label: 'Candidates' },
    { href: '/filings',            icon: 'fa-file-signature', label: 'Filings' },
    { href: '#more',               icon: 'fa-ellipsis-h',  label: 'More' }
  ];

  function titleForPage() {
    var h = document.querySelector('.ov h4, h1, h2, h4.mb-0, .content h4');
    var t = (h && h.textContent.trim()) || document.title || '';
    return t.replace(/\s*[|·-]\s*CTEHR.*$/i, '').trim().slice(0, 40);
  }

  function buildAppBar() {
    if (document.querySelector('.appbar')) return;
    var bar = document.createElement('div');
    bar.className = 'appbar';

    // Back only where there is somewhere to go back to within the site.
    var top = ['/private/overview', '/', '/dashboard'];
    var here = location.pathname.replace(/\/$/, '') || '/';
    if (top.indexOf(here) === -1 && document.referrer.indexOf(location.host) !== -1) {
      var back = document.createElement('a');
      back.className = 'back';
      back.href = '#';
      back.setAttribute('aria-label', 'Back');
      back.innerHTML = '<i class="fas fa-chevron-left"></i>';
      back.onclick = function (e) { e.preventDefault(); history.back(); };
      bar.appendChild(back);
    }

    var ttl = document.createElement('span');
    ttl.className = 'ttl';
    var text = titleForPage();
    ttl.textContent = text;
    bar.appendChild(ttl);

    // The app bar now says what the page is, so the page saying it again directly underneath
    // is just a wasted first screen.
    var h = document.querySelector('.ov h4, h1, h2, h4.mb-0, .content h4');
    if (h && h.textContent.trim().slice(0, 40) === text) h.classList.add('appbar-dupe');

    document.body.insertBefore(bar, document.body.firstChild);
    document.body.classList.add('has-appbar');
  }

  function buildTabs() {
    if (document.querySelector('.apptabs')) return;
    var nav = document.createElement('nav');
    nav.className = 'apptabs';
    var here = location.pathname;

    TABS.forEach(function (t) {
      var a = document.createElement('a');
      a.href = t.href;
      // The spend plan owns several paths; the tab should stay lit across all of them.
      var on = t.href !== '#more' &&
               (here === t.href || here.indexOf(t.href + '/') === 0);
      if (on) a.className = 'on';
      a.innerHTML = '<i class="fas ' + t.icon + '"></i><span>' + t.label + '</span>';
      if (t.href === '#more') {
        a.onclick = function (e) {
          e.preventDefault();
          // Reuse the navigation that is already on the page rather than inventing a menu
          // that would then need keeping in step with it.
          var nb = document.querySelector('.navbar-collapse, #navbarNav');
          if (!nb) { location.href = '/'; return; }
          nb.classList.toggle('show');
          document.body.classList.toggle('more-open');
          if (nb.classList.contains('show')) {
            nb.style.cssText = 'display:block;position:fixed;left:0;right:0;bottom:58px;' +
              'top:auto;max-height:70vh;overflow:auto;background:#fff;z-index:1029;' +
              'box-shadow:0 -8px 28px rgba(0,0,0,.18);border-top:1px solid #dfe4ea;' +
              'padding:8px 12px calc(10px + env(safe-area-inset-bottom))';
            document.querySelector('.navbar').style.display = 'block';
          } else {
            nb.style.cssText = '';
            document.querySelector('.navbar').style.display = '';
          }
        };
      }
      nav.appendChild(a);
    });
    document.body.appendChild(nav);
  }

  /* Turn table rows into cards.
   *
   * Only where it helps. A table with a lot of columns becomes an unreadably tall card, and a
   * table of pure figures reads better as a grid you scroll than as a stack of labelled lines,
   * so those keep the horizontal scroller they already have.
   */
  function cardify() {
    document.querySelectorAll('table').forEach(function (t) {
      if (t.dataset.carded || t.closest('.navbar')) return;
      var head = t.querySelector('thead tr');
      var body = t.querySelector('tbody');
      if (!head || !body) return;
      var labels = Array.prototype.map.call(head.children, function (th) {
        return th.textContent.trim();
      });
      if (labels.length < 2 || labels.length > 9) return;   // too wide to read as a card
      if (body.rows.length === 0) return;

      Array.prototype.forEach.call(body.rows, function (tr) {
        Array.prototype.forEach.call(tr.cells, function (td, i) {
          if (!td.hasAttribute('data-label') && labels[i]) {
            td.setAttribute('data-label', labels[i]);
          }
        });
      });
      t.classList.add('ascard');
      t.dataset.carded = '1';

      // A carded table is no longer wide, so the scroller around it is just a box.
      var box = t.closest('.tscroll');
      if (box) box.classList.add('tscroll-off');
    });
  }

  function start() {
    if (!isPhone()) return;
    buildAppBar();
    buildTabs();
    cardify();
    if (window.MutationObserver) {
      var p = null;
      new MutationObserver(function () {
        clearTimeout(p);
        p = setTimeout(cardify, 150);
      }).observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  // Rotating a phone or opening on a tablet should land on the right layout.
  var rp = null;
  window.addEventListener('resize', function () {
    clearTimeout(rp);
    rp = setTimeout(function () {
      if (isPhone()) start();
    }, 250);
  });
})();
