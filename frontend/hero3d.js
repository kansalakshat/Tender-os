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
  AnimationMixer, Box3, Vector3, Timer,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

export default async function start(canvas) {
  const hero = canvas.parentElement;
  // 1x pixels, no antialias: it sits at 35% opacity, so extra resolution is
  // invisible and only costs GPU time the scroll needs.
  const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: false,
                                       powerPreference: 'low-power' });
  renderer.setPixelRatio(1);

  const scene = new Scene();
  scene.add(new HemisphereLight(0xffffff, 0x444444, 2.2));
  const sun = new DirectionalLight(0xffffff, 2);
  sun.position.set(3, 5, 4);
  scene.add(sun);

  const gltf = await new GLTFLoader().loadAsync(canvas.dataset.model);
  const model = gltf.scene;
  scene.add(model);

  // Frame whatever the model's native scale is: centre it, back the camera off
  // far enough to fit its bounding sphere, look down a little.
  const box = new Box3().setFromObject(model);
  const r = box.getSize(new Vector3()).length() / 2;
  model.position.sub(box.getCenter(new Vector3()));
  const camera = new PerspectiveCamera(35, 1, r / 100, r * 20);
  camera.position.set(r * 1.6, r * 0.9, r * 2.2);
  camera.lookAt(0, -r * 0.1, 0);

  const mixer = new AnimationMixer(model);
  for (const clip of gltf.animations) mixer.clipAction(clip).play();

  const fit = () => {
    const w = hero.clientWidth, h = hero.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(fit).observe(hero);
  fit();

  // Only animate while the hero is on screen; the rAF loop stops otherwise.
  // Draws at most 30 fps and not at all mid-scroll, so a scrolling frame never
  // waits on WebGL. The timer keeps running, so it resumes without a jump.
  const timer = new Timer();
  let on = false, last = 0, scrolling = 0;
  addEventListener('scroll', () => {
    clearTimeout(scrolling);
    scrolling = setTimeout(() => { scrolling = 0; }, 150);
  }, { passive: true });
  const tick = (t) => {
    if (!on) return;
    requestAnimationFrame(tick);
    if (scrolling || t - last < 33) return;
    timer.update(t);
    const dt = Math.min(timer.getDelta(), 0.1);
    last = t;
    mixer.update(dt);
    model.rotation.y += dt * 0.08;
    renderer.render(scene, camera);
  };
  new IntersectionObserver(([e]) => {
    const was = on;
    on = e.isIntersecting;
    if (on && !was) { timer.reset(); requestAnimationFrame(tick); }
  }).observe(hero);

  canvas.classList.add('on');
}
