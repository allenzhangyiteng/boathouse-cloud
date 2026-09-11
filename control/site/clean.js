const descriptions={viewer:'Open the app and view the data you have access to.',editor:'Open the app and update shared data in your browser.',developer:'With Admin access, your agent can change the code and publish updates.'};
const roleDescription=document.getElementById('role-description');
document.querySelectorAll('[data-role]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-role]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));roleDescription.textContent=descriptions[button.dataset.role];roleDescription.classList.remove('is-changing');void roleDescription.offsetWidth;roleDescription.classList.add('is-changing');}));
document.querySelectorAll('.mobile-menu nav a').forEach(link=>link.addEventListener('click',()=>link.closest('details').removeAttribute('open')));

// Keep continuous decoration controllable and stop work when it is not visible.
const heroMotion = document.querySelector('[data-hero-motion]');
if (heroMotion) {
  const toggle = heroMotion.querySelector('[data-motion-toggle]');
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const syncPreference = () => {
    heroMotion.classList.toggle('motion-ready', !preference.matches);
    toggle.hidden = preference.matches;
  };
  toggle.addEventListener('click', () => {
    const paused = heroMotion.classList.toggle('motion-paused');
    toggle.setAttribute('aria-pressed', String(paused));
    toggle.querySelector('span').textContent = paused ? 'Resume animation' : 'Pause animation';
  });
  const syncVisibility = () => heroMotion.classList.toggle('motion-background', document.hidden);
  document.addEventListener('visibilitychange', syncVisibility);
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      heroMotion.classList.toggle('motion-offscreen', !entries[0].isIntersecting);
    });
    observer.observe(heroMotion);
  }
  preference.addEventListener('change', syncPreference);
  syncVisibility();
  syncPreference();
}
