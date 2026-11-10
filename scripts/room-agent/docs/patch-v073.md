# v0.7.3 — download output identity and layout

The prior portable-only filter exposed an older indexing defect: render directories rank later than build directories but contain only disposable Room.blend/Room.glb. Export validation writes Room.portable.blend into the build room_preview. A historical diagnostic for 28066a confirms both locations and the render request binding.

Saved-scene and edit presentations now follow the latest valid comparison's render request state.preview to an already indexed same-run room group. No cross-run search, regeneration, rename, history fallback or scene mutation. If that binding is absent/invalid, existing selection stays in place.

Files has one horizontal 84×34px button row: Blender, GLB, Diagnostics, all with identical typography. Blender is portable-only; its filename remains the download and tooltip. Full archive stays under More details.

Execution engine files/hashes unchanged from v0.7.2. Existing tasks need only a web server restart and refresh.
