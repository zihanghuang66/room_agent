"""Paired-image crops must not leak their neighbour into SVG letterboxing."""
from test_dashboard_stage3 import HTML, run_js


def test_each_panel_has_its_own_exact_crop_clip_and_matching_aspect_ratio():
    run_js(r'''
const row={path:'pair.png',width:1088,height:754,captured:[548,34,540,720],rendered:[0,34,540,720]};
const viewer=makePreviewViewer([row,{...row,path:'wide.png',width:2568,captured:[1288,34,1280,720],rendered:[0,34,1280,720]}],'crop');
let nodes=flat(viewer),images=nodes.filter(n=>n.tag==='image'),clips=nodes.filter(n=>n.tag==='clipPath');
assert.equal(clips.length,2);assert.notEqual(clips[0].attrs.id,clips[1].attrs.id);
for(let i=0;i<2;i++){
 const box=i===0?row.captured:row.rendered,rect=flat(clips[i]).find(n=>n.tag==='rect');
 assert.equal(clips[i].attrs.clipPathUnits,'userSpaceOnUse');
 assert.deepEqual(['x','y','width','height'].map(k=>Number(rect.attrs[k])),box);
 assert.equal(images[i].attrs['clip-path'],'url(#'+clips[i].attrs.id+')');
}
const frames=nodes.filter(n=>n.className==='preview-frame');
assert(frames.every(n=>n.style.aspectRatio==='540 / 720'));
assert.match(viewer.style.maxWidth,/0\.75/);
nodes.find(n=>n.className==='preview-next').onclick();
assert(frames.every(n=>n.style.aspectRatio==='1280 / 720'));
assert.equal(Number(flat(clips[0]).find(n=>n.tag==='rect').attrs.x),1288);
const other=makePreviewViewer([row],'other');
assert(!flat(other).filter(n=>n.tag==='clipPath').some(n=>clips.some(c=>c.attrs.id===n.attrs.id)));
''')


def test_before_after_clips_left_render_from_two_different_files():
    run_js(r'''
const row={path:'after.png',before_path:'before.png',width:1088,height:754,captured:[0,34,540,720],rendered:[0,34,540,720]};
const viewer=makePreviewViewer([row],'edit-crop',['Before','After']);
const nodes=flat(viewer),images=nodes.filter(n=>n.tag==='image');
assert(images[0].attrs.href.includes('before.png'));
assert(images[1].attrs.href.includes('after.png'));
const rects=nodes.filter(n=>n.tag==='rect');
assert.equal(rects.length,2);assert(rects.every(n=>n.attrs.x==='0'&&n.attrs.width==='540'));
''')


def test_preview_is_compact_centered_and_responsive_without_fixed_portrait_ratio():
    css=HTML.read_text().split('</style>')[0]
    assert '.preview-viewer{--preview-height:440px;--preview-columns:2;width:100%;margin-inline:auto;' in css
    assert '.preview-pair .preview-frame{max-height:none;' in css
    assert '.preview-viewer{--preview-height:360px;--preview-columns:1}' in css


def test_wide_viewport_cannot_show_neighbour_pixels_even_during_resize():
    run_js(r'''
const row={path:'pair.png',width:1088,height:754,captured:[548,34,540,720],rendered:[0,34,540,720]};
const viewer=makePreviewViewer([row],'resize'),clips=flat(viewer).filter(n=>n.tag==='clipPath');
const rectangles=clips.map(c=>flat(c).find(n=>n.tag==='rect')).map(r=>['x','y','width','height'].map(k=>Number(r.attrs[k])));
const inside=(rect,x,y)=>x>=rect[0]&&x<rect[0]+rect[2]&&y>=rect[1]&&y<rect[1]+rect[3];
for(const viewportWidth of [540,860,1600]){
 const scale=Math.min(viewportWidth/540,440/720),offset=(viewportWidth-540*scale)/2;
 // The uncropped right half of the PNG lies inside a wide SVG viewport's
 // letterbox. It must still be excluded by the source-coordinate clip.
 const leakedX=offset+600*scale;
 if(leakedX<viewportWidth)assert(!inside(rectangles[1],600,300));
 assert(inside(rectangles[1],270,300));
 assert(!inside(rectangles[0],270,300));assert(inside(rectangles[0],818,300));
 assert(!inside(rectangles[1],270,10)); // original composite header is excluded
}
''')
