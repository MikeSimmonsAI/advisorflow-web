'use strict';
(() => {
  const toggle = document.querySelector('.mobile-toggle');
  const nav = document.getElementById('nav-menu');
  const form = document.getElementById('request-search');
  const category = document.getElementById('category');
  const locationInput = document.getElementById('location');
  const contactCta = document.getElementById('contact-cta');
  const support = 'support@bookaboost.live';
  const mailto = (subject, body) => `mailto:${support}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  if (toggle && nav) {
    const closeMenu = () => { nav.classList.remove('is-open'); toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Open navigation'); };
    toggle.addEventListener('click', () => {
      const open = !nav.classList.contains('is-open');
      nav.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    });
    nav.querySelectorAll('a').forEach(a => a.addEventListener('click', closeMenu));
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  }
  if (form) form.addEventListener('submit', e => {
    e.preventDefault();
    const wantedCategory = category.value.trim();
    const wantedLocation = locationInput.value.trim();
    if (!wantedCategory) { category.focus(); category.setCustomValidity('Please select a property category.'); category.reportValidity(); return; }
    if (!wantedLocation) { locationInput.focus(); locationInput.setCustomValidity('Please enter a city, county, state or ZIP.'); locationInput.reportValidity(); return; }
    const body = `Hello BookaBoost,\n\nI'd like help finding overlooked properties.\nProperty category: ${wantedCategory}\nLocation: ${wantedLocation}\n\nPlease let me know what is available and how to get started.\n\nThank you.`;
    window.location.href = mailto('BookaBoost property opportunity search request', body);
  });
  category?.addEventListener('change', () => category.setCustomValidity(''));
  locationInput?.addEventListener('input', () => locationInput.setCustomValidity(''));
  document.querySelectorAll('[data-category]').forEach(link => {
    link.addEventListener('click', () => {
      const choice = link.getAttribute('data-category');
      if (category && choice) category.value = choice;
      if (contactCta && choice) contactCta.href = mailto('BookaBoost opportunity request: ' + choice, 'Hello BookaBoost,\n\nI am interested in ' + choice + '. Please tell me how to get started.\n');
    });
  });
  const year = document.getElementById('year');
  if (year) year.textContent = String(new Date().getFullYear());
})();