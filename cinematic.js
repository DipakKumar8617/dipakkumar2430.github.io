(() => {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false;
  const journey = document.querySelector('#rq-cinematic-journey');
  const scenes = journey ? [...journey.querySelectorAll('.rq-journey-image')] : [];
  const kicker = journey?.querySelector('.rq-journey-kicker');
  const heading = journey?.querySelector('.rq-journey-copy h3');
  const paragraph = journey?.querySelector('.rq-journey-copy p');
  const count = journey?.querySelector('.rq-scene-count');
  const progress = journey?.querySelector('.rq-journey-progress span');

  const copy = [
    ['01 ? THE ARRIVAL', 'Come home<br>to something<br><em>extraordinary.</em>', 'Architecture that welcomes you before you even step inside.'],
    ['02 ? THE WELCOME', 'A warmer kind<br>of <em>welcome.</em>', 'Thoughtful details. A calm arrival. Space to slow down.'],
    ['03 ? THE ASCENT', 'A little closer<br>to <em>your world.</em>', 'A private transition from the everyday to somewhere entirely yours.'],
    ['04 ? THE FEELING', 'Room to live<br><em>beautifully.</em>', 'Light, proportion and comfort ? the details that make a place yours.']
  ];

  function setScene(n, updateImages = true) {
    if (updateImages) {
      scenes.forEach((el, i) => {
        el.style.opacity = i === n ? '1' : '0';
        el.style.transform = i === n ? 'scale(1)' : 'scale(1.08)';
      });
    }
    if (kicker) kicker.textContent = copy[n][0];
    if (heading) heading.innerHTML = copy[n][1];
    if (paragraph) paragraph.textContent = copy[n][2];
    if (count) count.innerHTML = `0${n + 1} <i>/</i> 04`;
    if (progress) progress.style.width = `${(n + 1) * 25}%`;
  }

  if (journey) {
    setScene(0);

    if (!reduced && window.gsap && window.ScrollTrigger) {
      gsap.registerPlugin(ScrollTrigger);
      const mm = gsap.matchMedia();

      mm.add('(min-width: 761px)', () => {
        const stage = journey.querySelector('.rq-journey-stage');
        const timeline = gsap.timeline({
          scrollTrigger: {
            trigger: stage,
            start: 'top top',
            end: '+=2600',
            scrub: 1,
            pin: true,
            anticipatePin: 1,
            onUpdate: self => {
              setScene(Math.min(3, Math.floor(self.progress * 4)), false);
            }
          }
        });

        gsap.set(scenes, { opacity: 0, scale: 1.08 });
        if (scenes[0]) gsap.set(scenes[0], { opacity: 1, scale: 1 });

        for (let i = 1; i < scenes.length; i++) {
          timeline.to(scenes[i - 1], {
            opacity: 0,
            scale: 1.035,
            duration: 0.8
          }, i);

          timeline.fromTo(scenes[i],
            { opacity: 0, scale: 1.08 },
            { opacity: 1, scale: 1, duration: 0.8 },
            i
          );
        }

        return () => {
          timeline.scrollTrigger?.kill();
          timeline.kill();
        };
      });

      mm.add('(max-width: 760px)', () => {
        const stage = journey.querySelector('.rq-journey-stage');
        const trigger = ScrollTrigger.create({
          trigger: stage,
          start: 'top 65%',
          end: 'bottom 35%',
          onUpdate: self => {
            setScene(Math.min(3, Math.floor(self.progress * 4)));
          }
        });
        return () => trigger.kill();
      });
    }
  }

  document.querySelectorAll(
    '.rq-reveal,.rq-content-wrap .role-card,.rq-content-wrap .listing-card,.rq-content-wrap #roles .section-head,.rq-content-wrap #listings .section-head'
  ).forEach(el => {
    if (el.dataset.rqRevealBound) return;
    el.dataset.rqRevealBound = '1';

    if (reduced || !('IntersectionObserver' in window)) {
      el.classList.add('rq-visible');
      return;
    }

    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('rq-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });

    observer.observe(el);
  });

  const menu = document.querySelector('.rq-menu-toggle');
  const nav = document.querySelector('.rq-nav-links');

  if (menu && nav) {
    menu.addEventListener('click', () => {
      const open = nav.classList.toggle('rq-nav-open');
      menu.setAttribute('aria-expanded', String(open));
    });

    nav.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        nav.classList.remove('rq-nav-open');
        menu.setAttribute('aria-expanded', 'false');
      });
    });
  }

  if (!reduced && window.gsap && document.querySelector('.rq-hero-image')) {
    gsap.to('.rq-hero-image', {
      yPercent: 8,
      ease: 'none',
      scrollTrigger: {
        trigger: '.rq-hero',
        start: 'top top',
        end: 'bottom top',
        scrub: true
      }
    });
  }

  const loginLink = document.querySelector('.rq-login-link');
  const registerLink = document.querySelector('.rq-register-link');
  const logoutButton = document.querySelector('.rq-logout-link');

  async function syncNavigationSession() {
    try {
      const response = await fetch('/api/session');
      if (!response.ok || !(await response.json()).authenticated) return;
      loginLink.hidden = true;
      registerLink.textContent = 'Dashboard';
      registerLink.href = '/dashboard';
      logoutButton.hidden = false;
    } catch (error) {
      console.error('Session check failed:', error);
    }
  }

  logoutButton?.addEventListener('click', async () => {
    logoutButton.disabled = true;
    try {
      await fetch('/api/logout', { method: 'POST' });
    } finally {
      window.location.href = '/';
    }
  });

  if (loginLink && registerLink && logoutButton) syncNavigationSession();

})();
