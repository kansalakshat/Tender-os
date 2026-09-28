// Hero background: the animated construction-workers model, drawn behind the
// hero copy. Source for static/js/hero3d.js -- rebuild after editing with
//   npx esbuild frontend/hero3d.js --bundle --minify --format=esm --outfile=static/js/hero3d.js
// (three and esbuild installed anywhere outside the repo; nothing is vendored
// but the tree-shaken bundle). motion.js imports it only after window load, so
// it never competes with the page for bandwidth.
//
// static/models/workers.glb is low-poly_construction_workers_animated.glb run
// through gltf-transform: normal maps dropped, prune, dedup, resample,
// textures 256px WebP, weld, quantize. 4.5 MB -> 288 KB, ~170 KB gzipped.
import {
  WebGLRenderer, Scene, PerspectiveCamera, HemisphereLight, DirectionalLight,
  AnimationMixer, Box3, Vector3, Timer, Group,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

export default async function start(canvas) {
  const hero = canvas.parentElement;
  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: devicePixelRatio < 2 });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));

  const scene = new Scene();
  scene.add(new HemisphereLight(0xffffff, 0x444444, 2.2));
  const sun = new DirectionalLight(0xffffff, 2);
  sun.position.set(3, 5, 4);
  scene.add(sun);

  const gltf = await new GLTFLoader().loadAsync(canvas.dataset.model);
  const model = gltf.scene;
  // Fill the whole hero like background-size:cover: look steeply down (55deg)
  // so the ground plane, not empty sky, is behind the copy. The model spins on
  // a pivot at its centre, so the ground's corners swing; the distances below
  // were measured to keep them off-screen through a full turn.
  const box = new Box3().setFromObject(model);
  const r = box.getSize(new Vector3()).length() / 2;
  model.position.sub(box.getCenter(new Vector3()));
  const pivot = new Group();
  pivot.add(model);
  scene.add(pivot);
  const camera = new PerspectiveCamera(35, 1, r / 100, r * 20);
  const tilt = 55 * Math.PI / 180;

  const mixer = new AnimationMixer(model);
  for (const clip of gltf.animations) mixer.clipAction(clip).play();

  const fit = () => {
    const w = hero.clientWidth, h = hero.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // wider hero -> wider view -> come closer so the ground still spans it
    const d = r * Math.min(0.62, 1 / camera.aspect);
    camera.position.set(0, Math.sin(tilt) * d, Math.cos(tilt) * d);
    camera.lookAt(0, -r * 0.2, 0);
  };
  new ResizeObserver(fit).observe(hero);
  fit();

  // Only animate while the hero is on screen; the rAF loop stops otherwise.
  const timer = new Timer();
  let on = false;
  const tick = (t) => {
    if (!on) return;
    timer.update(t);
    const dt = timer.getDelta();
    mixer.update(dt);
    pivot.rotation.y += dt * 0.08;
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  };
  new IntersectionObserver(([e]) => {
    const was = on;
    on = e.isIntersecting;
    if (on && !was) { timer.reset(); requestAnimationFrame(tick); }
  }).observe(hero);

  canvas.classList.add('on');
}
