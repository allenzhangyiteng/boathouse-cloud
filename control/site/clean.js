const descriptions={viewer:'Open the app and view the data you have access to.',editor:'Open the app and update shared data in your browser.',developer:'With Admin access, your agent can change the code and publish updates.'};
const roleDescription=document.getElementById('role-description');
document.querySelectorAll('[data-role]').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('[data-role]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));roleDescription.textContent=descriptions[button.dataset.role];}));
document.querySelectorAll('.mobile-menu nav a').forEach(link=>link.addEventListener('click',()=>link.closest('details').removeAttribute('open')));
