(() => {
  const burger = document.querySelector('[data-burger]');
  const menu = document.querySelector('.menu');
  if (burger && menu){
    burger.addEventListener('click', () => menu.classList.toggle('open'));
  }
  const y = document.querySelector('[data-year]');
  if (y) y.textContent = new Date().getFullYear();
})();