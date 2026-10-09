"""Read-only capture diagnostics, encoded only on an authenticated hover request."""
import base64
import cv2
import numpy as np
from app.core.minimap_memory import MinimapMemory
from app.vision.game_viewport import game_viewport, minimap_region, hud_region_bbox


def encode_preview(frame, navigation, hud=None, inputs=None, profile=None, atlas=None):
    def encoded(image, ext):
        ok, buffer = cv2.imencode(ext, image, [cv2.IMWRITE_JPEG_QUALITY, 75] if ext == '.jpg' else [])
        if not ok:
            raise ValueError('화면 미리보기 변환 실패')
        mime = 'jpeg' if ext == '.jpg' else 'png'
        return f'data:image/{mime};base64,' + base64.b64encode(buffer).decode('ascii')

    # Never annotate or update the shared capture or the live movement memory.
    screen = frame.copy()
    h, w = screen.shape[:2]
    m = navigation['minimap']
    roi = MinimapMemory._crop(frame, navigation)
    result = {'minimap': None, 'mask': None, 'terrain_valid': False,
              'walkable_ratio': None, 'bbox': m.get('bbox'), 'resolution': [w, h],
              'viewport': list(game_viewport(frame))}
    if roi is not None:
        mask,valid,_,_,analysis_width=MinimapMemory._frame_terrain(frame,navigation,roi)
        roi=MinimapMemory._crop(frame,navigation,analysis_width)
        px, py = m['player']
        x, y = round(px*roi.shape[1]), round(py*roi.shape[0])
        marked = roi.copy()
        cv2.drawMarker(marked, (x, y), (60, 255, 80), cv2.MARKER_CROSS, 9, 1)
        pin_crop=MinimapMemory._pin_crop(frame,navigation,analysis_width)
        if pin_crop is not None and m.get('mapping',{}).get('pin_search_margin',0)>0:
            image,offset,player,expanded=pin_crop
            marked=image.copy()
            cv2.rectangle(marked,tuple(np.round(offset).astype(int)),tuple(np.round(offset+[roi.shape[1]-1,roi.shape[0]-1]).astype(int)),(80,240,240),1)
            cv2.drawMarker(marked,tuple(np.round(player).astype(int)),(60,255,80),cv2.MARKER_CROSS,9,1)
            result['pin_search_bbox_pixels']=list(expanded)
            cv2.rectangle(screen,expanded[:2],(expanded[2]-1,expanded[3]-1),(255,180,80),max(2,w//600))
        cell = m.get('mapping',{}).get('grid_cell_px',4)
        gx, gy = min(mask.shape[1]//cell-1, int(px*mask.shape[1]/cell)), min(mask.shape[0]//cell-1, int(py*mask.shape[0]/cell))
        player_walkable = float(mask[gy*cell:(gy+1)*cell,gx*cell:(gx+1)*cell].mean()) >= 160
        result.update(analysis_width=analysis_width,grid_cell_px=cell,minimap=encoded(marked, '.jpg'), mask=encoded(mask, '.png'),
                      player_walkable=player_walkable,
                      terrain_valid=bool(valid), walkable_ratio=round(float((mask > 0).mean()), 3))
        a, b, c, d = minimap_region(frame, m)
        cv2.rectangle(screen, (a, b), (c-1, d-1), (80, 240, 240), max(2, w//600))
    overlays=[]
    colors={'health':(70,80,255),'sp':(170,220,60),'mp':(255,140,90),'buffs':(80,230,160),'skills':(220,100,220)}
    labels={'health':'HP','sp':'SP','mp':'MP','buffs':'BUFF','skills':'SKILLS'}
    regions=(hud or {}).get('regions',{})
    def draw_region(key,box,source='configured'):
        if not box or len(box)!=4:return
        a,b,c,d=[round(float(v)*([w,h,w,h][i])/1000) for i,v in enumerate(box)]
        a,b,c,d=max(0,a),max(0,b),min(w,c),min(h,d)
        if c<=a or d<=b:return
        color=colors[key];cv2.rectangle(screen,(a,b),(c-1,d-1),color,max(2,w//600))
        cv2.putText(screen,labels[key],(a,max(14,b-5)),cv2.FONT_HERSHEY_SIMPLEX,max(.45,w/1800),color,2,cv2.LINE_AA)
        overlays.append({'kind':key,'label':labels[key],'bbox_pixels':[a,b,c,d],'source':source,'color':'#%02x%02x%02x'%(color[2],color[1],color[0])})
    for key in ('health','sp','mp','buffs','skills'):
        draw_region(key,hud_region_bbox(frame,regions.get(key,{})))
    if profile=='diablo4' and not regions.get('skills',{}).get('bbox'):
        a,b,c,d=game_viewport(frame)
        draw_region('skills',[(a+.385*(c-a))*1000/w,(b+.89*(d-b))*1000/h,(a+.615*(c-a))*1000/w,(b+.975*(d-b))*1000/h],'profile_default')
    for skill in (inputs or {}).get('attack_skills',[]):
        draw_region('skills',skill.get('visual_ready',{}).get('bbox'))
    result['atlas']=None
    if atlas and atlas.get('grid'):
        rows=atlas['grid'];grid=np.array([[int(cell) for cell in row] for row in rows],np.uint8)
        colors=np.array([(25,23,16),(120,152,172),(56,54,48),(88,121,71)],np.uint8)
        image=colors[grid];ah,aw=grid.shape
        preview_scale=min(.5,384/max(aw*atlas['resolution'],ah*atlas['resolution']))
        image=cv2.resize(image,(max(1,round(aw*atlas['resolution']*preview_scale)),max(1,round(ah*atlas['resolution']*preview_scale))),interpolation=cv2.INTER_NEAREST)
        def point(p):return round(p[0]*image.shape[1]),round(p[1]*image.shape[0])
        for start,end in atlas.get('trail',[]):cv2.line(image,point(start),point(end),(88,210,71),1)
        if atlas.get('player') is not None:cv2.circle(image,point(atlas['player']),4,(255,255,255),-1)
        if atlas.get('goal') is not None:
            cv2.drawMarker(image,point(atlas['goal']),(255,217,80),cv2.MARKER_CROSS,11,2)
            if atlas.get('player') is not None:cv2.line(image,point(atlas['player']),point(atlas['goal']),(255,217,80),1)
        result['atlas']=encoded(image,'.png')
        result['atlas_resolution']=atlas['resolution']
        result['atlas_scale']=preview_scale
    result['regions']=overlays
    if w > 960:
        screen = cv2.resize(screen, (960, max(1, round(h*960/w))), interpolation=cv2.INTER_AREA)
    result['screen'] = encoded(screen, '.jpg')
    return result
