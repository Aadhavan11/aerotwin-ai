# Third-party assets

## 3D aircraft model

This work is based on "B737-800 Model" (https://sketchfab.com/3d-models/b737-800-model-6cbe380405794ea69d6b0a3d144dd1af) by hikami3150 (https://sketchfab.com/hikami3150) licensed under CC-BY-4.0 (http://creativecommons.org/licenses/by/4.0/)

- **Files:** `web/public/models/b737-800/` (`scene.gltf`, `scene.bin`) and the author's own credit text in `LICENSE-AND-CREDIT.txt` next to them.
- **Used for:** the aircraft viewer on the Aircraft page. It is the only aircraft geometry in the app. Every tail is drawn with it; A320-class tails are labeled "Drawn with a Boeing 737-800 model" on screen.
- **Changes we made (CC BY asks us to say so):** the model is scaled uniformly to the published length of the aircraft type, turned 180 degrees about the vertical axis to face the scene's nose direction, and lit by the app's own lights. At run time the engine and landing-gear pieces are tinted and made to glow by system status (red alert, amber watch, blue selected). The geometry is not edited.
- **Credit in the app:** a link under the viewer's caption.
- **Not ours, and the author is not responsible for how we use it.** It is illustrative: it does not represent any specific airframe or configuration, and every aircraft in the app is simulated.

## Data
NASA C-MAPSS turbofan degradation data (FD001), downloaded at setup and not committed; see `docs/DATA-SOURCES.md`.

## Fonts
Geist and Geist Mono (SIL Open Font License 1.1), bundled from the `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` npm packages.
