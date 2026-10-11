# 3D aircraft models

`737-800.glb` (not committed) is a third-party Boeing 737-800 model from Sketchfab. Its licence has not been confirmed
and the download carries no licence file, so it is git-ignored. The app falls back to the built-in procedural model when it is absent.

To use it locally, copy the file in:

    cp "<path to uploads_files_3116828_PLANE.glb>" web/public/models/737-800.glb

Commit it only after checking the licence on the model's Sketchfab page (and crediting the author if it requires attribution).
