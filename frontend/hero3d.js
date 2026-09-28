// Hero background: the company's sector models (or the construction workers),
// drawn behind the hero copy. Source for static/js/hero3d.js -- rebuild with
//   npx esbuild frontend/hero3d.js --bundle --minify --format=esm --outfile=static/js/hero3d.js
// (three and esbuild installed anywhere outside the repo; nothing is vendored
// but the tree-shaken bundle). motion.js imports it only after window load, so
// it never competes with the page for bandwidth. Models come from
// frontend/compress-model.mjs; which ones a page shows is HERO_MODELS in
// app/web.py.
//
// Two layouts:
// - scene: one model with a ground colour (data-bg), e.g. workers.glb. The
//   canvas clears to that colour so the ground reads as endless, and the camera
//   looks down on it across the whole hero.
// - objects: everything else, also behind the copy. Each model is scaled to
//   the same size and they sit in a row across the hero, each spinning on its
//   own pivot.
import {
  WebGLRenderer, Scene, PerspectiveCamera, HemisphereLight, DirectionalLight,
  AnimationMixer, Box3, Vector3, Timer, Group,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';

const CELL = 2.2;   // spacing of models normalised to radius 1

export default async function start(canvas) {
  const urls = JSON.parse(canvas.dataset.models);
  const bg = canvas.dataset.bg;
  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: devicePixelRatio < 2 });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  // The colour the scene model's flat ground renders at under these lights
  // (measured, see HERO_MODELS). Re-measure if the lights change.
  if (bg) renderer.setClearColor(bg, 1);

  const scene = new Scene();
  scene.add(new HemisphereLight(0xffffff, 0x444444, 2.2));
  const sun = new DirectionalLight(0xffffff, 2);
  sun.position.set(3, 5, 4);
  scene.add(sun);

  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const gltfs = await Promise.all(urls.map((u) => loader.loadAsync(u)));

  // Centre every model on its own pivot. Objects are also scaled so each fits
  // a cylinder of radius 1 and height 2 -- the shape a model sweeps as it
  // spins about y -- with its longer side touching, so none looks small.
  const pivots = gltfs.map((g, i) => {
    const box = new Box3().setFromObject(g.scene);
    const size = box.getSize(new Vector3());
    const r = size.length() / 2;
    g.scene.position.sub(box.getCenter(new Vector3()));
    const pivot = new Group();
    pivot.add(g.scene);
    const k = 2 / Math.max(size.y, Math.hypot(size.x, size.z));
    if (!bg) pivot.scale.setScalar(k);
    pivot.userData.h = size.y * k;
    pivot.rotation.y = i * 1.3;   // out of phase, so they do not spin in lockstep
    pivot.userData.r = r;
    scene.add(pivot);
    return pivot;
  });
  const mixers = gltfs.map((g) => {
    const m = new AnimationMixer(g.scene);
    for (const clip of g.animations) m.clipAction(clip).play();
    return m;
  });

  const camera = new PerspectiveCamera(35, 1, 0.01, 1000);
  const fit = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    const half = Math.tan(camera.fov * Math.PI / 360);   // tan of half the vertical fov
    let d, tilt, look = 0;
    if (bg) {
      // Look down 45deg from far enough to see the whole scene; a narrow
      // (phone) hero backs off so it still fits the width.
      const r = pivots[0].userData.r;
      d = r * 1.3 * Math.max(1, 1 / camera.aspect);
      tilt = 45;
      look = -r * 0.2;
      camera.near = r / 100;
      camera.far = r * 20;
    } else {
      // One row, then back off until it fits both ways, plus a radius of
      // depth. Seen from `tilt` above, a cylinder of height h and radius 1
      // stands h*cos + 2*sin tall on screen.
      const n = pivots.length;
      pivots.forEach((p, i) => p.position.set((i - (n - 1) / 2) * CELL, 0, 0));
      tilt = 20;
      const t = tilt * Math.PI / 180;
      const tall = Math.max(...pivots.map((p) => p.userData.h)) * Math.cos(t) + 2 * Math.sin(t);
      d = Math.max(tall / 2 / half, n * CELL / 2 / (half * camera.aspect)) + 1;
      camera.near = 0.01;
      camera.far = d + 10;
    }
    const a = tilt * Math.PI / 180;
    camera.position.set(0, Math.sin(a) * d, Math.cos(a) * d);
    camera.lookAt(0, look, 0);
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(fit).observe(canvas);
  fit();

  // Only animate while the canvas is on screen; the rAF loop stops otherwise.
  const timer = new Timer();
  let on = false;
  const tick = (t) => {
    if (!on) return;
    timer.update(t);
    const dt = timer.getDelta();
    for (const m of mixers) m.update(dt);
    for (const p of pivots) p.rotation.y += dt * (bg ? 0.08 : 0.25);
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  };
  new IntersectionObserver(([e]) => {
    const was = on;
    on = e.isIntersecting;
    if (on && !was) { timer.reset(); requestAnimationFrame(tick); }
  }).observe(canvas);

  canvas.classList.add('on');
}
