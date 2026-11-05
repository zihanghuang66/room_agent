import copy,json,math,os,sys,unittest
from pathlib import Path
APP=Path(os.environ.get('ROOM_TEST_APP',str(Path(__file__).resolve().parents[1]/'litereality-full-v0.3.1/app')));sys.path.insert(0,str(APP/'src'))
from litereality_agent.pipeline.scene_init.layout import adapter
from litereality_agent.pipeline.scene_init.layout.adjust import check
ROOT=Path(__file__).resolve().parents[2]
CAPTURE=Path(os.environ['ROOM_TEST_CAPTURE']) if os.environ.get('ROOM_TEST_CAPTURE') else None

def shell(thickness=.0001):
 return {'walls':{'boundary':{'start':[0,0],'end':[4,0],'thickness':thickness,'height':3}},
  'objects':{'cabinet_any_id':{'category':'storage','center':[2,.04,1.25],'size':[1.6,.08,2.5],'yaw':0}},
  'openings':{},'floor_z':0,'ceiling_z':3,
  'floor':{'verts':[[0,0,0],[4,0,0],[4,4,0],[0,4,0]],'faces':[[0,1,2],[0,2,3]]}}

def errors(s):return [v for v in check(s) if v.severity=='error']

class WallFacade(unittest.TestCase):
 @unittest.skipUnless(CAPTURE is not None, 'Optional real scan fixture: set ROOM_TEST_CAPTURE; raw data is not in Git')
 def test_raw_thickness_not_replaced_by_16cm(self):
  s=adapter.shell_from_scene_data(CAPTURE)
  self.assertAlmostEqual(s['walls']['Wall1']['thickness'],.0001,places=6)
 @unittest.skipUnless(CAPTURE is not None, 'Optional real scan fixture: set ROOM_TEST_CAPTURE; raw data is not in Git')
 def test_current_scene_layout_passes_without_moving_wardrobe(self):
  s=adapter.shell_from_scene_data(CAPTURE)
  self.assertEqual(errors(s),[])
  old=json.loads((CAPTURE/'layout_shell.json').read_text())['objects']['Storage0']
  self.assertEqual(s['objects']['Storage0']['center'],old['center'])
  self.assertEqual(s['objects']['Storage0']['size'],old['size'])
 def test_flush_facade_passes_all_ids_and_categories(self):
  for category in ('storage','cabinet','table'):
   s=shell();s['objects']['cabinet_any_id']['category']=category
   self.assertEqual(errors(s),[])
 def test_actual_penetration_rejected_even_if_centre_is_clear(self):
  s=shell();s['objects']['cabinet_any_id'].update(center=[2,.08,1.25],size=[1.6,.20,2.5])
  self.assertTrue(any(v.kind=='wall_clash' for v in errors(s)))
 def test_measured_thick_wall_not_erased(self):
  self.assertTrue(any(v.kind=='wall_clash' for v in errors(shell(.24))))
 def test_outside_wall_span_does_not_intersect_infinite_plane(self):
  s=shell();s['objects']['cabinet_any_id']['center']=[5,-.03,1.25]
  self.assertFalse(any(v.kind=='wall_clash' for v in errors(s)))
 def test_rotating_scene_keeps_results(self):
  for angle in (23,100,-77):
   for shift,want in ((0,False),(-.03,True)):
    s=shell();s['objects']['cabinet_any_id']['center'][1]+=shift
    a=math.radians(angle)
    def xy(p):return [math.cos(a)*p[0]-math.sin(a)*p[1],math.sin(a)*p[0]+math.cos(a)*p[1]]
    for w in s['walls'].values():w['start']=xy(w['start']);w['end']=xy(w['end'])
    s['floor']['verts']=[xy(v)+[v[2]] for v in s['floor']['verts']]
    for o in s['objects'].values():o['center']=xy(o['center'])+[o['center'][2]];o['yaw']+=angle
    self.assertEqual(any(v.kind=='wall_clash' for v in errors(s)),want)
 def test_other_checks_still_reject_overlaps_and_sinking(self):
  s=shell();s['objects']['second']=copy.deepcopy(s['objects']['cabinet_any_id']);s['objects']['second']['center'][2]-=.2
  kinds={v.kind for v in errors(s)}
  self.assertIn('object_clash',kinds);self.assertIn('below_floor',kinds)

 def test_collision_metadata_cannot_whitelist_a_penetration(self):
  s=shell();s['objects']['cabinet_any_id']['center'][1]=-.03
  s['walls']['boundary']['solid_interval']=[-100,-99]
  s['objects']['cabinet_any_id']['embedded']=True
  self.assertTrue(any(v.kind=='wall_clash' for v in errors(s)))
 def test_invalid_wall_dimensions_cannot_pass(self):
  for t in (0,-.1,float('nan')):
   with self.assertRaises(ValueError):check(shell(t))

 def test_missing_optional_wall_thickness_uses_documented_plane_default(self):
  s=shell();del s['walls']['boundary']['thickness']
  self.assertEqual(errors(s),[])

if __name__=='__main__':unittest.main()
