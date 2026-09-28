// Shrink a downloaded .glb into a hero model under static/models:
//   node compress-model.mjs <in.glb> <repo>/static/models/<name>.glb [keep]
// keep = share of triangles to keep, default 0.25; use 1 when simplifying
// visibly eats detail (chip.glb did). Node resolves imports next to the script,
// so copy it into a scratch folder and there
//   npm i @gltf-transform/core@4 @gltf-transform/extensions@4 @gltf-transform/functions@4 sharp meshoptimizer
// Nothing is installed in the repo. tests/test_web.py fails any model over
// MODEL_BUDGET, so a heavy one cannot slow the home page down unnoticed.
//
// Then add it to HERO_MODELS in app/web.py and eyeball it on the home page.
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import {
  prune, dedup, resample, flatten, join, textureCompress, weld, simplify, meshopt,
} from '@gltf-transform/functions';
import { MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import sharp from 'sharp';
import { statSync } from 'node:fs';

const [src, dst, keep = '0.25'] = process.argv.slice(2);
await MeshoptEncoder.ready;
await MeshoptSimplifier.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder });
const doc = await io.read(src);
const root = doc.getRoot();

// Normal maps are invisible at hero scale and were most of the texture bytes.
for (const m of root.listMaterials()) m.setNormalTexture(null);
// Transmission (glass) costs an extra render pass every frame, and with no
// scene behind it to refract it drew the medical kit's bottles invisible.
for (const e of root.listExtensionsUsed())
  if (e.extensionName === 'KHR_materials_transmission') e.dispose();

const skinned = root.listSkins().length > 0;
await doc.transform(
  prune(), dedup(), resample(), flatten(), join(),
  textureCompress({ encoder: sharp, targetFormat: 'webp', resize: [256, 256], quality: 70 }),
  weld(),
  // error caps how far the simplifier may move a surface (share of the model's
  // size), so it stops short of `keep` rather than wreck a shape.
  ...(+keep < 1 && !skinned
    ? [simplify({ simplifier: MeshoptSimplifier, ratio: +keep, error: 0.005 })] : []),
  prune(),
  // Quantizes and packs geometry and animation (EXT_meshopt_compression);
  // hero3d.js decodes it with three's MeshoptDecoder.
  meshopt({ encoder: MeshoptEncoder, level: 'high' }),
);
await io.write(dst, doc);
console.log(`${dst}: ${(statSync(dst).size / 1024).toFixed(0)} KB`);
