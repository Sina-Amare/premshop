// Keeps a focused control out from under the sticky header (WCAG 2.4.11, from 761px).
// Chromium does not scroll a control it counts as already in view, even when a sticky
// bar covers it, and its focus scroll ignores scroll-padding (input.css sets it for
// in-page jumps, where it does work). The scroll is instant, as the browser's own focus
// scroll is, and is measured again on the next frame, after anything else that moved
// the page on the same focus.
(function () {
  var top = document.querySelector('.site-header');
  var gap = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--spacing-4')) || 16;
  function clear(t) {
    if (!top || top.contains(t) || getComputedStyle(top).position !== 'sticky') return;
    var over = top.getBoundingClientRect().bottom + gap - t.getBoundingClientRect().top;
    if (over > 0) scrollBy({ top: -over, behavior: 'instant' });
  }
  var jumping = false;  // a jump target takes focus while it glides into place: leave it be
  document.addEventListener('focusin', function (e) {
    var t = e.target;
    if (jumping) return;
    // The category row scrolls sideways with no scrollbar, and Chromium leaves a focused
    // link that is partly visible where it is: bring all of it in, clear of the edge.
    if (t.closest && t.closest('.catnav__list')) t.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    clear(t);
    requestAnimationFrame(function () { if (document.activeElement === t) clear(t); });
  });

  // On a phone the row opens scrolled to its start; a current category past the edge
  // would show as a cut word, so the row scrolls sideways to it (never the page).
  var cur = document.querySelector('.catnav__link[aria-current="page"]');
  if (cur) {
    var row = cur.closest('.catnav__list'), a = cur.getBoundingClientRect(), l = row.getBoundingClientRect();
    if (a.left < l.left) row.scrollBy({ left: a.left - l.left - gap, behavior: 'instant' });
    else if (a.right > l.right) row.scrollBy({ left: a.right - l.right + gap, behavior: 'instant' });
  }

  // The home glint runs only while its wall is on screen: off it, every frame still
  // cost a style recalculation for nobody (.is-offscreen pauses it).
  var hero = document.querySelector('.hero');
  if (hero && 'IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      hero.classList.toggle('is-offscreen', !es[es.length - 1].isIntersecting);
    }).observe(hero);
  }

  // A product page's in-page jumps glide when motion is welcome; nothing else scrolls
  // smoothly. Focus follows the jump, as it would without script.
  var calm = matchMedia('(prefers-reduced-motion: no-preference)');
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.facts__row[href^="#"], a.invoice__terms[href^="#"], .toinvoice a[href^="#"]');
    if (!a || !calm.matches || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var target = document.getElementById(decodeURIComponent(a.hash.slice(1)));
    if (!target) return;
    e.preventDefault();
    history.pushState(null, '', a.hash);
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    jumping = true;
    target.focus({ preventScroll: true });
    jumping = false;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
})();
