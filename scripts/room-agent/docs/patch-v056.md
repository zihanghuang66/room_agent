# v0.5.6 — exact preview crops and centered sizing

The original paired PNG contains a render on the left and capture on the right. Setting an outer SVG viewBox does not clip the image to that viewBox's source rectangle: `xMidYMid meet` can leave horizontal letterboxes inside the viewport, and the adjacent half of the PNG can paint into that area. Full-width panels capped at 440px amplified this effect after v0.5.5 removed the page's maximum width. `overflow:hidden` on the outer SVG only clips its viewport, not the crop rectangle.

Each panel now has an explicit unique SVG clipPath in source coordinates matching the metadata rectangle, excluding the other image and composite header. The frame uses that crop's aspect ratio. The comparison viewer has a computed maximum width (crop ratio × image-height limit × number of columns) and automatic inline margins. On mobile it changes to one column with a smaller image-height limit. No original PNG, camera mapping, input, checkpoint, review, Blender or GLB file is modified.

Regression tests verify both source halves, two distinct files for Before/After, exact clips, unique IDs for multiple viewers, frame aspect ratio updates during arrow navigation, centered sizing, responsive column count, and the specific wide-viewport letterbox leakage. DOM/geometry tests establish these contracts; they are not a claim of live-browser visual acceptance. No change to backend Python or execution fingerprints.

Upgrade an idle server and open the existing completed task. Do not Resume or rerender for this display fix. Verify full-screen, narrower windows, sidebar toggles, both comparison modes and arrow navigation. Original downloads remain available.
