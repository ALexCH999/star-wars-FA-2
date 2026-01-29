(() => {
  const canvas = document.getElementById('starfield');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let w, h, stars = [];
  const MODE = canvas.dataset.mode || document.body.className || '';

  // Global tuning by mode
  let NUM_STARS = 200;
  let BASE_SPEED = 0.2;
  // COLOR is an RGB string, used in rgba(...)
  let COLOR = '255,255,255';
  let TWINKLE = false;
  let TWINKLE_INTENSITY = 0.6;
  // chance per animation frame to spawn a cross glow somewhere (very rare)
  let CROSS_GLOBAL_CHANCE = 0.002;

  if (MODE.includes('index-slow')) {
    NUM_STARS = 180;
    BASE_SPEED = 0.2; // slower on home
    TWINKLE = false;
    COLOR = '220,230,255';
    CROSS_GLOBAL_CHANCE = 0.0005;
  } else if (MODE.includes('dark-twinkle')) {
    // much darker, with occasional cross-shaped emission
    NUM_STARS = 160;
    BASE_SPEED = 0.08;
    TWINKLE = true;
    TWINKLE_INTENSITY = 0.55;
    COLOR = '200,220,255'; // soft bluish base for stars
    CROSS_GLOBAL_CHANCE = 0.001; // rare global activation
  } else if (MODE.includes('admin')) {
    // admin mode: dark + red stars (explicitly red)
    NUM_STARS = 140;
    BASE_SPEED = 0.1;
    TWINKLE = true;
    TWINKLE_INTENSITY = 0.5;
    // a range of red-ish tones (we'll pick per-star hue variation)
    COLOR = null; // null signals per-star red variance (handled below)
    CROSS_GLOBAL_CHANCE = 0.0015;
  } else {
    // default / fallback
    NUM_STARS = 200;
    BASE_SPEED = 0.25;
    TWINKLE = false;
    COLOR = '255,255,255';
    CROSS_GLOBAL_CHANCE = 0.0008;
  }

  function resize(){
    // Используем несколько способов для кроссбраузерности
    w = canvas.width = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth;
    h = canvas.height = window.innerHeight || document.documentElement.clientHeight || document.body.clientHeight;
    // Убеждаемся, что canvas покрывает весь экран
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.position = 'fixed';
    canvas.style.top = '0';
    canvas.style.left = '0';
  }

  function init(){
    stars = [];
    // Убеждаемся, что размеры установлены перед созданием звёзд
    if (w === 0 || h === 0) {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    }
    for(let i=0;i<NUM_STARS;i++){
      // each star keeps its own color (for admin mode it will be red-range)
      const perStarColor = COLOR ? COLOR : (
        // admin: random red-ish tone (more natural)
        `${220 + Math.round(Math.random()*35)},${30 + Math.round(Math.random()*60)},${30 + Math.round(Math.random()*40)}`
      );

      stars.push({
        x: Math.random()*w,
        y: Math.random()*h,
        z: Math.random()*1.4 + 0.2,
        phase: Math.random()*Math.PI*2,   // twinkle phase
        twinkleSpeed: 0.002 + Math.random()*0.006,
        color: perStarColor,
        // cross fields
        crossActive: false,
        crossTimer: 0,
        crossDuration: 0
      });
    }
  }

  // Activate a random star's cross emission
  function maybeSpawnCross(){
    if (Math.random() < CROSS_GLOBAL_CHANCE) {
      // pick a random star
      const i = Math.floor(Math.random() * stars.length);
      const s = stars[i];
      // only activate if not already active
      if (!s.crossActive) {
        s.crossActive = true;
        s.crossDuration = 20 + Math.floor(Math.random()*40); // frames (20..60)
        s.crossTimer = 0;
      }
    }
  }

  function render(){
    ctx.clearRect(0,0,w,h);

    // Rarely spawn cross emissions
    maybeSpawnCross();

    for(let s of stars){
      // movement
      s.x -= s.z * BASE_SPEED;
      if (s.x < -12) s.x = w + Math.random()*50;
      // Если звезда вышла за пределы по Y, перемещаем её обратно
      if (s.y < 0) s.y = h;
      if (s.y > h) s.y = 0;

      let baseAlpha = 0.55 * s.z;
      if (TWINKLE) {
        // soft twinkle modulation
        baseAlpha = baseAlpha * (0.6 + 0.4 * Math.abs(Math.sin(s.phase)));
        s.phase += s.twinkleSpeed * (1 + Math.random()*0.8);
      }

      // small star core
      const coreSize = Math.max(0.8, s.z * 1.4);
      ctx.beginPath();
      ctx.fillStyle = `rgba(${s.color},${Math.max(0.06, baseAlpha)})`;
      ctx.arc(s.x, s.y, coreSize, 0, Math.PI*2);
      ctx.fill();

      // faint halo for depth (very subtle)
      ctx.beginPath();
      ctx.fillStyle = `rgba(${s.color},${0.02 * s.z})`;
      ctx.arc(s.x, s.y, coreSize*3, 0, Math.PI*2);
      ctx.fill();

      // If cross is active for this star, draw cross-shaped emission
      if (s.crossActive) {
        // progress 0..1
        s.crossTimer++;
        const p = s.crossTimer / s.crossDuration;
        // crossAlpha peaks near start then decays (ease out)
        const crossAlpha = (1 - p) * 0.9 * (0.9 + Math.random()*0.2);
        // sizes scale with depth z
        const crossLen = 8 + s.z * 18; // length half
        const crossWidth = Math.max(1, 0.6 + s.z * 1.8);

        // draw horizontal bar
        ctx.save();
        ctx.translate(s.x, s.y);
        ctx.globalAlpha = Math.max(0, crossAlpha);
        // soft rectangular bars (horizontal)
        ctx.fillStyle = `rgba(${s.color},1)`;
        ctx.fillRect(-crossLen, -crossWidth/2, crossLen*2, crossWidth);
        // vertical bar
        ctx.fillRect(-crossWidth/2, -crossLen, crossWidth, crossLen*2);
        ctx.restore();

        // small extra glow in center
        ctx.beginPath();
        ctx.fillStyle = `rgba(${s.color},${crossAlpha * 0.45})`;
        ctx.arc(s.x, s.y, crossLen*0.7, 0, Math.PI*2);
        ctx.fill();

        if (s.crossTimer >= s.crossDuration) {
          s.crossActive = false;
          s.crossTimer = 0;
          s.crossDuration = 0;
        }
      }
    }

    requestAnimationFrame(render);
  }

  window.addEventListener('resize', ()=>{ resize(); init(); });
  
  // Инициализация - используем несколько подходов для гарантии правильных размеров
  function start() {
    // Устанавливаем размеры canvas
    resize();
    // Небольшая задержка для гарантии правильных размеров
    requestAnimationFrame(() => {
      resize(); // Повторно устанавливаем размеры
      init();
      render();
    });
  }
  
  // Пробуем инициализировать сразу, если DOM готов
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    start();
  } else {
    // Ждём полной загрузки страницы
    window.addEventListener('load', start);
    // Также пробуем при DOMContentLoaded
    document.addEventListener('DOMContentLoaded', start);
  }
})();
